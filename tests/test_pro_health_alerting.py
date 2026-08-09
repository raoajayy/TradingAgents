"""Go-live Phase 5: Telegram sink, health model, dead-man, exports."""

import json

import pytest

from tradingagents.pro.alerting import Alert, AlertManager, TelegramAlertSink


class _FakeUrlopen:
    """Records the last urllib request; optionally raises."""

    def __init__(self, raise_exc=None):
        self.calls = []
        self.raise_exc = raise_exc

    def __call__(self, request, timeout=None):
        self.calls.append(request)
        if self.raise_exc:
            raise self.raise_exc

        class _Resp:
            def close(self_inner):
                pass

        return _Resp()


class TestTelegramAlertSink:
    def test_builds_bot_api_request(self, monkeypatch):
        fake = _FakeUrlopen()
        monkeypatch.setattr("urllib.request.urlopen", fake)
        sink = TelegramAlertSink("secret-token", "chat-42")
        sink.deliver(Alert("critical", "kill_switch", "halted"))
        assert len(fake.calls) == 1
        req = fake.calls[0]
        assert "bot" in req.full_url and "sendMessage" in req.full_url
        body = json.loads(req.data)
        assert body["chat_id"] == "chat-42"
        assert "kill_switch" in body["text"]

    def test_min_severity_filters_info(self, monkeypatch):
        fake = _FakeUrlopen()
        monkeypatch.setattr("urllib.request.urlopen", fake)
        TelegramAlertSink("t", "c", min_severity="warning").deliver(
            Alert("info", "feeds", "degraded"))
        assert fake.calls == []

    def test_failure_is_fail_closed_and_redacted(self, monkeypatch):
        fake = _FakeUrlopen(raise_exc=RuntimeError("boom with secret-token in it"))
        monkeypatch.setattr("urllib.request.urlopen", fake)
        sink = TelegramAlertSink("secret-token", "c")
        # the AlertManager isolates sink failures; the token must not leak
        mgr = AlertManager(sinks=[sink])
        mgr.emit("critical", "x", "y")  # must not raise
        with pytest.raises(RuntimeError) as exc:
            sink.deliver(Alert("critical", "x", "y"))
        assert "secret-token" not in str(exc.value)


