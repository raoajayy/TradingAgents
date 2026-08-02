"""FastAPI application for the Pro dashboard.

Requires the ``dashboard`` extra (``pip install "tradingagents[dashboard]"``).
The app is a thin shell: every endpoint delegates to the tested view-model
functions in service.py; the SPA (or the legacy single page) renders them.

Auth model: all ``/api/*`` routes require ``X-API-Key`` when a token is
configured. ``POST /api/session`` exchanges the key for an HttpOnly cookie
so browser-native transports that cannot set headers (EventSource,
``<a download>``) still authenticate. Static shell and ``/healthz`` are
open — they contain no data.

Google sign-in (optional): when ``PRO_FIREBASE_PROJECT_ID`` and a non-empty
``PRO_ALLOWED_EMAILS`` allowlist are both set, ``POST /api/session`` also
accepts ``Authorization: Bearer <firebase-id-token>`` — verified against
Google's public certs via google-auth (already a dependency), then gated on
``email_verified`` + the allowlist — and mints the same session cookie.
Fail closed: a project id without an allowlist keeps Google sign-in
disabled. ``GET /api/auth/config`` (open) tells the SPA which login UI to
render; ``PRO_FIREBASE_WEB_CONFIG`` carries the public Firebase web config.

Roles + entitlements (P3-05): every session JWT carries a ``role`` claim,
``viewer`` or ``operator``. Google identities resolve their role from the
event store's ``users`` table (boot-seeded from PRO_ALLOWED_EMAILS as
operators when the table is empty — the pre-roles world was
single-operator). ``X-API-Key`` remains full operator by design: it is the
single deployment-level operator token (one secret, one holder — there is
no second identity to demote), and demoting it would brick the CLI/curl
admin path that predates Google sign-in. Mutating verbs (POST/PUT/DELETE)
under ``/api`` require the operator role — viewers get a 403 with a clear
detail; every GET stays viewer-readable. Mutations re-resolve the role
from the users table on every request (a demotion bites immediately);
reads trust the signed JWT claim until the session re-establishes — an
accepted, documented lag (docs/CONTROLS.md §1). Per-user preference isolation:
Google identities read/write ``dashboard_prefs:<email>`` kv documents;
token auth keeps the legacy shared ``dashboard_prefs`` document.

Public API (P3-11): ``/public/v1/*`` sits OUTSIDE the ``/api`` session
middleware but always requires ``Authorization: Bearer <api-token>`` —
tokens live hashed (sha256) in the event store's ``api_tokens`` table
with csv scopes (``read:decisions``, ``read:calibration``) and are
operator-managed via POST/GET/DELETE ``/api/tokens`` (the raw token is
returned once, at creation; an optional ``expires_days`` sets an
``expires_at`` after which the token 401s "token expired").
Requests are rate-limited per token by an
in-process token bucket (``PRO_PUBLIC_RATE_LIMIT`` req/min, default 60;
honest under the deployment's max-instances=1 invariant). Webhook
registrations (``/api/webhooks``, operator-only) fire signed
``run_complete`` POSTs from the service loop — see
``tradingagents.pro.webhooks``.

Public track record (P4-02): ``/public/v1/track-record`` (scope
``read:decisions``) serves the pre-registered decisions ledger + honest
aggregates for the public track-record page. It is feature-flagged and
DEFAULT OFF pending legal counsel: the route 404s unless
``PRO_PUBLIC_TRACK_RECORD=1`` is set at startup.

Run locally:
    uvicorn --factory tradingagents.pro.dashboard.app:create_default_app
"""

# NOTE: no `from __future__ import annotations` here — FastAPI resolves
# endpoint annotations at runtime against module globals, and Request/
# Response are imported lazily inside create_app (fastapi is an optional
# extra). Deferred annotations would demote them to query params.
import logging
from dataclasses import dataclass, field
from importlib import resources

from tradingagents.pro.backtest import BacktestResult
from tradingagents.pro.dashboard import marketdata as md, service
from tradingagents.pro.dashboard.backtest_store import BacktestRunStore
from tradingagents.pro.dashboard.events import EventBroadcaster
from tradingagents.pro.dashboard.intel import IntelService
from tradingagents.pro.dashboard.prefs import PrefsStore, default_data_dir
from tradingagents.pro.dashboard.recorder import PipelineRecorder, RunRecord
from tradingagents.pro.dashboard.ticker import TickCache
from tradingagents.pro.memory import ProMemory

# "__session" is the ONLY cookie Firebase Hosting's CDN forwards to a
# Cloud Run backend — any other name is silently stripped from requests,
# which turns every cookie-authenticated call into a 401 behind Hosting
# (observed live: successful Google sign-in bounced straight back to the
# login screen). Plain deployments don't care what it's called.
SESSION_COOKIE = "__session"

logger = logging.getLogger(__name__)

# In-band marker for a stream that died AFTER bytes were sent (no status
# code left to use). The client watches for this prefix and retries the
# question against the structured /ask endpoint rather than rendering the
# note as the model's answer.
STREAM_INTERRUPTED = "[stream interrupted:"


def _verify_firebase_token(id_token: str, audience: str) -> dict:
    """Verify a Firebase ID token and return its claims. Module-level so
    tests can monkeypatch it (the repo's injectable-fakes pattern); raises
    ValueError on any invalid/expired/wrong-audience token."""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    return google_id_token.verify_firebase_token(
        id_token, google_requests.Request(), audience=audience)


@dataclass
class DashboardState:
    recorder: PipelineRecorder = field(default_factory=PipelineRecorder)
    memory: ProMemory = field(default_factory=ProMemory)
    backtest: BacktestResult | None = None
    backtest_runs: BacktestRunStore = field(default_factory=BacktestRunStore)
    backtest_optimizations: BacktestRunStore = field(
        default_factory=lambda: BacktestRunStore(
            default_data_dir() / "backtest_optimizations.json"))
    backtest_bakeoffs: BacktestRunStore = field(
        default_factory=lambda: BacktestRunStore(
            default_data_dir() / "backtest_bakeoffs.json"))
    backtest_job = None      # BacktestJob, while an interactive run is in flight
    backtest_opt_job = None  # BacktestJob, while an optimization is in flight
    backtest_bakeoff_job = None  # BacktestJob, while a bake-off is in flight
    monte_carlo = None
    router = None            # ExecutionRouter, when attached to live/paper loop
    equity: float | None = None
    broadcaster: EventBroadcaster = field(default_factory=EventBroadcaster)
    marketdata: md.MarketDataService = field(default_factory=md.MarketDataService)
    ticks: TickCache | None = None  # set in __post_init__
    prefs: PrefsStore = field(default_factory=PrefsStore)
    intel: IntelService = field(default_factory=IntelService)
    trigger = None            # PipelineTrigger, when a service loop is attached
    metrics = None            # MetricsRegistry, when a service loop is attached
    arming = None             # ArmingStore, when live wiring is present
    alerts = None             # AlertManager, when a service loop is attached

    @property
    def runs(self) -> list[RunRecord]:
        return self.recorder.runs

    def latest_run(self) -> RunRecord | None:
        return self.runs[-1] if self.runs else None

    def latest_run_for(self, symbol: str) -> RunRecord | None:
        """Newest run for one symbol — the per-symbol decision board
        (trader review G1) must not lose a gold ticket because a BTC
        run happened afterwards."""
        for run in reversed(self.runs):
            if run.symbol == symbol:
                return run
        return None


