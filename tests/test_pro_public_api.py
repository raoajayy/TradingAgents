"""P3-11: token-scoped public read-only API, rate limiting, webhooks."""

import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from tests.test_pro_pipeline_graph import (  # noqa: E402
    CONFIG,
    FakePipelineLLM,
    pipeline_snapshot,
)
from tradingagents.pro.alerting import AlertManager  # noqa: E402
from tradingagents.pro.dashboard.app import DashboardState, create_app  # noqa: E402
from tradingagents.pro.dashboard.prefs import PrefsStore  # noqa: E402
from tradingagents.pro.memory import ProMemory  # noqa: E402
from tradingagents.pro.store import EventStore  # noqa: E402
from tradingagents.pro.webhooks import (  # noqa: E402
    WebhookRegistry,
    sign_payload,
)

OPERATOR = {"X-API-Key": "secret-token"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _build(tmp_path, monkeypatch=None, rate: str | None = None):
    """Event-store-backed dashboard app with one recorded run + auth on."""
    if rate is not None:
        monkeypatch.setenv("PRO_PUBLIC_RATE_LIMIT", rate)
    store = EventStore(tmp_path / "pro.db")
    state = DashboardState(memory=ProMemory())
    state.prefs = PrefsStore(store=store)
    run = state.recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory)
    client = TestClient(create_app(state, api_token="secret-token"))
    return client, state, store, run


class _CaptureSink:
    def __init__(self):
        self.alerts = []

    def deliver(self, alert):
        self.alerts.append(alert)


class _CaptureTransport:
    """Records every delivery; scripted to fail while ``failures`` > 0."""

    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = []

    def __call__(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": body,
                           "headers": headers, "timeout": timeout})
        if self.failures > 0:
            self.failures -= 1
            raise OSError("connection refused")