class TestSinkWiring:
    def test_no_optional_sinks_without_env(self, monkeypatch):
        from tradingagents.pro.dashboard.events import EventBroadcaster
        from tradingagents.pro.main import _build_alert_sinks

        for var in ("PRO_TELEGRAM_BOT_TOKEN", "PRO_TELEGRAM_CHAT_ID",
                    "PRO_ALERT_WEBHOOK_URL"):
            monkeypatch.delenv(var, raising=False)
        sinks = _build_alert_sinks(EventBroadcaster())
        names = {type(s).__name__ for s in sinks}
        assert names == {"LogAlertSink", "BroadcastAlertSink"}

    def test_telegram_and_webhook_added_with_env(self, monkeypatch):
        from tradingagents.pro.dashboard.events import EventBroadcaster
        from tradingagents.pro.main import _build_alert_sinks

        monkeypatch.setenv("PRO_TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("PRO_TELEGRAM_CHAT_ID", "c")
        monkeypatch.setenv("PRO_ALERT_WEBHOOK_URL", "https://example/hook")
        names = {type(s).__name__ for s in _build_alert_sinks(EventBroadcaster())}
        assert "TelegramAlertSink" in names and "WebhookAlertSink" in names

    def test_bell_sink_wired_when_prefs_present(self, tmp_path):
        # review P1.4: the bell read "All clear" through a run start, a
        # completion, and two feed outages — alerts never reached it
        from tradingagents.pro.dashboard.events import EventBroadcaster
        from tradingagents.pro.dashboard.prefs import PrefsStore
        from tradingagents.pro.main import _build_alert_sinks

        prefs = PrefsStore(tmp_path / "prefs.json")
        sinks = _build_alert_sinks(EventBroadcaster(), prefs)
        assert "NotificationSink" in {type(s).__name__ for s in sinks}


class TestBellOnEvent:
    def _state(self, tmp_path):
        from tradingagents.pro.dashboard.events import EventBroadcaster
        from tradingagents.pro.dashboard.prefs import PrefsStore

        class State:
            broadcaster = EventBroadcaster()
            prefs = PrefsStore(tmp_path / "prefs.json")

        return State()

    def test_run_events_land_in_the_bell(self, tmp_path):
        from tradingagents.pro.main import _bell_on_event

        state = self._state(tmp_path)
        on_event = _bell_on_event(state)
        on_event("run", {"symbol": "XAUUSD", "action": "SELL",
                         "run_id": "r1"})
        (note,) = state.prefs.notifications()
        assert note["event"] == "run_complete"
        assert "XAUUSD: SELL" in note["text"]

    def test_rejections_name_their_stage(self, tmp_path):
        from tradingagents.pro.main import _bell_on_event

        state = self._state(tmp_path)
        _bell_on_event(state)("run", {"symbol": "BTC-USD", "action": None,
                                      "rejected_at": "event_gate"})
        (note,) = state.prefs.notifications()
        assert "rejected @ event_gate" in note["text"]

    def test_non_run_events_do_not_touch_the_bell(self, tmp_path):
        from tradingagents.pro.main import _bell_on_event

        state = self._state(tmp_path)
        _bell_on_event(state)("status", {"equity": 1.0})
        assert state.prefs.notifications() == []

    def test_alert_feed_entries_reach_the_bell(self, tmp_path):
        """The dashboard's Alerts panel derives entries from run records and
        persists nothing; the bell reads the persisted ring. They were two
        stores with no bridge, so the bell showed 'No notifications yet'
        while the Alerts panel had a long list of the same events."""
        from tradingagents.pro.dashboard import service as dashboard_service
        from tradingagents.pro.main import _bell_on_event

        class _Run:
            run_id = "r1"
            rejection = {"stage": "risk_gate", "reasons": ["stop too wide"]}
            state = {}

            class started_at:  # noqa: N801 - stub for .isoformat()
                @staticmethod
                def isoformat():
                    return "2026-08-02T10:00:00+00:00"

        state = self._state(tmp_path)
        state.runs = [_Run()]
        # the panel and the bell must agree on what happened
        (panel_alert,) = dashboard_service.alert_feed([_Run()])["alerts"]

        _bell_on_event(state)("run", {"symbol": "XAUUSD", "action": None,
                                      "rejected_at": "risk_gate",
                                      "run_id": "r1"})
        notes = state.prefs.notifications()
        mirrored = [n for n in notes if n["event"] == "alert"]
        assert len(mirrored) == 1
        assert mirrored[0]["severity"] == panel_alert["severity"] == "warning"
        assert mirrored[0]["text"] == panel_alert["text"]
        assert "stop too wide" in mirrored[0]["text"]
        # the run-completion note is still there alongside it
        assert any(n["event"] == "run_complete" for n in notes)

    def test_mirroring_is_idempotent(self, tmp_path):
        """The startup backfill re-offers every historical alert on every
        restart; keyed inserts make that a no-op instead of a duplicate."""
        from tradingagents.pro.main import mirror_alert_feed

        class _Run:
            run_id = "r1"
            rejection = {"stage": "risk_gate", "reasons": ["stop too wide"]}
            state = {}

            class started_at:  # noqa: N801
                @staticmethod
                def isoformat():
                    return "2026-08-02T10:00:00+00:00"

        state = self._state(tmp_path)
        runs = [_Run()]
        assert mirror_alert_feed(state, runs) == 1
        assert mirror_alert_feed(state, runs) == 0  # restart: no duplicates
        assert mirror_alert_feed(state, runs) == 0
        assert len(state.prefs.notifications()) == 1

    def test_a_run_with_no_alerts_adds_no_alert_notes(self, tmp_path):
        from tradingagents.pro.main import _bell_on_event

        class _Run:
            run_id = "r1"
            rejection = None
            state = {}

            class started_at:  # noqa: N801
                @staticmethod
                def isoformat():
                    return "2026-08-02T10:00:00+00:00"

        state = self._state(tmp_path)
        state.runs = [_Run()]
        _bell_on_event(state)("run", {"symbol": "XAUUSD", "action": "BUY",
                                      "run_id": "r1"})
        notes = state.prefs.notifications()
        assert [n["event"] for n in notes] == ["run_complete"]


class TestLiveHealth:
    def _state(self, tmp_path, missing=(), last_run_age=0.0):
        import time

        from tradingagents.pro.dashboard.app import DashboardState
        from tradingagents.pro.memory import ProMemory
        from tradingagents.pro.observability import MetricsRegistry

        state = DashboardState(memory=ProMemory())
        metrics = MetricsRegistry()
        metrics.set_gauge("last_run_ts", time.time() - last_run_age)
        state.metrics = metrics
        return state

    def test_ok_when_clean(self, tmp_path):
        from tradingagents.pro.health import live_health

        report = live_health(self._state(tmp_path))
        assert report.ok

    def test_stale_run_is_degraded(self, tmp_path):
        from tradingagents.pro.health import live_health

        report = live_health(self._state(tmp_path, last_run_age=99_999),
                             max_run_age_seconds=5400)
        assert not report.ok and "run_recency" in report.degraded

    def test_dead_provider_is_unhealthy(self, tmp_path):
        """Production ran for days on a provider refusing 100% of calls with
        HTTP 402 while every health probe stayed green — feeds fine, venue
        reachable, kill switch clear, and run_recency fresh because rejected
        runs still count as loop iterations."""
        from tradingagents.pro.health import live_health

        state = self._state(tmp_path)
        for _ in range(164):
            state.metrics.inc("llm_failures_total", schema="EvidenceDraft")

        report = live_health(state)
        assert not report.ok
        assert "models" in report.degraded
        detail = next(c.detail for c in report.checks if c.name == "models")
        assert "refused every call" in detail and "billing" in detail

    def test_dead_provider_does_not_gate_execution(self, tmp_path):
        """The dead-man switch keys off execution_ok. A model outage must not
        trip it: flattening live positions because the LLM is down converts a
        research outage into forced trading."""
        from tradingagents.pro.health import live_health

        state = self._state(tmp_path)
        for _ in range(50):
            state.metrics.inc("llm_failures_total", schema="EvidenceDraft")

        report = live_health(state)
        assert not report.ok            # visible on /health/live
        assert report.execution_ok      # ...but does not stop trading

    def test_healthy_provider_passes_and_partial_failures_tolerated(self, tmp_path):
        from tradingagents.pro.health import live_health

        state = self._state(tmp_path)
        for _ in range(90):
            state.metrics.inc("llm_calls_total", schema="EvidenceDraft")
        for _ in range(10):  # 10% failure — agents abstain on parse errors
            state.metrics.inc("llm_failures_total", schema="EvidenceDraft")

        report = live_health(state)
        assert report.ok
        assert "models" not in report.degraded

    def test_no_model_check_before_any_call(self, tmp_path):
        """A freshly restarted instance has made no calls; absence of data is
        not evidence of failure."""
        from tradingagents.pro.health import live_health

        report = live_health(self._state(tmp_path))
        assert [c for c in report.checks if c.name == "models"] == []
        assert report.ok

    def test_feeds_only_degradation_keeps_execution_ok(self):
        # the dead-man heartbeat consumes execution_ok: an optional data
        # feed outage (coinmetrics, 2026-08-08) must NOT starve it — it
        # tripped the switch 600s after every armed start while venue/
        # clock/kill-switch were all green
        from tradingagents.pro.health import HealthReport

        report = HealthReport()
        report.add("feeds", False, "degraded: ['coinmetrics_community']")
        report.add("venue", True, "venue reachable")
        report.add("kill_switch", True, "clear")
        report.add("run_recency", True, "last run 60s ago")
        assert not report.ok            # full verdict stays honest
        assert report.execution_ok      # but execution is healthy

    def test_execution_degradation_still_fails_execution_ok(self):
        from tradingagents.pro.health import HealthReport

        report = HealthReport()
        report.add("feeds", True, "all feeds fresh")
        report.add("venue", False, "venue unreachable")
        assert not report.execution_ok

    def test_venue_unreachable_is_degraded(self, tmp_path):
        from tradingagents.pro.health import live_health

        state = self._state(tmp_path)

        class _DeadRouter:
            class adapter:
                @staticmethod
                def account():
                    raise ConnectionError("down")

            class kill_switch:
                engaged = False
                reason = ""

        state.router = _DeadRouter()
        report = live_health(state)
        assert not report.ok and "venue" in report.degraded

    def test_endpoint_503_when_degraded(self, tmp_path):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from tradingagents.pro.dashboard.app import create_app

        state = self._state(tmp_path, last_run_age=99_999)
        client = TestClient(create_app(state))
        resp = client.get("/health/live")
        assert resp.status_code == 503
        assert resp.json()["ok"] is False

    def test_endpoint_200_clean(self, tmp_path):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient

        from tradingagents.pro.dashboard.app import create_app

        client = TestClient(create_app(self._state(tmp_path)))
        assert client.get("/health/live").status_code == 200


class TestDeadManSwitch:
    def test_fresh_heartbeat_no_trip(self):
        from tradingagents.pro.deadman import DeadManSwitch

        healthy = type("H", (), {"ok": True})()
        tripped = []
        dm = DeadManSwitch(lambda: healthy, tripped.append,
                           timeout_seconds=600, now=lambda: 1000.0)
        assert dm.tick(now=2000.0) is False  # healthy refreshes heartbeat
        assert tripped == []

    def test_stale_health_trips_once(self):
        from tradingagents.pro.deadman import DeadManSwitch

        unhealthy = type("H", (), {"ok": False})()
        tripped = []
        dm = DeadManSwitch(lambda: unhealthy, tripped.append,
                           timeout_seconds=600, now=lambda: 1000.0)
        assert dm.tick(now=1000.0) is False   # within timeout
        assert dm.tick(now=1700.0) is True    # 700s > 600s -> trip
        assert dm.tick(now=1800.0) is False   # latched
        assert tripped == ["health unconfirmed for 700s (timeout 600s)"] or \
            len(tripped) == 1

    def test_cancel_resting_orders_action(self, tmp_path):
        from tests.test_pro_execution_conformance import CREDS, FakeDeltaHttp
        from tradingagents.contracts import RiskLimits
        from tradingagents.pro.deadman import cancel_resting_orders
        from tradingagents.pro.execution import (
            AuditLog,
            CircuitBreaker,
            ExecutionRouter,
            KillSwitch,
            OrderManager,
        )
        from tradingagents.pro.execution.adapters.delta import DeltaAdapter

        fake = FakeDeltaHttp()
        adapter = DeltaAdapter(CREDS, http=fake, max_read_retries=0)
        adapter.instruments.refresh()
        limits = RiskLimits()
        audit = AuditLog()
        router = ExecutionRouter(
            adapter=adapter, limits=limits, kill_switch=KillSwitch(),
            breaker=CircuitBreaker(limits, equity_base=10_000.0), audit=audit)
        oms = OrderManager(adapter, journal_path=tmp_path / "j.jsonl",
                           audit=audit)
        oms.recover()
        router.oms = oms
        # a resting limit order (stays open on the fake venue)
        from tradingagents.pro.execution.orders import ExecutionPlan

        oms.execute(ExecutionPlan(
            run_id="r", decision_hash="d" * 64, symbol="BTC-USD", side="BUY",
            quantity=0.01, reference_price=4000.0, order_type="limit",
            limit_price=1000.0))
        resting = [o for o in oms.orders.values()
                   if o.sent and not o.state.terminal]
        assert resting  # something is working on the venue

        cancel_resting_orders(router)("test")
        still_open = [o for o in oms.orders.values()
                      if o.sent and not o.state.terminal]
        assert still_open == []                 # cancelled
        assert router.kill_switch.engaged       # and halted
        assert any(e["event"] == "deadman_tripped" for e in audit.entries)


class TestJournalExportColumns:
    def test_by_mode_and_venue_fields(self):
        # reuse the pipeline's real recommendation so the trade record is
        # contract-valid, then close it with venue details + a mode tag
        from tests.test_pro_pipeline_graph import (
            CONFIG,
            FakePipelineLLM,
            pipeline_snapshot,
            run_pipeline,
        )
        from tradingagents.pro.dashboard.service import trade_journal
        from tradingagents.pro.memory import ProMemory

        memory = ProMemory()
        result = run_pipeline(FakePipelineLLM(), CONFIG, pipeline_snapshot())
        rec = result["recommendation"]
        trade = memory.record_trade(rec, regime=result.get("regime"))
        memory.close_trade(trade.id, pnl=12.0, details={
            "mode": "canary", "commission": 0.5,
            "venue_order_id": "ta-abc", "fill_price": 4010.0,
            "entry_price": 4000.0})
        journal = trade_journal(memory)
        entry = journal["entries"][0]
        assert entry["mode"] == "canary"
        assert entry["commission"] == 0.5
        assert entry["venue_order_id"] == "ta-abc"
        assert journal["by_mode"]["canary"]["n_trades"] == 1
        assert journal["by_mode"]["canary"]["win_rate"] == 1.0


class TestHasLlmKey:
    def test_claude_cli_counts_local_login_as_keyed(self, monkeypatch):
        # local workstations authenticate via `claude login`, not the
        # headless CLAUDE_CODE_OAUTH_TOKEN env — a present binary must not
        # strand the loop in monitor mode
        from tradingagents.pro import main as pro_main

        monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "claude-cli")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/claude")
        assert pro_main.has_llm_key()

    def test_claude_cli_without_binary_or_token_is_keyless(self, monkeypatch):
        from tradingagents.pro import main as pro_main

        monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "claude-cli")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert not pro_main.has_llm_key()