def create_app(state: DashboardState | None = None, api_token: str | None = None):
    """``api_token`` (or env PRO_DASHBOARD_TOKEN) enables auth on /api/*.
    Unset = open, for localhost dev only (SEC-01) — deployment templates
    set the token and bind loopback."""
    import asyncio
    import hmac
    import os
    import secrets
    from contextlib import asynccontextmanager

    from fastapi import FastAPI, HTTPException, Request, Response
    from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

    state = state or DashboardState()
    token = api_token if api_token is not None else os.environ.get("PRO_DASHBOARD_TOKEN")

    # Sessions are STATELESS, cookie-based JWTs (HS256, stdlib — the repo
    # avoids new dependencies): header.payload.signature signed with a key
    # derived from the API token, so a session survives process restarts,
    # redeploys, and Cloud Run scale-to-zero, and the payload carries the
    # signed identity (`sub`) across reloads. Rotating PRO_DASHBOARD_TOKEN
    # invalidates every outstanding session.
    SESSION_TTL_SECONDS = 7 * 24 * 3600
    _jwt_key = (hmac.new(token.encode(), b"pro-session-jwt", "sha256").digest()
                if token else b"")

    def _b64url(data: bytes) -> str:
        import base64 as _base64

        return _base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    def _b64url_decode(text: str) -> bytes:
        import base64 as _base64

        return _base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

    def _mint_session(identity: "str | None" = None,
                      role: str = "operator") -> str:
        import json as _json
        import time as _time

        now = int(_time.time())
        header = _b64url(_json.dumps(
            {"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
        # P3-05: the role rides in the signed payload so READ authorization
        # stays stateless (no per-request users-table read). Mutating verbs
        # re-resolve the role from the users table per request (see the
        # middleware), so the cached claim only ever affects reads; a read
        # under a stale claim lags until the next session re-establish.
        payload = _b64url(_json.dumps(
            {"iss": "tradingagents-pro", "sub": identity or "api-token",
             "role": role, "iat": now, "exp": now + SESSION_TTL_SECONDS},
            separators=(",", ":")).encode())
        signing_input = f"{header}.{payload}"
        signature = _b64url(hmac.new(_jwt_key, signing_input.encode(),
                                     "sha256").digest())
        return f"{signing_input}.{signature}"

    def _session_claims(cookie: str) -> "dict | None":
        """Verify the JWT cookie; returns its claims or None."""
        import json as _json
        import time as _time

        parts = cookie.split(".")
        if len(parts) != 3:
            return None
        signing_input = f"{parts[0]}.{parts[1]}"
        expected = _b64url(hmac.new(_jwt_key, signing_input.encode(),
                                    "sha256").digest())
        if not hmac.compare_digest(parts[2], expected):
            return None
        try:
            claims = _json.loads(_b64url_decode(parts[1]))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(claims.get("exp"), int) or claims["exp"] <= _time.time():
            return None
        return claims

    def _session_valid(cookie: str) -> bool:
        return _session_claims(cookie) is not None

    # Google sign-in (optional). Fail closed: BOTH the project id (token
    # audience) and a non-empty email allowlist are required — a project id
    # alone would admit any Google account on earth.
    firebase_project = os.environ.get("PRO_FIREBASE_PROJECT_ID", "").strip()
    allowed_emails = {
        email.strip().lower()
        for email in os.environ.get("PRO_ALLOWED_EMAILS", "").split(",")
        if email.strip()
    }
    google_enabled = bool(firebase_project and allowed_emails)

    # P3-05 users + roles. The users table lives in the P2-01 event store;
    # the dashboard reaches it through the PrefsStore the service wired
    # (state.prefs.store is the EventStore, or None for the legacy
    # file-backed prefs used by tests/dev). Seeding mirrors migrate_legacy:
    # guarded on an empty table, idempotent, allowlist entries = operators.
    users_store = getattr(state.prefs, "store", None)
    if users_store is not None and allowed_emails:
        from tradingagents.pro.store import seed_users

        seed_users(users_store, allowed_emails)

    def _role_for(email: str) -> str:
        """Users-table role for a signed-in (already allowlisted) email.
        An allowlisted email absent from the table (added to
        PRO_ALLOWED_EMAILS after the seed, or no event store attached)
        defaults to operator — the allowlist remains the single-operator
        admission source until the table says otherwise."""
        if users_store is not None:
            role = users_store.get_user_role(email)
            if role is not None:
                return role
        return "operator"

    def _request_identity(request: Request) -> "str | None":
        """The verified per-user identity of a request, or None for the
        deployment-level paths (API key, no-auth dev mode, legacy
        pre-role cookies minted for the token)."""
        if not token:
            return None
        if hmac.compare_digest(request.headers.get("x-api-key", ""), token):
            return None
        claims = _session_claims(request.cookies.get(SESSION_COOKIE, ""))
        if claims is None:
            return None
        sub = claims.get("sub")
        return None if sub in (None, "api-token") else str(sub)

    def _request_role(request: Request, fresh: bool = False) -> str:
        """Role of an already-authenticated request. X-API-Key is full
        operator by design (see module docstring: it is the single
        deployment-level operator secret). Cookies carry the signed role
        claim; pre-P3-05 cookies (no role claim) re-resolve from the
        users table so an upgrade never silently promotes anyone.

        ``fresh=True`` (the mutation path) ignores the cached role claim
        for per-user sessions and re-resolves from the users table on
        THIS request, so a demoted operator loses mutation rights
        immediately — not after the 7-day JWT finally expires. Reads keep
        the fast stateless-claim path; that read-path lag is a documented
        accepted risk (docs/CONTROLS.md §1)."""
        if not token:
            return "operator"  # open dev mode: no identities exist
        if hmac.compare_digest(request.headers.get("x-api-key", ""), token):
            return "operator"
        claims = _session_claims(request.cookies.get(SESSION_COOKIE, ""))
        if claims is None:
            return "viewer"  # unauthenticated: fail closed (middleware 401s first)
        sub = claims.get("sub")
        if fresh and sub not in (None, "api-token"):
            return _role_for(str(sub))
        role = claims.get("role")
        if role in ("viewer", "operator"):
            return str(role)
        if sub in (None, "api-token"):
            return "operator"
        return _role_for(str(sub))

    # Direct SSE (optional): Firebase Hosting's proxy buffers responses and
    # cannot carry Server-Sent Events (observed live: /api/stream → 503
    # behind Hosting). When BOTH are set, the SPA connects its EventSource
    # straight to the Cloud Run origin, authenticated by a short-lived
    # single-use ticket minted through the normal (cookie) session:
    #   PRO_STREAM_DIRECT_URL      public Cloud Run URL (no trailing slash)
    #   PRO_STREAM_ALLOWED_ORIGIN  the Hosting origin allowed via CORS
    # In-process ticket store is safe: deployments enforce max-instances=1
    # (the same single-writer invariant that guards /data).
    stream_direct_url = os.environ.get("PRO_STREAM_DIRECT_URL", "").strip().rstrip("/")
    stream_allowed_origin = os.environ.get(
        "PRO_STREAM_ALLOWED_ORIGIN", "").strip().rstrip("/")
    stream_direct = bool(stream_direct_url and stream_allowed_origin)
    stream_tickets: dict[str, float] = {}  # ticket -> monotonic expiry
    STREAM_TICKET_TTL = 60.0
    firebase_web_config = None
    if google_enabled:
        import json as _json

        raw_config = os.environ.get("PRO_FIREBASE_WEB_CONFIG", "")
        try:
            firebase_web_config = _json.loads(raw_config) if raw_config else None
        except ValueError:
            firebase_web_config = None
        if firebase_web_config is None:
            import logging

            logging.getLogger(__name__).warning(
                "PRO_FIREBASE_WEB_CONFIG missing/invalid — Google sign-in "
                "disabled (the SPA needs the public web config to start "
                "the OAuth popup)"
            )
            google_enabled = False
    elif firebase_project and not allowed_emails:
        import logging

        logging.getLogger(__name__).warning(
            "PRO_FIREBASE_PROJECT_ID set without PRO_ALLOWED_EMAILS — "
            "Google sign-in stays DISABLED (fail closed; an empty allowlist "
            "would admit any Google account)"
        )

    @asynccontextmanager
    async def lifespan(app):
        state.broadcaster.bind_loop(asyncio.get_running_loop())
        pollers = []
        try:
            from tradingagents.pro.dashboard.ticker import (
                PriceAlertEngine,
                QuoteTickPoller,
            )

            def _emit_price_alert(severity, event, text, **labels):
                # AlertManager (Telegram/webhook/log/notification) when a
                # loop is attached; SSE + notification store always
                if state.alerts is not None:
                    state.alerts.emit(severity, event, text, **labels)
                    return
                from datetime import datetime, timezone

                now = datetime.now(timezone.utc).isoformat()
                state.prefs.add_notification(
                    severity=severity, event=event, text=text, time=now)
                state.broadcaster.publish("alert", {
                    "severity": severity, "event": event,
                    "text": text, "time": now,
                })

            alert_engine = PriceAlertEngine(state.prefs, _emit_price_alert)

            # one poller per live symbol whose vendor supports quotes;
            # registry access runs the vendor probes (logged) exactly once
            for spec in state.marketdata.registry.values():
                if spec.live and spec.source in ("delta_exchange", "oanda_gold",
                                                 "oanda"):
                    poller = QuoteTickPoller(
                        spec.feed_factory(), state.broadcaster,
                        symbol=spec.vendor_symbol, display_symbol=spec.symbol,
                        cache=state.ticks,
                        alert_engine=alert_engine,
                    )
                    poller.start()
                    pollers.append(poller)
        except Exception:  # a broken tick feed must never block the app
            import logging

            logging.getLogger(__name__).exception("tick pollers not started")
        yield
        for poller in pollers:
            poller.stop()
        # P2-11: the liquidation stream starts lazily inside IntelService;
        # stop it here so reloads don't leak websocket threads
        liq = getattr(state.intel, "_liquidations", None)
        if liq is not None:
            liq.stop()

    app = FastAPI(title="TradingAgents Pro Dashboard", lifespan=lifespan)
    app.state.dashboard = state

    def _consume_stream_ticket(request: Request) -> bool:
        """Single-use, short-TTL ticket auth for the direct-SSE path only.
        Consumed on first validation — a replayed URL is rejected."""
        import time as _time

        supplied = request.query_params.get("ticket", "")
        if not (stream_direct and supplied):
            return False
        now = _time.monotonic()
        for stale in [t for t, exp in stream_tickets.items() if exp < now]:
            stream_tickets.pop(stale, None)
        return stream_tickets.pop(supplied, 0.0) >= now

    def _authenticated(request: Request) -> bool:
        if not token:
            return True
        supplied = request.headers.get("x-api-key", "")
        if hmac.compare_digest(supplied, token):
            return True
        cookie = request.cookies.get(SESSION_COOKIE, "")
        if bool(cookie) and _session_valid(cookie):
            return True
        return (request.url.path == "/api/stream"
                and _consume_stream_ticket(request))

    if not token:
        import logging

        logging.getLogger(__name__).warning(
            "dashboard auth DISABLED (no PRO_DASHBOARD_TOKEN) — dev/testing "
            "only; set the token before any non-loopback exposure"
        )
    # P3-05 authorization: every mutating verb under /api requires the
    # operator role — /api/session excepted (it MINTS the session; a
    # viewer must be able to sign in). /api/auth/* and /api/stream/ticket
    # are GET-only, so the mutation gate never applies to them. The
    # operator-only surface this blanket rule covers, explicitly:
    #   POST   /api/pipeline/run            trigger an LLM run (costs money)
    #   POST   /api/flatten                 emergency flatten (execution!)
    #   POST   /api/reconcile/resolve       flatten unknown venue positions
    #   POST   /api/runs/{id}/ask[/stream]  grounded Q&A (real LLM spend)
    #   POST   /api/calibration/backfill    retro-score stored runs
    #   PUT    /api/prefs                   write prefs
    #   POST/DELETE /api/watchlists…        watchlist mutation
    #   POST/DELETE /api/price-alerts…      price-alert mutation
    #   POST/DELETE /api/condition-alerts…  condition-alert mutation
    #   POST   /api/notifications/read      notification read-state
    #   POST   /api/backtest/run|cancel|optimize|portfolio|bakeoff
    #   DELETE /api/backtest/runs/{id}      delete a saved backtest
    #   POST   /api/users                   role administration
    # Read-only GETs stay viewer-accessible. A viewer's own prefs writes
    # (prefs/watchlists/alerts/notifications) are still operator-gated:
    # P3-05's contract is "viewers cannot mutate", full stop.
    _MUTATION_EXEMPT = {"/api/session"}
    _MUTATING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

    if token:
        @app.middleware("http")
        async def require_api_key(request: Request, call_next):
            path = request.url.path
            # only /api/* carries data; the static shell and /healthz are
            # open. /api/session mints the cookie; /api/auth/config carries
            # no data (it tells the login screen which UI to render).
            if (not path.startswith("/api")
                    or path in ("/api/session", "/api/auth/config")):
                return await call_next(request)
            if not _authenticated(request):
                return JSONResponse({"detail": "missing or invalid X-API-Key"},
                                    status_code=401)
            # fresh=True: mutations re-resolve the role from the users
            # table per request (role-demotion takes effect immediately);
            # reads keep the fast signed-claim path (accepted lag, §1 of
            # docs/CONTROLS.md)
            if (request.method in _MUTATING_METHODS
                    and path not in _MUTATION_EXEMPT
                    and _request_role(request, fresh=True) != "operator"):
                return JSONResponse(
                    {"detail": "your role (viewer) cannot perform this "
                               "action; ask an operator"},
                    status_code=403)
            return await call_next(request)

    if os.environ.get("PRO_DASHBOARD_DEV") == "1":
        # added after the auth middleware => wraps it, so 401s carry CORS
        # headers and the Vite dev origin can react to them
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["X-API-Key", "Content-Type", "Last-Event-ID"],
        )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/health/live")
    def health_live() -> JSONResponse:
        # aggregate liveness for uptime monitors, the loop's entry gate,
        # and the dead-man switch. 503 when degraded so external monitors
        # treat degraded-while-armed as down (go-live Phase 5).
        from tradingagents.pro.health import live_health

        report = live_health(state, state.arming).as_dict()
        return JSONResponse(report, status_code=200 if report["ok"] else 503)

    @app.get("/metrics")
    def metrics() -> Response:
        # Prometheus scrape target; open like /healthz (no payload data,
        # counters only). Empty until a service loop attaches a registry.
        from fastapi.responses import PlainTextResponse

        rendered = state.metrics.render_prometheus() if state.metrics else ""
        return PlainTextResponse(rendered, media_type="text/plain; version=0.0.4")

    @app.get("/api/auth/config")
    def auth_config() -> dict:
        # open by design: no data, just which login UI the SPA should render
        # and where the EventSource should connect (null = same origin)
        return {
            "auth_required": bool(token),
            "google": google_enabled,
            "firebase": firebase_web_config if google_enabled else None,
            "stream_url": stream_direct_url if stream_direct else None,
        }

    @app.get("/api/stream/ticket")
    def stream_ticket() -> dict:
        # auth-gated by the middleware like every /api route: only an
        # established session (cookie through Hosting) can mint one
        import time as _time

        if not stream_direct:
            raise HTTPException(status_code=404,
                                detail="direct stream not configured")
        ticket = secrets.token_urlsafe(32)
        stream_tickets[ticket] = _time.monotonic() + STREAM_TICKET_TTL
        return {"ticket": ticket}

    def _google_identity(request: Request) -> str:
        """Validate the Authorization: Bearer Firebase ID token; returns the
        allowlisted email or raises HTTPException (401 invalid, 403 not
        allowed). Only called when google_enabled."""
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(status_code=401,
                                detail="missing or invalid X-API-Key")
        try:
            claims = _verify_firebase_token(
                auth_header.removeprefix("Bearer ").strip(), firebase_project)
        except Exception:
            raise HTTPException(status_code=401,
                                detail="invalid or expired Google sign-in "
                                       "token") from None
        email = str(claims.get("email", "")).lower()
        if not claims.get("email_verified") or not email:
            raise HTTPException(status_code=401,
                                detail="Google account email not verified")
        if email not in allowed_emails:
            raise HTTPException(status_code=403,
                                detail="this Google account is not authorized")
        return email

    @app.post("/api/session")
    def create_session(request: Request, response: Response) -> dict:
        identity = None
        role = "operator"
        if token:
            supplied = request.headers.get("x-api-key", "")
            authed = hmac.compare_digest(supplied, token)
            if not authed:
                # an existing valid session JWT re-establishes on boot — a
                # Google user's page reload carries ONLY the cookie (no
                # header, no fresh ID token) and must not bounce to the
                # login screen. The signed `sub` claim restores identity;
                # re-minting below gives a sliding TTL.
                claims = _session_claims(
                    request.cookies.get(SESSION_COOKIE, ""))
                if claims is not None:
                    authed = True
                    if claims.get("sub") not in (None, "api-token"):
                        identity = str(claims["sub"])
            if not authed:
                if not google_enabled:
                    raise HTTPException(status_code=401,
                                        detail="missing or invalid X-API-Key")
                identity = _google_identity(request)  # raises 401/403
            # P3-05: re-resolve the role from the users table on every
            # (re-)establish — a downgrade lands at the next page load,
            # not only when the 7-day cookie finally expires. API-key
            # sessions (identity None) stay full operator by design.
            if identity is not None:
                role = _role_for(identity)
            # max_age: without it the browser drops the cookie on quit —
            # combined with the stateless JWT, sign-in survives browser
            # restarts, server redeploys, and scale-to-zero for the TTL
            response.set_cookie(SESSION_COOKIE, _mint_session(identity, role),
                                httponly=True, samesite="strict", path="/",
                                max_age=SESSION_TTL_SECONDS)
        return {"authenticated": True, "auth_required": bool(token),
                "identity": identity, "role": role}

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        state.broadcaster.ensure_loop()
        raw = (request.headers.get("last-event-id")
               or request.query_params.get("last_event_id") or "")
        last_id = int(raw) if raw.isdigit() else None
        raw_max = request.query_params.get("max_events") or ""
        max_events = int(raw_max) if raw_max.isdigit() else None

        async def frames():
            # max_events bounds the stream (curl debugging, tests);
            # browsers omit it and hold the connection open
            delivered = 0
            agen = state.broadcaster.subscribe(last_id)
            try:
                async for frame in agen:
                    yield frame
                    if frame.startswith("id:"):  # real events only, not heartbeats
                        delivered += 1
                        if max_events is not None and delivered >= max_events:
                            return
            finally:
                await agen.aclose()

        headers = {"Cache-Control": "no-cache",
                   "X-Accel-Buffering": "no"}  # nginx: do not buffer SSE
        # direct-SSE is cross-origin from the Hosting site; EventSource GETs
        # are CORS "simple requests" (no preflight) but the response must
        # name the allowed origin. Ticket auth means no credentials header.
        if stream_direct and (request.headers.get("origin", "").rstrip("/")
                              == stream_allowed_origin):
            headers["Access-Control-Allow-Origin"] = stream_allowed_origin
        return StreamingResponse(
            frames(),
            media_type="text/event-stream",
            headers=headers,
        )

    def _legacy_html() -> str:
        return (
            resources.files("tradingagents.pro.dashboard")
            .joinpath("templates", "dashboard.html")
            .read_text(encoding="utf-8")
        )

    static_root = resources.files("tradingagents.pro.dashboard") / "static"
    spa_index = static_root / "index.html"
    try:
        has_spa = spa_index.is_file()
    except Exception:
        has_spa = False

    if has_spa:
        import os as _os

        from fastapi.staticfiles import StaticFiles

        assets_dir = _os.fspath(static_root / "assets")
        if _os.path.isdir(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        if has_spa:
            return HTMLResponse(spa_index.read_text(encoding="utf-8"),
                                headers={"Cache-Control": "no-cache"})
        return HTMLResponse(_legacy_html())

    @app.get("/legacy", response_class=HTMLResponse)
    def legacy() -> str:
        return _legacy_html()

    @app.get("/api/symbols")
    def symbols() -> list[dict]:
        return state.marketdata.symbols()

    def _parse_timeframe(value: str):
        from tradingagents.contracts import Timeframe

        try:
            return Timeframe(value)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"unknown timeframe {value!r}; use one of "
                       f"{[t.value for t in Timeframe]}",
            ) from None

    def _fetch_bars(symbol: str, timeframe: str, limit: int,
                    end: int | None = None):
        from datetime import datetime, timezone

        from tradingagents.dataflows.errors import (
            NoMarketDataError,
            VendorRateLimitError,
        )

        tf = _parse_timeframe(timeframe)
        end_dt = (datetime.fromtimestamp(end, tz=timezone.utc)
                  if end is not None else None)
        try:
            return state.marketdata.get_bars(symbol, tf, limit, end=end_dt)
        except md.UnknownSymbolError:
            raise HTTPException(status_code=404,
                                detail=f"unknown symbol {symbol!r}") from None
        except md.UnsupportedTimeframeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except VendorRateLimitError as exc:
            raise HTTPException(status_code=503, detail=str(exc),
                                headers={"Retry-After": "30"}) from None
        except NoMarketDataError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except Exception as exc:  # vendor unreachable / network egress blocked
            raise HTTPException(
                status_code=503,
                detail=f"market data vendor unreachable: {type(exc).__name__}",
                headers={"Retry-After": "60"},
            ) from None

    @app.get("/api/bars")
    def bars(symbol: str, timeframe: str = "1d",
             limit: int = md.DEFAULT_LIMIT,
             end: int | None = None) -> list[dict]:
        """``end`` (epoch seconds, exclusive) pages history backward — the
        chart's load-more. Omitted = the latest window (unchanged)."""
        return md.bars_view(_fetch_bars(symbol, timeframe, limit, end))

    @app.get("/api/chart/annotations")
    async def chart_annotations_route(symbol: str) -> dict:
        """The AI's record for one symbol, chart-paintable (chart Phase 1).
        Async on purpose: pure in-memory read — never queued behind
        vendor-bound threadpool handlers (the R2.7 lesson)."""
        import os

        from tradingagents.pro.dashboard.annotations import chart_annotations

        known = {s["symbol"] for s in state.marketdata.symbols()}
        if symbol not in known:
            raise HTTPException(status_code=404,
                                detail=f"unknown symbol {symbol!r}")
        cadence = float(os.environ.get("PRO_LOOP_INTERVAL_SECONDS", "3600"))
        return chart_annotations(state.runs, state.memory, symbol,
                                 cadence_seconds=cadence)

    @app.get("/api/bars/indicators")
    def bar_indicators(symbol: str, timeframe: str = "1d",
                       names: str = "", limit: int = md.DEFAULT_LIMIT) -> dict:
        from tradingagents.pro.ingestion.indicators import DEFAULT_INDICATOR_NAMES

        requested = tuple(n for n in names.split(",") if n) or DEFAULT_INDICATOR_NAMES
        fetched = _fetch_bars(symbol, timeframe, limit)
        try:
            return md.indicator_series_view(fetched, requested)
        except ValueError as exc:  # unknown indicator names
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/bars/volume-profile")
    def bars_volume_profile(symbol: str, timeframe: str = "1d",
                            limit: int = md.DEFAULT_LIMIT,
                            bins: int = 24) -> dict:
        """Fixed-range volume profile over the served bar window (review
        P2.4) — deterministic server math; the chart renders, never
        computes."""
        from tradingagents.pro.ingestion.profile import volume_profile

        fetched = _fetch_bars(symbol, timeframe, limit)
        try:
            return volume_profile(fetched, bins=bins)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/overview")
    def overview() -> dict:
        return service.market_overview(state.latest_run())

    _regime_cache: dict = {"at": 0.0, "payload": None}

    @app.get("/api/regime")
    def regime() -> dict:
        """Per-symbol deterministic regime (trader review G3) — the same
        classify_regime the pipeline records, computed over daily bars for
        every dashboard symbol so the strip never shows one symbol's
        regime on another's screen. Never an LLM."""
        import time as _time

        from tradingagents.pro.analytics.features import classify_regime
        from tradingagents.pro.ingestion.sessions import current_session

        now = _time.monotonic()
        if _regime_cache["payload"] is not None and now - _regime_cache["at"] < 300:
            return _regime_cache["payload"]
        from datetime import datetime, timezone

        symbols: dict[str, dict] = {}
        for sym in sorted(state.marketdata.registry):
            try:
                bars = state.marketdata.get_bars(sym, "1d", limit=60)
                value = classify_regime(bars).value if len(bars) >= 3 else None
            except Exception:
                value = None  # degraded vendor -> honest null, not a guess
            symbols[sym] = {"regime": value}
        as_of = datetime.now(timezone.utc)
        payload = {
            "symbols": symbols,
            "session": current_session(as_of).value,
            "as_of": as_of.isoformat(),
        }
        _regime_cache.update(at=now, payload=payload)
        return payload

    @app.get("/api/runs")
    def runs() -> list[dict]:
        return [
            {
                "run_id": run.run_id,
                "started_at": run.started_at.isoformat(),
                "symbol": run.symbol,
                "action": run.recommendation.action.value if run.recommendation else None,
                "rejected_at": run.rejection and run.rejection.get("stage"),
                "timeframe": run.timeframe,
                "trigger": run.trigger,
                # P3-07 provenance stamp; None on pre-stamp runs
                "versions": run.versions,
            }
            for run in state.runs
        ]

    def _run_or_404(run_id: str) -> RunRecord:
        for run in state.runs:
            if run.run_id == run_id:
                return run
        raise HTTPException(status_code=404, detail=f"no run {run_id}")

    @app.get("/api/runs/{run_id}/timeline")
    def timeline(run_id: str) -> dict:
        return service.debate_timeline(_run_or_404(run_id))

    @app.get("/api/runs/{run_id}/evidence")
    def evidence(run_id: str) -> dict:
        return service.evidence_panels(_run_or_404(run_id))

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str) -> JSONResponse:
        """P3-06 decision-audit export pack: one complete JSON bundle per
        run (inputs, transcript, gates, ticket, execution, outcome,
        calibration, P3-07 versions) — the compliance artifact. Served as
        a download; auth-gated by the /api middleware like every run view.
        JSON only: the repo's HTML/PDF report generator renders
        BacktestResults, not decision runs, and a PDF dependency for this
        pack is deliberately avoided."""
        run = _run_or_404(run_id)  # 404 before any filename is built
        pack = service.decision_export_pack(run, state.memory)
        return JSONResponse(pack, headers={
            "Content-Disposition":
                f'attachment; filename="run-{run.run_id}.json"'})

    @app.get("/api/runs/{run_id}/diff")
    def run_diff(run_id: str, against: str = "previous") -> dict:
        """P5-05 "what changed the machine's mind": a structured, honest
        diff of two runs of the SAME symbol.

        ``against`` is another run_id, or the literal "previous" (default)
        — the most recent earlier run for this run's symbol. 404 names an
        unknown run (either side) or the absence of a previous run; 422
        explains a cross-symbol comparison rather than diffing two
        unrelated decisions.
        """
        run = _run_or_404(run_id)
        if against == "previous":
            other = service.previous_run_for(state.runs, run)
            if other is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"no earlier run for {run.symbol} to compare "
                           f"against; this is its first recorded run")
        else:
            other = _run_or_404(against)
        try:
            return service.run_diff(other, run)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    def _ticket_view(run: RunRecord | None) -> dict:
        if run is None:
            return service.recommendation_view(None)
        reflection = run.state.get("reflection") or {}
        view = service.recommendation_view(
            run.recommendation,
            invalidation=reflection.get("invalidation"),
            rejection=run.rejection,
        )
        # let per-symbol/per-run consumers link back without /api/overview
        view.setdefault("symbol", run.symbol)
        view["run_id"] = run.run_id
        view["run_started_at"] = run.started_at.isoformat()
        view["timeframe"] = run.timeframe
        # empirical p(win) from the system's own scored record (lived +
        # retro outcomes); None below the sample floor — never invented
        if run.recommendation is not None:
            view["p_win"] = service.estimate_p_win(
                state.memory, run.recommendation.confidence)
        return view

    # async on purpose (review R2.7): these are pure in-memory reads, yet as
    # sync defs they shared the threadpool with vendor-bound handlers
    # (intel/bars/calendar) and were observed queuing >30s behind them.
    # On the event loop they cannot be starved.
    @app.get("/api/recommendation/latest")
    async def latest_recommendation(symbol: str | None = None) -> dict:
        run = state.latest_run_for(symbol) if symbol else state.latest_run()
        return _ticket_view(run)

    @app.get("/api/runs/{run_id}/recommendation")
    async def run_recommendation(run_id: str) -> dict:
        return _ticket_view(_run_or_404(run_id))

    def _ask_prep(run_id: str, body: dict):
        """Shared validation + prompt inputs for the ask endpoints. Returns
        (run, llm, question, supporting, counters, invalidation)."""
        from tradingagents.pro.pipeline.nodes import _all_evidence
        from tradingagents.pro.pipeline.qa import MAX_QUESTION_CHARS

        run = _run_or_404(run_id)
        service_obj = getattr(state.trigger, "service", None)
        llm = getattr(service_obj, "llm", None)
        if llm is None:
            raise HTTPException(
                status_code=503,
                detail="ask is unavailable in monitor mode (no model attached)")
        question = str(body.get("question", "")).strip()
        if not question:
            raise HTTPException(status_code=422, detail="question is required")
        if len(question) > MAX_QUESTION_CHARS:
            raise HTTPException(
                status_code=422,
                detail=f"question exceeds {MAX_QUESTION_CHARS} characters")
        rec = run.recommendation
        if rec is not None:
            supporting = list(rec.evidence)
            counters = list(rec.counterarguments)
        else:  # rejected / HOLD: pull whatever evidence the run gathered
            try:
                supporting = _all_evidence(run.state)
            except Exception:
                supporting = []
            counters = []
        invalidation = (run.state.get("reflection") or {}).get("invalidation")
        return run, llm, question, supporting, counters, invalidation

    @app.post("/api/runs/{run_id}/ask")
    async def ask_run(run_id: str, request: Request) -> dict:
        """Grounded Q&A over ONE run's record (evidence/debate/verdict).
        Answers only from that record with agent-id citations; refuses to
        reach beyond it. Needs the pipeline LLM (the loop's own bundle)."""
        from tradingagents.pro.models import ModelBundle
        from tradingagents.pro.pipeline.nodes import _debate_block
        from tradingagents.pro.pipeline.qa import EvidenceAnswer, build_qa_prompt

        run, llm, question, supporting, counters, invalidation = _ask_prep(
            run_id, await request.json())
        prompt = build_qa_prompt(
            question, symbol=run.symbol, recommendation=run.recommendation,
            supporting=supporting, counterarguments=counters,
            debate_block=_debate_block(run.debate), invalidation=invalidation)
        bundle = ModelBundle.coerce(llm)
        try:
            answer = await asyncio.to_thread(
                bundle.deep.with_structured_output(EvidenceAnswer).invoke, prompt)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"model call failed: {type(exc).__name__}") from None
        if answer is None:
            raise HTTPException(status_code=502, detail="model returned nothing")
        return {
            "run_id": run.run_id,
            "answerable": answer.answerable,
            "answer": answer.answer,
            "cited_agent_ids": list(answer.cited_agent_ids),
        }

    @app.post("/api/runs/{run_id}/ask/stream")
    async def ask_run_stream(run_id: str, request: Request) -> StreamingResponse:
        """Streaming grounded Q&A (PB.2): same record-only discipline, but
        the answer prose streams token-by-token (<5s to first token vs the
        structured endpoint's ~30s wait), ending with a 'SOURCES:' line the
        client splits into citation tags. Falls back to /ask on the client
        if the model can't stream."""
        from tradingagents.pro.models import ModelBundle
        from tradingagents.pro.observability import supports_streaming
        from tradingagents.pro.pipeline.nodes import _debate_block
        from tradingagents.pro.pipeline.qa import build_qa_stream_prompt

        run, llm, question, supporting, counters, invalidation = _ask_prep(
            run_id, await request.json())
        bundle = ModelBundle.coerce(llm)
        # capability probe BEFORE any bytes go out: a model that cannot
        # stream must produce a status the client can branch on, not a 200
        # whose body is an error string. Discovering this inside generate()
        # is what made the documented /ask fallback unreachable.
        if not supports_streaming(bundle.deep):
            raise HTTPException(
                status_code=501,
                detail="this model does not support streaming; use /ask")
        prompt = build_qa_stream_prompt(
            question, symbol=run.symbol, recommendation=run.recommendation,
            supporting=supporting, counterarguments=counters,
            debate_block=_debate_block(run.debate), invalidation=invalidation)

        def generate():
            try:
                for chunk in bundle.deep.stream(prompt):
                    text = getattr(chunk, "content", None)
                    if text:
                        yield text if isinstance(text, str) else str(text)
            except Exception as exc:
                # bytes may already be on the wire, so a status code is no
                # longer available: emit the sentinel the client watches for
                # and let it retry against /ask.
                logger.exception("ask stream failed for run %s", run.run_id)
                yield f"\n{STREAM_INTERRUPTED} {type(exc).__name__}]"

        return StreamingResponse(generate(), media_type="text/plain")

    @app.get("/api/status")
    def status() -> dict:
        return service.system_status(state.router, state.equity, state.arming,
                                     ticks=state.ticks, marketdata=state.marketdata,
                                     memory=state.memory)

    @app.post("/api/flatten")
    async def flatten(request: Request) -> JSONResponse:
        """Emergency flatten — the ONE sanctioned dashboard->execution
        write. Behind auth (the /api middleware) plus a typed-confirmation
        body echoing the exact phrase the UI generated."""
        if state.router is None:
            raise HTTPException(status_code=503,
                                detail="no execution router attached")
        body = await request.json()
        if body.get("confirm") != "FLATTEN":
            raise HTTPException(
                status_code=422,
                detail="type FLATTEN to confirm the emergency flatten")
        from tradingagents.pro.flatten import emergency_flatten

        summary = emergency_flatten(
            state.router, arming=state.arming, operator="dashboard",
            alerts=getattr(state, "alerts", None))
        return JSONResponse({"status": "flattened", **summary})

    @app.post("/api/reconcile/resolve")
    async def reconcile_resolve(request: Request) -> JSONResponse:
        """Operator remediation for reconciliation drift: flatten venue
        positions the local book does not know (no surviving stop/TP plan
        — adopting them would leave unmanaged risk). Typed confirmation,
        same contract as /api/flatten; audited per symbol."""
        if state.router is None:
            raise HTTPException(status_code=503,
                                detail="no execution router attached")
        body = await request.json()
        if body.get("confirm") != "RESOLVE":
            raise HTTPException(
                status_code=422,
                detail="type RESOLVE to confirm flattening unknown positions")
        marks: dict[str, float] = {}
        if state.marketdata is not None:
            for position in state.router.adapter.positions():
                if position.symbol in state.router.local_book:
                    continue
                try:
                    from tradingagents.contracts import Timeframe

                    bars = state.marketdata.get_bars(
                        position.symbol, Timeframe.D1, limit=1)
                    if bars:
                        marks[position.symbol] = bars[-1].close
                except Exception:  # noqa: BLE001 — fall back to avg price
                    import logging

                    logging.getLogger(__name__).warning(
                        "no mark for %s during drift resolve",
                        position.symbol, exc_info=True)
        summary = state.router.resolve_unknown_positions(
            marks, operator="dashboard")
        report = state.router.reconcile()
        if getattr(state, "alerts", None) is not None:
            state.alerts.emit(
                "warning", "reconciliation_resolved",
                f"operator flattened {len(summary['flattened'])} unknown "
                "position(s); drift resolution audited",
            )
        return JSONResponse({"status": "resolved", **summary,
                             "in_sync": report.in_sync})

    @app.get("/api/alerts")
    def alerts() -> dict:
        return service.alert_feed(state.runs)

    @app.post("/api/pipeline/run")
    async def run_pipeline(request: Request) -> JSONResponse:
        import threading as _threading

        body = await request.json()
        symbol = body.get("symbol")
        timeframe = body.get("timeframe")
        trigger = state.trigger
        if trigger is None:
            raise HTTPException(
                status_code=503,
                detail="no pipeline service attached (monitor mode) — "
                       "run via the service container or pro_live_terminal",
            )
        if symbol not in trigger.SYMBOLS or timeframe not in trigger.TIMEFRAMES:
            raise HTTPException(
                status_code=422,
                detail=f"symbol must be one of {list(trigger.SYMBOLS)} and "
                       f"timeframe one of {list(trigger.TIMEFRAMES)}",
            )
        # feed-support validation BEFORE the worker thread spawns: an
        # unsupported pair × timeframe (e.g. FX intraday without an OANDA
        # token) must surface as a 422 here, not crash the run to a 500
        # in the background (mirrors the TriggerBusy → 409 mapping below)
        from tradingagents.pro.main import TriggerUnsupported

        try:
            trigger.validate_supported(symbol, timeframe)
        except TriggerUnsupported as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if trigger.busy():
            raise HTTPException(status_code=409,
                                detail="a pipeline run is already in progress")

        def work():
            import logging as _logging

            try:
                trigger.run(symbol, timeframe)
            except Exception:
                _logging.getLogger(__name__).exception("on-demand run failed")

        _threading.Thread(target=work, name="on-demand-run", daemon=True).start()
        return JSONResponse({"status": "started", "symbol": symbol,
                             "timeframe": timeframe}, status_code=202)

    # P3-05 per-user preference isolation. Google identities get their own
    # PrefsStore over the kv key "dashboard_prefs:<email>"; token auth (and
    # open dev mode) keeps the legacy shared "dashboard_prefs" document —
    # backward compatible byte-for-byte. The cache is a plain dict with a
    # hard cap: identities are bounded by the allowlist, so real LRU
    # machinery would be ceremony (on overflow we drop everything and
    # rebuild — PrefsStore re-reads its kv row, losing nothing).
    #
    # state.prefs itself deliberately remains the SERVICE's store: the
    # alert sink (NotificationSink), the price-alert engine, intel alert
    # state, event-trigger state, and last_bar_state all write through it.
    # Those are SYSTEM state — properties of the one trading loop, not of
    # whoever is looking at the dashboard — so keying them by viewer
    # identity would fork the loop's memory per login.
    from tradingagents.pro.dashboard.prefs import PREFS_KV_KEY

    _user_prefs: dict[str, PrefsStore] = {}
    _USER_PREFS_CACHE_MAX = 256

    def _prefs_for(request: Request) -> PrefsStore:
        email = _request_identity(request)
        store = getattr(state.prefs, "store", None)
        if email is None or store is None:
            # token auth / dev mode — or file-backed prefs (no kv table to
            # key by identity): the legacy shared document
            return state.prefs
        cached = _user_prefs.get(email)
        if cached is None:
            if len(_user_prefs) >= _USER_PREFS_CACHE_MAX:
                _user_prefs.clear()
            cached = PrefsStore(store=store,
                                kv_key=f"{PREFS_KV_KEY}:{email}")
            _user_prefs[email] = cached
        return cached

    @app.get("/api/prefs")
    def get_prefs(request: Request) -> dict:
        return _prefs_for(request).get_prefs()

    @app.put("/api/prefs")
    async def put_prefs(request: Request) -> dict:
        body = await request.body()
        if len(body) > 256 * 1024:
            raise HTTPException(status_code=413, detail="prefs document too large")
        import json as _json

        from pydantic import ValidationError

        try:
            return _prefs_for(request).put_prefs(_json.loads(body))
        except _json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail=f"invalid JSON: {exc}") from None
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from None

    @app.get("/api/watchlists")
    def watchlists(request: Request) -> list[dict]:
        return _prefs_for(request).watchlists()

    @app.post("/api/watchlists")
    async def upsert_watchlist(request: Request) -> dict:
        from pydantic import ValidationError

        try:
            return _prefs_for(request).upsert_watchlist(await request.json())
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from None

    @app.delete("/api/watchlists/{name}")
    def delete_watchlist(name: str, request: Request) -> dict:
        if not _prefs_for(request).delete_watchlist(name):
            raise HTTPException(status_code=404, detail=f"no watchlist {name!r}")
        return {"deleted": name}

    @app.get("/api/price-alerts")
    def price_alerts(request: Request) -> list[dict]:
        return _prefs_for(request).price_alerts()

    @app.post("/api/price-alerts")
    async def create_price_alert(request: Request) -> dict:
        data = await request.json()
        symbol = data.get("symbol")
        if symbol not in state.marketdata.registry:
            raise HTTPException(
                status_code=422,
                detail=f"unknown symbol {symbol!r}; "
                       f"supported: {sorted(state.marketdata.registry)}")
        try:
            return _prefs_for(request).add_price_alert(data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:  # pydantic validation
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.delete("/api/price-alerts/{alert_id}")
    def delete_price_alert(alert_id: str, request: Request) -> dict:
        if not _prefs_for(request).delete_price_alert(alert_id):
            raise HTTPException(status_code=404, detail=f"no alert {alert_id}")
        return {"deleted": alert_id}

    @app.get("/api/condition-alerts")
    def condition_alerts(request: Request) -> list[dict]:
        return _prefs_for(request).condition_alerts()

    @app.post("/api/condition-alerts")
    async def create_condition_alert(request: Request) -> dict:
        from tradingagents.pro.dashboard.intel import METRIC_INFO

        data = await request.json()
        metric = data.get("metric")
        if metric not in METRIC_INFO:
            raise HTTPException(
                status_code=422,
                detail=f"unknown metric {metric!r}; "
                       f"supported: {sorted(METRIC_INFO)}")
        try:
            return _prefs_for(request).add_condition_alert(data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception as exc:  # pydantic validation
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.delete("/api/condition-alerts/{alert_id}")
    def delete_condition_alert(alert_id: str, request: Request) -> dict:
        if not _prefs_for(request).delete_condition_alert(alert_id):
            raise HTTPException(status_code=404, detail=f"no alert {alert_id}")
        return {"deleted": alert_id}

    @app.get("/api/notifications")
    def notifications(request: Request, unread: int = 0) -> dict:
        notes = _prefs_for(request).notifications(unread_only=bool(unread))
        return {"notifications": notes,
                "unread": sum(1 for n in notes if not n["read"])}

    @app.post("/api/notifications/read")
    async def mark_notifications_read(request: Request) -> dict:
        body = await request.json() if int(request.headers.get("content-length") or 0) else {}
        return {"marked": _prefs_for(request).mark_read(body.get("ids"))}

    # --- P3-05 user administration (operator-only) -------------------------

    def _users_store_or_503():
        if users_store is None:
            raise HTTPException(
                status_code=503,
                detail="user administration requires the SQLite event store "
                       "(monitor/dev mode runs without one)")
        return users_store

    @app.get("/api/users")
    def list_users(request: Request) -> dict:
        # GETs are viewer-readable by the blanket middleware rule, but the
        # user roster is administration data — explicitly operator-only
        if _request_role(request) != "operator":
            raise HTTPException(
                status_code=403,
                detail="your role (viewer) cannot list users; ask an operator")
        return {"users": _users_store_or_503().list_users()}

    @app.post("/api/users")
    async def upsert_user(request: Request) -> dict:
        # POST => the middleware already enforced role=operator
        body = await request.json()
        email, role = body.get("email"), body.get("role")
        if not isinstance(email, str) or not isinstance(role, str):
            raise HTTPException(status_code=422,
                                detail="body must be {email, role}")
        try:
            return _users_store_or_503().put_user(email, role)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    # --- P3-11 API tokens (operator-only management) ------------------------
    # The tokens gate the /public/v1 read-only API below. Storage is the
    # event store's api_tokens table (hash only); the raw token appears in
    # exactly one response — the creation's.

    def _event_store_or_503(feature: str):
        if users_store is None:
            raise HTTPException(
                status_code=503,
                detail=f"{feature} requires the SQLite event store "
                       "(monitor/dev mode runs without one)")
        return users_store

    def _require_operator(request: Request, what: str) -> None:
        # GETs are viewer-readable by the blanket middleware rule, but
        # token/webhook administration is operator data (like /api/users)
        if _request_role(request) != "operator":
            raise HTTPException(
                status_code=403,
                detail=f"your role (viewer) cannot {what}; ask an operator")

    @app.get("/api/tokens")
    def list_api_tokens(request: Request) -> dict:
        _require_operator(request, "list API tokens")
        return {"tokens": _event_store_or_503("token management")
                .list_api_tokens()}

    @app.post("/api/tokens")
    async def create_api_token(request: Request) -> dict:
        # POST => the middleware already enforced role=operator
        body = await request.json()
        label, scopes = body.get("label"), body.get("scopes")
        expires_days = body.get("expires_days")  # optional; None = no expiry
        try:
            created = _event_store_or_503("token management") \
                .create_api_token(label or "", scopes or [],
                                  expires_days=expires_days)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # the ONE response that carries the raw token — store only the hash
        created["note"] = ("store this token now; it is shown once and "
                           "only its hash is kept")
        return created

    @app.delete("/api/tokens/{token_hash}")
    def revoke_api_token(token_hash: str, request: Request) -> dict:
        if not _event_store_or_503("token management") \
                .revoke_api_token(token_hash):
            raise HTTPException(status_code=404,
                                detail=f"no token {token_hash}")
        return {"revoked": token_hash}

    # --- P3-11 webhooks (operator-only management) ---------------------------

    def _webhook_registry():
        from tradingagents.pro.webhooks import WebhookRegistry

        return WebhookRegistry(_event_store_or_503("webhook management"),
                               alerts=state.alerts)

    @app.get("/api/webhooks")
    def list_webhooks(request: Request) -> dict:
        _require_operator(request, "list webhooks")
        return {"webhooks": _webhook_registry().list()}

    @app.post("/api/webhooks")
    async def create_webhook(request: Request) -> dict:
        body = await request.json()
        try:
            return _webhook_registry().add(
                str(body.get("url") or ""),
                str(body.get("event") or "run_complete"),
                str(body.get("secret") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.delete("/api/webhooks/{hook_id}")
    def delete_webhook(hook_id: str, request: Request) -> dict:
        if not _webhook_registry().delete(hook_id):
            raise HTTPException(status_code=404,
                                detail=f"no webhook {hook_id}")
        return {"deleted": hook_id}

    @app.post("/api/webhooks/{hook_id}/enable")
    def enable_webhook(hook_id: str, request: Request) -> dict:
        # POST => the middleware already enforced role=operator. Clears
        # the strike count + disabled flag after a three-strikes
        # auto-disable (the webhook_disabled alert points here).
        if not _webhook_registry().enable(hook_id):
            raise HTTPException(status_code=404,
                                detail=f"no webhook {hook_id}")
        return {"enabled": hook_id}

    # --- P4-03 listings (calibration-gated marketplace foundations) ---------
    # CRUD over the event store's listings table. Mutations are operator-
    # only via the blanket middleware rule (POST/PUT/DELETE under /api);
    # GETs stay viewer-readable — a listing is catalogue data, not
    # administration. The publish transition is THE point of P4-03: it
    # runs service.listing_gate over calibration_json and 422s with the
    # specific failures, so nothing reaches "published" without a real
    # graded record. DELETE delists (soft) — a listing that was ever
    # published soft-retires rather than vanishing.

    def _listing_or_404(listing_id: str) -> dict:
        listing = _event_store_or_503("listings") \
            .get_listing(listing_id)
        if listing is None:
            raise HTTPException(status_code=404,
                                detail=f"no listing {listing_id}")
        return listing

    @app.get("/api/listings")
    def list_listings(request: Request, status: str | None = None) -> dict:
        try:
            return {"listings": _event_store_or_503("listings")
                    .list_listings(status=status)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/listings")
    async def create_listing(request: Request) -> dict:
        # POST => the middleware already enforced role=operator
        body = await request.json()
        # the seller of record: the signed-in identity, or the deployment
        # operator for X-API-Key / dev-mode requests
        owner = _request_identity(request) or "operator@deployment"
        try:
            return _event_store_or_503("listings").create_listing(
                owner_email=str(body.get("owner_email") or owner),
                kind=str(body.get("kind") or ""),
                title=str(body.get("title") or ""),
                description=str(body.get("description") or ""),
                config=body.get("config") or {},
                calibration=body.get("calibration"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/listings/{listing_id}")
    def get_listing(listing_id: str, request: Request) -> dict:
        return _listing_or_404(listing_id)

    @app.put("/api/listings/{listing_id}")
    async def update_listing(listing_id: str, request: Request) -> dict:
        body = await request.json()
        _listing_or_404(listing_id)
        try:
            # store-level rule: touching config/calibration on a published
            # listing demotes it to draft — edits re-enter the publish gate
            updated = _event_store_or_503("listings").update_listing(
                listing_id,
                title=body.get("title"),
                description=body.get("description"),
                config=body.get("config"),
                calibration=body.get("calibration"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return updated

    @app.delete("/api/listings/{listing_id}")
    def delist_listing(listing_id: str, request: Request) -> dict:
        _listing_or_404(listing_id)
        return _event_store_or_503("listings") \
            .set_listing_status(listing_id, "delisted")

    @app.post("/api/listings/{listing_id}/publish")
    def publish_listing(listing_id: str, request: Request) -> dict:
        # POST => operator (middleware). The calibration gate — the whole
        # point of P4-03: no graded record, no marketplace.
        listing = _listing_or_404(listing_id)
        failures = service.listing_gate(listing.get("calibration"))
        if failures:
            raise HTTPException(
                status_code=422,
                detail={"published": False,
                        "listing_id": listing_id,
                        "failures": failures})
        return _event_store_or_503("listings") \
            .set_listing_status(listing_id, "published")

    # --- P3-11 public read-only API (/public/v1, Bearer-token gated) --------
    # Mounted OUTSIDE /api on purpose: the session middleware matches
    # request paths on startswith("/api"), so /public/v1 never sees the
    # cookie/X-API-Key gate — but every endpoint here requires a Bearer
    # token from the api_tokens table, scope-checked per route, ALWAYS
    # (dev mode included). Read-only by construction: recent decisions
    # (no transcripts/evidence — the audit surfaces stay operator-only)
    # and the calibration record.
    #
    # Rate limiting is an in-process token bucket per api-token. That is
    # HONEST for this deployment shape: Cloud Run runs max-instances=1
    # (the same single-writer invariant that guards /data and the stream
    # tickets), so one process sees all traffic. A multi-instance future
    # needs a shared store; this is deliberately not that.
    public_rate_per_min = float(
        os.environ.get("PRO_PUBLIC_RATE_LIMIT", "60"))
    _public_buckets: dict[str, list] = {}  # token_hash -> [tokens, last_t]

    def _public_rate_ok(token_hash: str) -> "tuple[bool, int]":
        """(allowed, retry_after_seconds). Continuous-refill token bucket,
        capacity == PRO_PUBLIC_RATE_LIMIT requests per minute."""
        import math as _math
        import time as _time

        if public_rate_per_min <= 0:  # explicit opt-out
            return True, 0
        per_second = public_rate_per_min / 60.0
        now = _time.monotonic()
        tokens, last = _public_buckets.get(
            token_hash, [public_rate_per_min, now])
        tokens = min(public_rate_per_min, tokens + (now - last) * per_second)
        if tokens >= 1.0:
            _public_buckets[token_hash] = [tokens - 1.0, now]
            return True, 0
        _public_buckets[token_hash] = [tokens, now]
        return False, max(1, _math.ceil((1.0 - tokens) / per_second))

    def _public_auth(request: Request, scope: str) -> dict:
        """Bearer-token auth + scope check + rate limit for /public/v1.
        Returns the token record; raises 401/403/429/503."""
        store = getattr(state.prefs, "store", None)
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="public API requires the SQLite event store")
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="missing bearer token; send Authorization: "
                       "Bearer <api-token>")
        record = store.resolve_api_token(
            auth_header.removeprefix("Bearer ").strip())
        if record is None:
            raise HTTPException(status_code=401,
                                detail="invalid or revoked API token")
        if record.get("expired"):
            raise HTTPException(status_code=401, detail="token expired")
        if scope not in record["scopes"]:
            raise HTTPException(
                status_code=403,
                detail=f"this token lacks the {scope!r} scope "
                       f"(has: {record['scopes']})")
        allowed, retry_after = _public_rate_ok(record["token_hash"])
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded "
                       f"({public_rate_per_min:g} requests/minute)",
                headers={"Retry-After": str(retry_after)})
        return record

    def _public_decision_row(run: RunRecord) -> dict:
        rec = run.recommendation
        return {
            "run_id": run.run_id,
            "symbol": run.symbol,
            "started_at": run.started_at.isoformat(),
            "timeframe": run.timeframe,
            "action": rec.action.value if rec else None,
            "rejected_at": run.rejection and run.rejection.get("stage"),
            "confidence": rec.confidence if rec else None,
            # P3-07 provenance stamp; None on pre-stamp runs
            "versions": run.versions,
        }

    # async on purpose (the R2.7 lesson): pure in-memory reads must not
    # queue behind vendor-bound threadpool handlers — and single-threaded
    # event-loop execution makes the rate-limit bucket update atomic.
    @app.get("/public/v1/decisions")
    async def public_decisions(request: Request, limit: int = 50) -> dict:
        _public_auth(request, "read:decisions")
        limit = max(1, min(int(limit), 500))
        return {"decisions": [_public_decision_row(run)
                              for run in reversed(state.runs[-limit:])]}

    @app.get("/public/v1/decisions/{run_id}")
    async def public_decision(run_id: str, request: Request) -> dict:
        _public_auth(request, "read:decisions")
        run = _run_or_404(run_id)
        gates = run.state.get("gate_results") or {}
        row = _public_decision_row(run)
        # gates summary only — verdict + reasons, never the debate record
        row["gates"] = {
            name: {"passed": g.get("passed"),
                   "reasons": list(g.get("reasons") or g.get("issues") or [])}
            for name, g in gates.items() if isinstance(g, dict)
        }
        row["rejection"] = run.rejection
        return row

    @app.get("/public/v1/calibration")
    async def public_calibration(request: Request) -> dict:
        _public_auth(request, "read:calibration")
        return service.calibration_report(state.memory)

    # P4-02 public live track record. Feature-flagged OFF by default:
    # publishing a live performance record has legal exposure (marketing
    # of trading results), so the route only exists when the operator sets
    # PRO_PUBLIC_TRACK_RECORD=1 AFTER counsel signs off — otherwise it
    # 404s before auth, indistinguishable from a route that was never
    # shipped. Same Bearer+scope mechanics as the other public endpoints
    # (scope read:decisions). Payload honesty rules live in (and are
    # tested against) service.public_track_record.
    track_record_enabled = (
        os.environ.get("PRO_PUBLIC_TRACK_RECORD", "0") == "1")

    @app.get("/public/v1/track-record")
    async def public_track_record(request: Request,
                                  limit: int = 100) -> dict:
        if not track_record_enabled:
            # flag off = the page does not exist, not "forbidden"
            raise HTTPException(status_code=404, detail="Not Found")
        _public_auth(request, "read:decisions")
        limit = max(1, min(int(limit), 500))
        return service.public_track_record(
            state.runs, state.memory, limit=limit)

    # P4-03 public marketplace catalogue: PUBLISHED listings only — a row
    # exists here only because it passed service.listing_gate. Exposes the
    # graded record (calibration) + its provenance stamp; NEVER
    # config_json (the paid strategy/prompt artifact stays private) and
    # never the owner's email (a public identity surface is a later
    # slice). Same Bearer+scope+rate-limit mechanics as the other public
    # endpoints (scope read:decisions).
    @app.get("/public/v1/listings")
    async def public_listings(request: Request) -> dict:
        _public_auth(request, "read:decisions")
        store = getattr(state.prefs, "store", None)  # non-None post-auth
        rows = []
        for listing in store.list_listings(status="published"):
            calibration = listing.get("calibration") or {}
            rows.append({
                "id": listing["id"],
                "kind": listing["kind"],
                "title": listing["title"],
                "description": listing["description"],
                "calibration": calibration,
                # P3-07-style provenance: the versions/corpus stamp the
                # graded record was published under
                "versions": calibration.get("versions"),
                "corpus": calibration.get("corpus"),
                "created_at": listing["created_at"],
                "updated_at": listing["updated_at"],
            })
        return {"listings": rows}

    @app.get("/api/journal")
    def journal() -> dict:
        return service.trade_journal(state.memory)

    @app.get("/api/portfolio/stats")
    def portfolio_stats() -> dict:
        """Live-book analytics (trader review): closed-trade performance
        + aggregate open exposure — the real record, not the backtest."""
        base = (state.router.breaker.equity_base
                if state.router is not None
                and getattr(state.router, "breaker", None) is not None
                else 100_000.0)
        perf = service.journal_performance(state.memory, starting_equity=base)
        positions, _ = service.open_positions_view(
            state.router, state.equity, ticks=state.ticks,
            marketdata=state.marketdata,
            memory=state.memory) if state.router is not None else ([], None)
        max_open = (state.router.limits.max_open_positions
                    if state.router is not None else 3)
        perf["exposure"] = service.portfolio_exposure(
            positions, state.equity, max_open,
            marketdata=state.marketdata)
        return perf

    @app.get("/api/scanner")
    def scanner() -> dict:
        """Deterministic universe scan (trader review: 'today's best
        opportunity immediately'): the pipeline's zero-LLM prepare-stage
        features across every tradeable symbol, ranked. Running the full
        agent debate stays a deliberate, priced action."""
        from tradingagents.contracts import utc_now
        from tradingagents.pro.analytics.features import (
            classify_regime,
            close_zscore,
            realized_volatility,
            trend_slope,
        )

        rows = []
        for spec in state.marketdata.registry.values():
            if not spec.tradeable:
                continue
            try:
                tf = "1h" if any(t.value == "1h" for t in spec.timeframes) else "1d"
                bars = state.marketdata.get_bars(spec.symbol, tf, limit=120)
                if len(bars) < 60:
                    continue
                regime = classify_regime(bars)
                slope, r2 = trend_slope(bars)
                zscore = close_zscore(bars)
                vol = realized_volatility(bars)
                # setup score: stretched price (|z|) in a directional regime
                # scores highest; pure chop scores lowest. Deterministic and
                # explainable — not a prediction.
                regime_weight = {
                    "trending_up": 1.0, "trending_down": 1.0,
                    "high_volatility": 0.8, "crisis": 0.8,
                    "low_volatility": 0.5, "ranging": 0.4,
                }.get(regime.value, 0.3)
                score = round((abs(zscore) + abs(slope) * 50) * regime_weight, 2)
                rows.append({
                    "symbol": spec.symbol,
                    "timeframe": tf,
                    "regime": regime.value,
                    "trend_slope": slope,
                    "trend_r2": r2,
                    "zscore": zscore,
                    "realized_vol": vol,
                    "last_close": bars[-1].close,
                    "score": score,
                })
            except Exception:  # one degraded vendor never blanks the scan
                continue
        rows.sort(key=lambda r: r["score"], reverse=True)
        return {"rows": rows, "as_of": utc_now().isoformat()}

    @app.get("/api/calibration/summary")
    def calibration_summary() -> dict:
        return service.brier_summary(state.memory)

    @app.post("/api/calibration/backfill")
    def calibration_backfill() -> dict:
        """Retro-score stored REAL runs against subsequent bars so the
        calibration chart accrues (trader review: 'twenty scored trades').
        Idempotent; provenance-tagged; excluded from the blotter."""
        from tradingagents.contracts import Timeframe
        from tradingagents.pro.analytics.retro import backfill_outcomes

        def bars_for(run):
            tf = Timeframe(run.timeframe or "1h")
            bars = state.marketdata.get_bars(run.symbol, tf.value, limit=1000)
            return [b for b in bars if b.start > run.started_at]

        service_obj = getattr(state.trigger, "service", None)
        open_rec_ids = {
            pos.recommendation.id
            for pos in getattr(service_obj, "open_positions", {}).values()
            if getattr(pos, "recommendation", None) is not None
        }
        result = backfill_outcomes(state.runs, state.memory, bars_for,
                                   open_rec_ids=open_rec_ids)
        return result

    @app.get("/api/risk/budget")
    def risk_budget() -> dict:
        budget = service.risk_budget(state.router)
        if budget.get("attached"):
            trigger = state.trigger
            budget["orders_today"] = getattr(
                getattr(trigger, "service", None), "_orders_today", None)
        return budget

    @app.get("/api/backtest")
    def backtest() -> dict:
        return service.backtest_view(state.backtest, state.monte_carlo)

    import threading as _bt_threading

    _backtest_lock = _bt_threading.Lock()

    @app.post("/api/backtest/run")
    async def run_backtest(request: Request) -> JSONResponse:
        """Start an interactive backtest as a background job (returns 202 +
        job_id). Progress + trades stream over /api/stream (backtest_progress
        / backtest_trade / backtest_done). Deterministic by default (scripted
        no-cost LLM — mechanics, not model skill); ``use_llm`` runs the real
        pipeline from .env keys (costs money, capped, requires confirm_cost).
        Uses an ISOLATED memory so simulations never touch the live record."""
        from pydantic import ValidationError

        from tradingagents.pro.dashboard import backtest_job as btjob

        body = await request.json()
        try:
            req = btjob.BacktestRunRequest.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        params = req.model_dump()
        # validate + cost-gate before taking the lock or spawning a thread
        try:
            btjob.resolve_request(state.marketdata, params)
        except btjob._CostConfirmationRequired as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "cost_confirmation_required",
                        "estimate": exc.estimate},
            ) from exc
        except (ValueError, md.UnknownSymbolError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not _backtest_lock.acquire(blocking=False):
            raise HTTPException(status_code=409,
                                detail="a backtest is already running")
        job = btjob.new_job(params)
        state.backtest_job = job

        def work():
            try:
                btjob.run_job(state, job, params)
            finally:
                _backtest_lock.release()

        _bt_threading.Thread(target=work, name="backtest-run",
                             daemon=True).start()
        return JSONResponse({"job_id": job.id, "status": "started"},
                            status_code=202)

    @app.post("/api/backtest/cancel")
    def cancel_backtest() -> dict:
        """Stop the in-flight run; the partial (trades, equity, decisions so
        far) is saved to the run history labeled ``cancelled``."""
        job = state.backtest_job
        if job is None or job.status != "running":
            raise HTTPException(status_code=409, detail="no backtest running")
        job.cancel.set()
        return {"status": "cancelling", "job_id": job.id}

    @app.post("/api/backtest/optimize")
    async def optimize_backtest(request: Request) -> JSONResponse:
        """Grid-search a strategy's parameters as a background job (202 +
        job_id). Each trial is a child backtest on the same window; the result
        carries the overfitting guards (deflated Sharpe, PBO) + a verdict.
        Shares the single backtest worker (409 if a run/optimization is
        already in flight); large grids need confirm_cost (400 + estimate)."""
        from pydantic import ValidationError

        from tradingagents.pro.dashboard import backtest_job as btjob

        body = await request.json()
        try:
            req = btjob.OptimizeRequest.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        params = req.model_dump()
        try:
            btjob.resolve_optimize_request(state.marketdata, params)
        except btjob._CostConfirmationRequired as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "cost_confirmation_required",
                        "estimate": exc.estimate}) from exc
        except (ValueError, md.UnknownSymbolError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not _backtest_lock.acquire(blocking=False):
            raise HTTPException(status_code=409,
                                detail="a backtest or optimization is already running")
        job = btjob.new_job(params)
        state.backtest_opt_job = job

        def work():
            try:
                btjob.run_optimization_job(state, job, params)
            finally:
                _backtest_lock.release()

        _bt_threading.Thread(target=work, name="backtest-optimize",
                             daemon=True).start()
        return JSONResponse({"job_id": job.id, "status": "started"},
                            status_code=202)

    @app.post("/api/backtest/portfolio")
    async def portfolio_backtest(request: Request) -> JSONResponse:
        """Run a multi-symbol backtest as a background job (202 + job_id): one
        native strategy trades a basket on a shared broker whose caps bind
        across the whole portfolio. Shares the single backtest worker (409 if
        a run/optimization is already in flight); large baskets need
        confirm_cost (400 + estimate). The result is a normal run record, so
        it appears in Saved runs and the result view renders it."""
        from pydantic import ValidationError

        from tradingagents.pro.dashboard import backtest_job as btjob

        body = await request.json()
        try:
            req = btjob.PortfolioRunRequest.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        params = req.model_dump()
        try:
            btjob.resolve_portfolio_request(state.marketdata, params)
        except btjob._CostConfirmationRequired as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "cost_confirmation_required",
                        "estimate": exc.estimate}) from exc
        except (ValueError, md.UnknownSymbolError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not _backtest_lock.acquire(blocking=False):
            raise HTTPException(status_code=409,
                                detail="a backtest or optimization is already running")
        job = btjob.new_job(params)
        state.backtest_job = job

        def work():
            try:
                btjob.run_portfolio_job(state, job, params)
            finally:
                _backtest_lock.release()

        _bt_threading.Thread(target=work, name="backtest-portfolio",
                             daemon=True).start()
        return JSONResponse({"job_id": job.id, "status": "started"},
                            status_code=202)

    @app.get("/api/backtest/optimize/job")
    def optimize_job_status() -> dict:
        job = state.backtest_opt_job
        return job.snapshot() if job is not None else {"status": "idle"}

    @app.get("/api/backtest/optimizations")
    def optimizations() -> dict:
        return {"optimizations": state.backtest_optimizations.list()}

    @app.get("/api/backtest/optimizations/{opt_id}")
    def optimization(opt_id: str) -> dict:
        record = state.backtest_optimizations.get(opt_id)
        if record is None:
            raise HTTPException(status_code=404, detail="optimization not found")
        return record

    @app.post("/api/backtest/bakeoff")
    async def bakeoff(request: Request) -> JSONResponse:
        """Run several strategies over the SAME window and rank them by an
        honest objective (a strategy 'bake-off'). Empty strategy_ids = the
        whole registered library. Shares the single backtest worker (409 if a
        run/optimization/bake-off is already in flight); a large basket needs
        confirm_cost (400 + estimate)."""
        from pydantic import ValidationError

        from tradingagents.pro.dashboard import backtest_job as btjob

        body = await request.json()
        try:
            req = btjob.BakeoffRequest.model_validate(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        params = req.model_dump()
        try:
            btjob.resolve_bakeoff_request(state.marketdata, params)
        except btjob._CostConfirmationRequired as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": "cost_confirmation_required",
                        "estimate": exc.estimate}) from exc
        except (ValueError, md.UnknownSymbolError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if not _backtest_lock.acquire(blocking=False):
            raise HTTPException(status_code=409,
                                detail="a backtest or optimization is already running")
        job = btjob.new_job(params)
        state.backtest_bakeoff_job = job

        def work():
            try:
                btjob.run_bakeoff_job(state, job, params)
            finally:
                _backtest_lock.release()

        _bt_threading.Thread(target=work, name="backtest-bakeoff",
                             daemon=True).start()
        return JSONResponse({"job_id": job.id, "status": "started"},
                            status_code=202)

    @app.get("/api/backtest/bakeoff/job")
    def bakeoff_job_status() -> dict:
        job = state.backtest_bakeoff_job
        return job.snapshot() if job is not None else {"status": "idle"}

    @app.get("/api/backtest/bakeoffs")
    def bakeoffs() -> dict:
        return {"bakeoffs": state.backtest_bakeoffs.list()}

    @app.get("/api/backtest/bakeoffs/{bakeoff_id}")
    def bakeoff_record(bakeoff_id: str) -> dict:
        record = state.backtest_bakeoffs.get(bakeoff_id)
        if record is None:
            raise HTTPException(status_code=404, detail="bakeoff not found")
        return record

    @app.get("/api/backtest/job")
    def backtest_job_status() -> dict:
        job = state.backtest_job
        return job.snapshot() if job is not None else {"status": "idle"}

    @app.get("/api/backtest/strategies")
    def backtest_strategies() -> dict:
        """Registered strategies + their declared parameter schema (track T1),
        so the UI can render the strategy picker + param inputs dynamically."""
        from tradingagents.pro.backtest import list_strategies

        # every registered strategy (rules_v1, trend_following_v1, …) plus the
        # job-built pipeline_llm (needs the model bundle, not the registry — it
        # shares rules_v1's declared knobs).
        out = [{"id": s.id, "description": s.description, "params": s.params}
               for s in list_strategies()]
        rules = next((s for s in list_strategies() if s.id == "rules_v1"), None)
        out.append({
            "id": "pipeline_llm",
            "description": "Real multi-agent LLM pipeline on the operator's "
                           "model bundle — costs money, measures model skill. "
                           "Same risk geometry and gates as rules_v1.",
            "params": rules.params if rules is not None else [],
        })
        return {"strategies": out}

    @app.get("/api/backtest/presets")
    def backtest_presets() -> dict:
        """Strategy-Lab tuned presets (guard-validated params per
        strategy/symbol/timeframe), so the UI can flag when a tuned preset is
        available for the current selection and offer to apply it."""
        from tradingagents.pro.backtest import list_presets

        return {"presets": list_presets()}

    @app.get("/api/backtest/runs")
    def backtest_runs() -> dict:
        return {"runs": state.backtest_runs.list()}

    @app.get("/api/backtest/runs/{run_id}")
    def backtest_run(run_id: str) -> dict:
        record = state.backtest_runs.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown backtest run")
        return record

    @app.get("/api/backtest/runs/{run_id}/artifacts/{name}")
    def backtest_artifact(run_id: str, name: str):
        """Full-fidelity bulk data for one run (every equity point / trade /
        decision) — streamed from the per-run artifact files, never embedded
        in the record."""
        from fastapi.responses import FileResponse

        from tradingagents.pro.dashboard.backtest_artifacts import (
            ARTIFACT_NAMES,
            RunArtifacts,
        )
        if name not in ARTIFACT_NAMES:
            raise HTTPException(status_code=404, detail="unknown artifact")
        path = RunArtifacts(run_id).path(name)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(path, media_type="application/json")

    @app.get("/api/backtest/runs/{run_id}/report.html")
    def backtest_report_html(run_id: str):
        """Self-contained institutional report (opt-in ``emit_report`` runs);
        404 when the run was executed without report generation."""
        from fastapi.responses import FileResponse

        from tradingagents.pro.dashboard.backtest_artifacts import RunArtifacts
        path = RunArtifacts(run_id).dir / "report.html"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="report not found")
        return FileResponse(path, media_type="text/html")

    @app.get("/api/backtest/runs/{run_id}/report.pdf")
    def backtest_report_pdf(run_id: str):
        from fastapi.responses import FileResponse

        from tradingagents.pro.dashboard.backtest_artifacts import RunArtifacts
        path = RunArtifacts(run_id).dir / "report.pdf"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="report not found")
        return FileResponse(path, media_type="application/pdf")

    @app.get("/api/backtest/runs/{run_id}/charts/{name}")
    def backtest_chart(run_id: str, name: str):
        """One report chart PNG. Name is allowlisted (``[a-z_]+.png``) so it
        can't escape the run's chart directory."""
        import re

        from fastapi.responses import FileResponse

        from tradingagents.pro.dashboard.backtest_artifacts import RunArtifacts
        if not re.fullmatch(r"[a-z_]+\.png", name):
            raise HTTPException(status_code=404, detail="unknown chart")
        path = RunArtifacts(run_id).dir / "charts" / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="chart not found")
        return FileResponse(path, media_type="image/png")

    @app.delete("/api/backtest/runs/{run_id}")
    def delete_backtest_run(run_id: str) -> dict:
        from tradingagents.pro.dashboard.backtest_artifacts import RunArtifacts

        if not state.backtest_runs.delete(run_id):
            raise HTTPException(status_code=404, detail="unknown backtest run")
        RunArtifacts(run_id).delete()
        return {"status": "deleted", "id": run_id}

    @app.get("/api/memory")
    def memory_view() -> dict:
        return service.memory_insights(state.memory)

    @app.get("/api/intel")
    def intel() -> dict:
        return state.intel.snapshot()

    @app.get("/api/intel/correlations")
    def intel_correlations(window: int = 30) -> dict:
        return state.intel.correlations(state.marketdata, window)

    @app.get("/api/calendar")
    def calendar(days: int = 30) -> dict:
        return state.intel.calendar(days)

    @app.get("/api/export/journal.csv")
    def export_journal_csv() -> StreamingResponse:
        import csv
        import io
        from datetime import date

        journal = service.trade_journal(state.memory)

        def rows():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow(["symbol", "action", "regime", "pnl", "won",
                             "closed_at", "mode", "commission",
                             "venue_order_id", "fill_price", "entry_price"])
            yield buffer.getvalue()
            for entry in journal["entries"]:
                buffer.seek(0)
                buffer.truncate(0)
                writer.writerow([entry["symbol"], entry["action"],
                                 entry["regime"], entry["pnl"], entry["won"],
                                 entry["closed_at"], entry.get("mode"),
                                 entry.get("commission"),
                                 entry.get("venue_order_id"),
                                 entry.get("fill_price"),
                                 entry.get("entry_price")])
                yield buffer.getvalue()

        return StreamingResponse(
            rows(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="journal-{date.today():%Y%m%d}.csv"'},
        )

    @app.get("/api/export/report.json")
    def export_report() -> dict:
        import importlib.metadata

        run = state.latest_run()
        reflection = (run.state.get("reflection") or {}) if run else {}
        try:
            app_version = importlib.metadata.version("tradingagents")
        except importlib.metadata.PackageNotFoundError:
            app_version = "dev"
        from tradingagents.contracts import utc_now

        return {
            "generated_at": utc_now().isoformat(),
            "app_version": app_version,
            "overview": service.market_overview(run),
            "recommendation": service.recommendation_view(
                run.recommendation if run else None,
                invalidation=reflection.get("invalidation"),
                rejection=run.rejection if run else None,
            ),
            "status": service.system_status(state.router, state.equity),
            "journal": service.trade_journal(state.memory),
            "backtest": service.backtest_view(state.backtest, state.monte_carlo),
            "agents": service.agent_performance(state.runs, state.memory),
            "memory": service.memory_insights(state.memory),
            "alerts": service.alert_feed(state.runs),
        }

    @app.get("/api/agents")
    def agents() -> dict:
        return service.agent_performance(state.runs, state.memory)

    # SPA fallback: root-level build files (sw.js, manifest, icons) are
    # served as files; client routes (/trade/..., /decisions/...) get
    # index.html; unknown /api paths still 404. Registered last.
    @app.get("/{path:path}", include_in_schema=False)
    def spa_fallback(path: str):
        import mimetypes

        from fastapi.responses import Response

        # P4-02: /public/track-record is the ONE /public path that serves
        # the SPA shell (the pre-auth page lives outside AuthGate) — and
        # only while the track-record flag is on; every other /public path
        # keeps 404ing so unknown API probes never receive HTML.
        if path == "public/track-record" and track_record_enabled:
            pass
        elif (path.startswith(("api/", "public/"))
                or path in ("api", "public", "healthz", "metrics")):
            raise HTTPException(status_code=404, detail=f"no route /{path}")
        if has_spa and path and ".." not in path:
            candidate = static_root / path
            try:
                is_file = candidate.is_file()
            except Exception:
                is_file = False
            if is_file:
                media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
                cache = ("no-cache" if path in ("sw.js", "index.html")
                         else "public, max-age=86400")
                return Response(candidate.read_bytes(), media_type=media_type,
                                headers={"Cache-Control": cache})
        if has_spa:
            return HTMLResponse(spa_index.read_text(encoding="utf-8"),
                                headers={"Cache-Control": "no-cache"})
        return HTMLResponse(_legacy_html())

    return app


def create_default_app():
    """uvicorn --factory entry point with empty state."""
    return create_app()