class TestTokenLifecycle:
    def test_create_list_revoke(self, tmp_path):
        client, state, store, run = _build(tmp_path)

        created = client.post("/api/tokens", headers=OPERATOR, json={
            "label": "partner", "scopes": ["read:decisions"]})
        assert created.status_code == 200
        body = created.json()
        raw = body["token"]
        assert raw and len(raw) >= 32
        assert body["token_hash"] != raw  # only the hash is stored
        assert body["scopes"] == ["read:decisions"]

        listed = client.get("/api/tokens", headers=OPERATOR).json()["tokens"]
        assert len(listed) == 1
        assert listed[0]["token_hash"] == body["token_hash"]
        assert listed[0]["revoked_at"] is None
        assert "token" not in listed[0]  # the raw token never reappears

        # the live token reads decisions
        ok = client.get("/public/v1/decisions", headers=_bearer(raw))
        assert ok.status_code == 200

        revoked = client.delete(f"/api/tokens/{body['token_hash']}",
                                headers=OPERATOR)
        assert revoked.status_code == 200
        assert client.get("/api/tokens", headers=OPERATOR) \
            .json()["tokens"][0]["revoked_at"] is not None

        # revoked token -> 401
        denied = client.get("/public/v1/decisions", headers=_bearer(raw))
        assert denied.status_code == 401
        assert "revoked" in denied.json()["detail"]

        assert client.delete("/api/tokens/nope",
                             headers=OPERATOR).status_code == 404

    def test_creation_validation(self, tmp_path):
        client, *_ = _build(tmp_path)
        assert client.post("/api/tokens", headers=OPERATOR, json={
            "label": "", "scopes": ["read:decisions"]}).status_code == 422
        assert client.post("/api/tokens", headers=OPERATOR, json={
            "label": "x", "scopes": ["write:orders"]}).status_code == 422
        assert client.post("/api/tokens", headers=OPERATOR, json={
            "label": "x", "scopes": []}).status_code == 422

    def test_management_needs_operator_auth(self, tmp_path):
        client, *_ = _build(tmp_path)
        # no credentials at all: the /api middleware 401s
        assert client.get("/api/tokens").status_code == 401
        assert client.post("/api/tokens", json={}).status_code == 401

    def test_store_resolve_round_trip(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        created = store.create_api_token("t", "read:decisions,read:calibration")
        resolved = store.resolve_api_token(created["token"])
        assert resolved["token_hash"] == created["token_hash"]
        assert resolved["scopes"] == ["read:calibration", "read:decisions"]
        assert store.resolve_api_token("garbage") is None
        assert store.revoke_api_token(created["token_hash"]) is True
        assert store.resolve_api_token(created["token"]) is None
        store.close()


class TestScopesAndAuth:
    def test_scope_enforced_per_endpoint(self, tmp_path):
        client, state, store, run = _build(tmp_path)
        calib_only = store.create_api_token("c", ["read:calibration"])["token"]

        denied = client.get("/public/v1/decisions", headers=_bearer(calib_only))
        assert denied.status_code == 403
        assert "read:decisions" in denied.json()["detail"]
        assert client.get(f"/public/v1/decisions/{run.run_id}",
                          headers=_bearer(calib_only)).status_code == 403
        assert client.get("/public/v1/calibration",
                          headers=_bearer(calib_only)).status_code == 200

        decisions_only = store.create_api_token(
            "d", ["read:decisions"])["token"]
        assert client.get("/public/v1/decisions",
                          headers=_bearer(decisions_only)).status_code == 200
        assert client.get("/public/v1/calibration",
                          headers=_bearer(decisions_only)).status_code == 403

    def test_public_requires_bearer_never_session(self, tmp_path):
        client, *_ = _build(tmp_path)
        # no Authorization header -> 401 with a how-to detail
        resp = client.get("/public/v1/decisions")
        assert resp.status_code == 401
        assert "Bearer" in resp.json()["detail"]
        # the dashboard operator credential is NOT a public-API credential
        assert client.get("/public/v1/decisions",
                          headers=OPERATOR).status_code == 401
        assert client.get("/public/v1/decisions",
                          headers=_bearer("garbage")).status_code == 401
        # unknown /public paths 404 instead of serving the SPA shell
        assert client.get("/public/v1/nope").status_code == 404

    def test_public_requires_token_even_in_open_dev_mode(self, tmp_path):
        # no PRO_DASHBOARD_TOKEN: /api is open, /public/v1 still is NOT
        store = EventStore(tmp_path / "pro.db")
        state = DashboardState(memory=ProMemory())
        state.prefs = PrefsStore(store=store)
        client = TestClient(create_app(state))  # auth disabled
        assert client.get("/api/runs").status_code == 200
        assert client.get("/public/v1/decisions").status_code == 401

    def test_503_without_event_store(self):
        state = DashboardState(memory=ProMemory())  # file-backed prefs
        client = TestClient(create_app(state, api_token="secret-token"))
        assert client.get("/public/v1/decisions",
                          headers=_bearer("x")).status_code == 503
        assert client.get("/api/tokens", headers=OPERATOR).status_code == 503


class TestPublicPayloads:
    def test_decisions_list_shape_excludes_transcripts(self, tmp_path):
        client, state, store, run = _build(tmp_path)
        token = store.create_api_token("d", ["read:decisions"])["token"]

        body = client.get("/public/v1/decisions",
                          headers=_bearer(token)).json()
        assert len(body["decisions"]) == 1
        row = body["decisions"][0]
        assert set(row) == {"run_id", "symbol", "started_at", "timeframe",
                            "action", "rejected_at", "confidence", "versions"}
        assert row["run_id"] == run.run_id
        assert row["symbol"] == "XAUUSD"
        assert row["action"] == "BUY"
        assert row["rejected_at"] is None
        assert row["confidence"] == 72
        assert set(row["versions"]) == {"git_sha", "prompt_hash",
                                        "model_ids", "config_hash"}
        # the audit surfaces stay operator-only
        text = json.dumps(body)
        for private in ("transcript", "evidence", "debate", "speaker"):
            assert private not in text

    def test_decision_detail_adds_gates_and_rejection(self, tmp_path):
        client, state, store, run = _build(tmp_path)
        token = store.create_api_token("d", ["read:decisions"])["token"]

        row = client.get(f"/public/v1/decisions/{run.run_id}",
                         headers=_bearer(token)).json()
        assert row["run_id"] == run.run_id
        assert row["rejection"] is None  # accepted run
        # gates summary: verdict + reasons only, straight from gate_results
        assert row["gates"]["risk"]["passed"] is True
        assert row["gates"]["risk"]["reasons"] == []
        assert row["gates"]["critic"]["passed"] is True
        # never the debate record
        text = json.dumps(row)
        for private in ("transcript", "evidence_by_team", "speaker"):
            assert private not in text

        missing = client.get("/public/v1/decisions/nope",
                             headers=_bearer(token))
        assert missing.status_code == 404

    def test_calibration_buckets_and_brier(self, tmp_path):
        client, state, store, run = _build(tmp_path)
        token = store.create_api_token("c", ["read:calibration"])["token"]

        # score some outcomes: confidence 72 -> the 60-80 bucket
        for pnl in (1.0, 1.0, -1.0):
            trade = state.memory.record_trade(run.recommendation)
            state.memory.close_trade(trade.id, pnl=pnl, write_lesson=False)

        body = client.get("/public/v1/calibration",
                          headers=_bearer(token)).json()
        assert body["n"] == 3
        assert body["brier"] is not None
        buckets = {(b["confidence_lo"], b["confidence_hi"]): b
                   for b in body["buckets"]}
        assert buckets[(60, 80)]["n"] == 3
        assert buckets[(60, 80)]["p_win"] == pytest.approx(2 / 3)
        assert buckets[(0, 20)]["n"] == 0
        assert buckets[(0, 20)]["p_win"] is None  # never invented


class TestRateLimit:
    def test_429_with_retry_after(self, tmp_path, monkeypatch):
        client, state, store, run = _build(tmp_path, monkeypatch, rate="3")
        token = store.create_api_token("d", ["read:decisions"])["token"]

        for _ in range(3):
            assert client.get("/public/v1/decisions",
                              headers=_bearer(token)).status_code == 200
        limited = client.get("/public/v1/decisions", headers=_bearer(token))
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) >= 1
        assert "rate limit" in limited.json()["detail"]

    def test_buckets_are_per_token(self, tmp_path, monkeypatch):
        client, state, store, run = _build(tmp_path, monkeypatch, rate="2")
        first = store.create_api_token("a", ["read:decisions"])["token"]
        second = store.create_api_token("b", ["read:decisions"])["token"]
        for _ in range(2):
            assert client.get("/public/v1/decisions",
                              headers=_bearer(first)).status_code == 200
        assert client.get("/public/v1/decisions",
                          headers=_bearer(first)).status_code == 429
        # a different token has its own bucket
        assert client.get("/public/v1/decisions",
                          headers=_bearer(second)).status_code == 200