class TestRotationCursor:
    """The rotation cursor must survive restarts.

    It used to be an in-process itertools.cycle while the seen-bar state
    persisted to GCS, so every container restart replayed the roster from
    XAUUSD — already marked seen for the day — and the tail of the roster
    (EURUSD/USDJPY) could go days without ever being reached.
    """

    def _prefs(self, tmp_path):
        from tradingagents.pro.dashboard.prefs import PrefsStore

        return PrefsStore(tmp_path / "prefs.json")

    def test_cursor_advances_and_wraps(self, tmp_path):
        prefs = self._prefs(tmp_path)
        assert [prefs.next_rotation_index(6) for _ in range(8)] == \
            [0, 1, 2, 3, 4, 5, 0, 1]

    def test_cursor_survives_a_restart(self, tmp_path):
        prefs = self._prefs(tmp_path)
        [prefs.next_rotation_index(6) for _ in range(4)]  # consumed 0..3

        restarted = self._prefs(tmp_path)  # fresh process, same store
        assert restarted.next_rotation_index(6) == 4, \
            "a restart must resume the rotation, not replay it from the head"

    def test_roster_shrinking_does_not_index_out_of_range(self, tmp_path):
        prefs = self._prefs(tmp_path)
        [prefs.next_rotation_index(6) for _ in range(6)]
        prefs.next_rotation_index(6)
        assert prefs.next_rotation_index(2) in (0, 1)

    def test_zero_length_roster_is_safe(self, tmp_path):
        assert self._prefs(tmp_path).next_rotation_index(0) == 0


