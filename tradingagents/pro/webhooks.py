"""P3-11 outbound webhooks: push ``run_complete`` to registered URLs.

Registrations are operator-managed (POST/GET/DELETE /api/webhooks) and
live as one JSON document in the event store's kv table (key
``webhooks``) — the same durability story as prefs. Each registration is
{id, url, event, secret, created_at, failures, disabled}.

Delivery contract:
- body: compact JSON of the event payload
- signature: ``X-Pro-Signature: sha256=<hex hmac-sha256(body, secret)>``
  so the receiver can verify both origin and integrity
- 5s timeout; failures are logged, counted, and NEVER raised into the
  trading loop
- one in-dispatch retry after a short backoff (2s) masks a transient
  receiver blip; only a delivery that fails BOTH attempts counts as one
  failure
- 3 consecutive failures disable the registration and emit a warning
  alert (``webhook_disabled``); a successful delivery resets the count
- an operator re-arms a disabled registration via ``enable`` (POST
  /api/webhooks/{id}/enable), which clears the strike count

The transport is injectable (tests capture deliveries without sockets);
the default is stdlib urllib — no new dependency, mirroring
WebhookAlertSink.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import urllib.request
import uuid

from tradingagents.contracts import utc_now

logger = logging.getLogger(__name__)

WEBHOOKS_KV_KEY = "webhooks"
SUPPORTED_EVENTS = ("run_complete",)
MAX_CONSECUTIVE_FAILURES = 3
RETRY_BACKOFF_SECONDS = 2.0


def _default_transport(url: str, body: bytes, headers: dict,
                       timeout: float) -> None:
    request = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
    urllib.request.urlopen(request, timeout=timeout).close()


def sign_payload(secret: str, body: bytes) -> str:
    """The exact signature header value receivers must recompute."""
    digest = hmac.new(secret.encode(), body, "sha256").hexdigest()
    return f"sha256={digest}"


class WebhookRegistry:
    """kv-backed registration store + best-effort dispatcher.

    ``store`` is the P2-01 EventStore (get_kv/put_kv). The registry lock
    guards read-modify-write on the kv document across threads (the
    EventStore lock only serializes individual statements).
    """

    def __init__(self, store, alerts=None, transport=None,
                 timeout: float = 5.0,
                 retry_delay: float = RETRY_BACKOFF_SECONDS):
        self._store = store
        self._alerts = alerts
        self._transport = transport or _default_transport
        self._timeout = timeout
        # backoff before the single in-dispatch retry; injectable so tests
        # never sleep for real
        self._retry_delay = retry_delay
        self._lock = threading.Lock()

    # --- persistence -----------------------------------------------------

    def _load(self) -> list[dict]:
        raw = self._store.get_kv(WEBHOOKS_KV_KEY)
        if not raw:
            return []
        try:
            hooks = json.loads(raw)
            return hooks if isinstance(hooks, list) else []
        except ValueError:
            logger.warning("corrupt webhooks document; starting empty")
            return []

    def _save(self, hooks: list[dict]) -> None:
        self._store.put_kv(WEBHOOKS_KV_KEY, json.dumps(hooks))

    @staticmethod
    def _public_view(hook: dict) -> dict:
        """A registration as endpoints return it — the secret never leaves
        the store (mirrors the api-token hash-only discipline)."""
        return {k: v for k, v in hook.items() if k != "secret"}

    # --- management (operator endpoints) ----------------------------------

    def add(self, url: str, event: str, secret: str) -> dict:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        if event not in SUPPORTED_EVENTS:
            raise ValueError(
                f"event must be one of {list(SUPPORTED_EVENTS)}")
        if not (secret or "").strip():
            raise ValueError("secret is required (signs every delivery)")
        hook = {
            "id": str(uuid.uuid4()),
            "url": url,
            "event": event,
            "secret": secret,
            "created_at": utc_now().isoformat(),
            "failures": 0,
            "disabled": False,
        }
        with self._lock:
            hooks = self._load()
            hooks.append(hook)
            self._save(hooks)
        return self._public_view(hook)

    def list(self) -> list[dict]:
        with self._lock:
            return [self._public_view(h) for h in self._load()]

    def delete(self, hook_id: str) -> bool:
        with self._lock:
            hooks = self._load()
            kept = [h for h in hooks if h.get("id") != hook_id]
            if len(kept) == len(hooks):
                return False
            self._save(kept)
        return True

    def enable(self, hook_id: str) -> bool:
        """Re-arm a registration: clear the strike count and the disabled
        flag (the operator's answer to a three-strikes ``webhook_disabled``
        alert — no more delete-and-re-register with a fresh secret). False
        when no such registration exists."""
        found = False
        with self._lock:
            hooks = self._load()
            for hook in hooks:
                if hook.get("id") == hook_id:
                    hook["failures"] = 0
                    hook["disabled"] = False
                    found = True
            if found:
                self._save(hooks)
        return found

    def has_active(self, event: str) -> bool:
        with self._lock:
            return any(h.get("event") == event and not h.get("disabled")
                       for h in self._load())

    # --- dispatch ----------------------------------------------------------

    def dispatch(self, event: str, payload: dict) -> None:
        """POST ``payload`` to every active registration for ``event``.
        Never raises — a broken receiver must not touch the loop."""
        try:
            body = json.dumps(payload, separators=(",", ":")).encode()
            with self._lock:
                targets = [dict(h) for h in self._load()
                           if h.get("event") == event
                           and not h.get("disabled")]
            for hook in targets:
                headers = {
                    "Content-Type": "application/json",
                    "X-Pro-Signature": sign_payload(hook["secret"], body),
                }
                if self._deliver(hook["url"], body, headers):
                    self._record_success(hook["id"])
                else:
                    self._record_failure(hook["id"])
        except Exception:  # noqa: BLE001 — belt-and-braces: never raise
            logger.exception("webhook dispatch failed")

    def _deliver(self, url: str, body: bytes, headers: dict) -> bool:
        """One delivery = up to two transport attempts with a short backoff
        between them, so a single transient receiver blip (cold start,
        connection reset) never accrues a strike. True on any success."""
        for attempt in (1, 2):
            try:
                self._transport(url, body, headers, self._timeout)
            except Exception:
                logger.warning("webhook delivery to %s failed (attempt %d)",
                               url, attempt, exc_info=True)
                if attempt == 1 and self._retry_delay > 0:
                    time.sleep(self._retry_delay)
            else:
                return True
        return False

    def _record_success(self, hook_id: str) -> None:
        with self._lock:
            hooks = self._load()
            for hook in hooks:
                if hook.get("id") == hook_id:
                    hook["failures"] = 0
            self._save(hooks)

    def _record_failure(self, hook_id: str) -> None:
        disabled_url = None
        with self._lock:
            hooks = self._load()
            for hook in hooks:
                if hook.get("id") != hook_id:
                    continue
                hook["failures"] = int(hook.get("failures", 0)) + 1
                if (hook["failures"] >= MAX_CONSECUTIVE_FAILURES
                        and not hook.get("disabled")):
                    hook["disabled"] = True
                    disabled_url = hook["url"]
            self._save(hooks)
        if disabled_url is not None and self._alerts is not None:
            try:
                self._alerts.emit(
                    "warning", "webhook_disabled",
                    f"webhook {disabled_url} disabled after "
                    f"{MAX_CONSECUTIVE_FAILURES} consecutive delivery "
                    "failures; POST /api/webhooks/{id}/enable to re-enable",
                )
            except Exception:
                logger.exception("webhook_disabled alert not delivered")