class TestWebhookRegistry:
    def test_fires_with_valid_hmac_signature(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        transport = _CaptureTransport()
        registry = WebhookRegistry(store, transport=transport)
        registry.add("https://example.com/hook", "run_complete", "s3cret")

        payload = {"event": "run_complete", "run_id": "r1"}
        registry.dispatch("run_complete", payload)

        assert len(transport.calls) == 1
        call = transport.calls[0]
        assert call["url"] == "https://example.com/hook"
        assert call["timeout"] == 5.0
        assert json.loads(call["body"]) == payload
        # receiver-side verification: recompute the HMAC over the body
        assert call["headers"]["X-Pro-Signature"] == sign_payload(
            "s3cret", call["body"])
        assert call["headers"]["X-Pro-Signature"].startswith("sha256=")
        store.close()

    def test_registration_validation_and_secret_privacy(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        registry = WebhookRegistry(store)
        with pytest.raises(ValueError):
            registry.add("ftp://x", "run_complete", "s")
        with pytest.raises(ValueError):
            registry.add("https://x.com", "nope", "s")
        with pytest.raises(ValueError):
            registry.add("https://x.com", "run_complete", "")
        created = registry.add("https://x.com/h", "run_complete", "s")
        assert "secret" not in created
        assert all("secret" not in h for h in registry.list())
        assert registry.delete(created["id"]) is True
        assert registry.delete(created["id"]) is False
        store.close()

    def test_three_strikes_disable_with_alert(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        sink = _CaptureSink()
        transport = _CaptureTransport(failures=99)
        registry = WebhookRegistry(store, alerts=AlertManager(sinks=[sink]),
                                   transport=transport, retry_delay=0)
        registry.add("https://down.example.com/hook", "run_complete", "s")

        for _ in range(3):
            registry.dispatch("run_complete", {"event": "run_complete"})
        # each dispatch = original attempt + one in-dispatch retry
        assert len(transport.calls) == 6

        hook = registry.list()[0]
        assert hook["failures"] == 3
        assert hook["disabled"] is True
        disabled_alerts = [a for a in sink.alerts
                           if a.event == "webhook_disabled"]
        assert len(disabled_alerts) == 1
        assert "down.example.com" in disabled_alerts[0].text

        # disabled hooks receive nothing further
        registry.dispatch("run_complete", {"event": "run_complete"})
        assert len(transport.calls) == 6
        store.close()

    def test_success_resets_the_strike_count(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        # four scripted transport failures = two fully failed deliveries
        # (each dispatch burns the attempt AND its retry)
        transport = _CaptureTransport(failures=4)
        registry = WebhookRegistry(store, transport=transport, retry_delay=0)
        registry.add("https://flaky.example.com/hook", "run_complete", "s")

        registry.dispatch("run_complete", {})  # attempt+retry fail (1)
        registry.dispatch("run_complete", {})  # attempt+retry fail (2)
        registry.dispatch("run_complete", {})  # success -> reset
        hook = registry.list()[0]
        assert hook["failures"] == 0 and hook["disabled"] is False
        store.close()

    def test_retry_masks_a_single_transient_failure(self, tmp_path):
        # one connection blip: the in-dispatch retry delivers, so the
        # registration accrues NO strike
        store = EventStore(tmp_path / "pro.db")
        transport = _CaptureTransport(failures=1)
        registry = WebhookRegistry(store, transport=transport, retry_delay=0)
        registry.add("https://blip.example.com/hook", "run_complete", "s")

        registry.dispatch("run_complete", {"event": "run_complete"})
        assert len(transport.calls) == 2  # attempt + successful retry
        hook = registry.list()[0]
        assert hook["failures"] == 0 and hook["disabled"] is False
        store.close()

    def test_two_hard_failures_count_once_each(self, tmp_path):
        # a delivery whose retry ALSO fails is exactly one strike — the
        # retry must never double-count a hard-down receiver
        store = EventStore(tmp_path / "pro.db")
        transport = _CaptureTransport(failures=99)
        registry = WebhookRegistry(store, transport=transport, retry_delay=0)
        registry.add("https://down.example.com/hook", "run_complete", "s")

        registry.dispatch("run_complete", {})
        registry.dispatch("run_complete", {})
        assert len(transport.calls) == 4  # 2 dispatches x (attempt + retry)
        hook = registry.list()[0]
        assert hook["failures"] == 2
        assert hook["disabled"] is False  # third strike hasn't happened
        store.close()

    def test_enable_resets_strikes_and_resumes_delivery(self, tmp_path):
        store = EventStore(tmp_path / "pro.db")
        transport = _CaptureTransport(failures=99)
        registry = WebhookRegistry(store, transport=transport, retry_delay=0)
        created = registry.add("https://down.example.com/hook",
                               "run_complete", "s")
        for _ in range(3):
            registry.dispatch("run_complete", {})
        assert registry.list()[0]["disabled"] is True
        n_calls = len(transport.calls)

        assert registry.enable(created["id"]) is True
        hook = registry.list()[0]
        assert hook["failures"] == 0 and hook["disabled"] is False
        assert registry.has_active("run_complete") is True

        # deliveries resume (receiver healthy again)
        transport.failures = 0
        registry.dispatch("run_complete", {"event": "run_complete"})
        assert len(transport.calls) == n_calls + 1
        assert registry.list()[0]["failures"] == 0

        assert registry.enable("no-such-id") is False
        store.close()


class TestWebhookEndpoints:
    def test_operator_crud(self, tmp_path):
        client, state, store, run = _build(tmp_path)
        created = client.post("/api/webhooks", headers=OPERATOR, json={
            "url": "https://example.com/hook", "event": "run_complete",
            "secret": "s3cret"})
        assert created.status_code == 200
        hook = created.json()
        assert hook["url"] == "https://example.com/hook"
        assert "secret" not in hook

        listed = client.get("/api/webhooks",
                            headers=OPERATOR).json()["webhooks"]
        assert [h["id"] for h in listed] == [hook["id"]]
        assert all("secret" not in h for h in listed)

        assert client.post("/api/webhooks", headers=OPERATOR, json={
            "url": "nope", "secret": "s"}).status_code == 422
        assert client.delete(f"/api/webhooks/{hook['id']}",
                             headers=OPERATOR).status_code == 200
        assert client.delete(f"/api/webhooks/{hook['id']}",
                             headers=OPERATOR).status_code == 404
        # unauthenticated management is refused by the /api middleware
        assert client.get("/api/webhooks").status_code == 401

    def test_enable_endpoint_rearms_a_disabled_hook(self, tmp_path):
        from tradingagents.pro.webhooks import WebhookRegistry

        client, state, store, run = _build(tmp_path)
        hook = client.post("/api/webhooks", headers=OPERATOR, json={
            "url": "https://example.com/hook", "event": "run_complete",
            "secret": "s3cret"}).json()
        # trip the three-strikes auto-disable through the same kv document
        registry = WebhookRegistry(store,
                                   transport=_CaptureTransport(failures=99),
                                   retry_delay=0)
        for _ in range(3):
            registry.dispatch("run_complete", {})
        listed = client.get("/api/webhooks",
                            headers=OPERATOR).json()["webhooks"]
        assert listed[0]["disabled"] is True and listed[0]["failures"] == 3

        enabled = client.post(f"/api/webhooks/{hook['id']}/enable",
                              headers=OPERATOR)
        assert enabled.status_code == 200
        assert enabled.json() == {"enabled": hook["id"]}
        listed = client.get("/api/webhooks",
                            headers=OPERATOR).json()["webhooks"]
        assert listed[0]["disabled"] is False and listed[0]["failures"] == 0

        assert client.post("/api/webhooks/no-such-id/enable",
                           headers=OPERATOR).status_code == 404
        # unauthenticated enable is refused by the /api middleware
        assert client.post(
            f"/api/webhooks/{hook['id']}/enable").status_code == 401


class TestRunCompleteIntegration:
    def test_service_fires_webhook_after_recorded_run(self, tmp_path):
        from tests.test_pro_e2e_service import make_service

        store = EventStore(tmp_path / "pro.db")
        service = make_service([130.0])
        service.dashboard.prefs = PrefsStore(store=store)
        transport = _CaptureTransport()
        # injectable-fakes pattern: pre-wire the registry with a capture
        # transport; registrations still live in the event store's kv
        service.webhooks = WebhookRegistry(store, alerts=service.alerts,
                                           transport=transport)
        service.webhooks.add("https://example.com/hook", "run_complete",
                             "s3cret")

        summary = service.run_once()
        assert summary["run_id"]

        # dispatch runs on a daemon thread — poll like the trigger tests
        for _ in range(100):
            if transport.calls:
                break
            time.sleep(0.02)
        assert len(transport.calls) == 1
        call = transport.calls[0]
        payload = json.loads(call["body"])
        assert payload["event"] == "run_complete"
        assert payload["run_id"] == summary["run_id"]
        assert payload["symbol"] == "XAUUSD"
        assert payload["action"] == "BUY"
        assert payload["started_at"]
        assert set(payload["versions"]) == {"git_sha", "prompt_hash",
                                            "model_ids", "config_hash"}
        assert call["headers"]["X-Pro-Signature"] == sign_payload(
            "s3cret", call["body"])
        store.close()

    def test_no_registrations_means_no_thread_no_calls(self, tmp_path):
        from tests.test_pro_e2e_service import make_service

        store = EventStore(tmp_path / "pro.db")
        service = make_service([130.0])
        service.dashboard.prefs = PrefsStore(store=store)
        transport = _CaptureTransport()
        service.webhooks = WebhookRegistry(store, transport=transport)
        service.run_once()
        time.sleep(0.05)
        assert transport.calls == []
        store.close()