class TestBuildShaProvenance:
    """The audit stamp must describe the code actually running."""

    def test_baked_sha_wins_over_a_stale_env_var(self, tmp_path, monkeypatch):
        from tradingagents.pro import versioning

        baked = tmp_path / "sha"
        baked.write_text("e6582e4", encoding="utf-8")
        monkeypatch.setattr(versioning, "BUILD_SHA_FILE", baked)
        monkeypatch.setenv("GIT_SHA", "071fd14")   # the prod drift, verbatim
        versioning.reset_cache()

        # --update-env-vars MERGES, so the stale env var outlives its image
        assert versioning.git_sha() == "e6582e4"
        versioning.reset_cache()

    def test_env_is_used_when_nothing_is_baked(self, tmp_path, monkeypatch):
        from tradingagents.pro import versioning

        monkeypatch.setattr(versioning, "BUILD_SHA_FILE", tmp_path / "absent")
        monkeypatch.setenv("GIT_SHA", "abc1234")
        versioning.reset_cache()
        assert versioning.git_sha() == "abc1234"
        versioning.reset_cache()

    def test_unknown_baked_value_falls_back_to_env(self, tmp_path, monkeypatch):
        from tradingagents.pro import versioning

        baked = tmp_path / "sha"
        baked.write_text("unknown", encoding="utf-8")  # ARG default
        monkeypatch.setattr(versioning, "BUILD_SHA_FILE", baked)
        monkeypatch.setenv("GIT_SHA", "abc1234")
        versioning.reset_cache()
        assert versioning.git_sha() == "abc1234"
        versioning.reset_cache()
