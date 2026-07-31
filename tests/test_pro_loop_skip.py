"""Unchanged-bar loop skip: the hourly rotation revisits each symbol several
times per driving bar (all snapshot builders run daily-driven), so a loop
run whose last bar has not advanced is pure LLM waste. The service skips it
(persisted per-symbol memory, `runs_skipped_unchanged_total` metric) while
operator/event triggers and PRO_RERUN_UNCHANGED_BARS=1 always run."""

from tests.test_pro_e2e_service import LIMITS, ScriptedSnapshots
from tests.test_pro_pipeline_graph import CONFIG, FakePipelineLLM, pipeline_snapshot
from tradingagents.pro.dashboard.app import DashboardState
from tradingagents.pro.dashboard.prefs import PrefsStore
from tradingagents.pro.execution import (
    VENUES,
    AuditLog,
    CircuitBreaker,
    ExecutionRouter,
    KillSwitch,
    PaperVenueAdapter,
)
from tradingagents.pro.memory import ProMemory
from tradingagents.pro.observability import MetricsRegistry
from tradingagents.pro.service import PaperTradingService


def make_service(tmp_path, source):
    memory = ProMemory()
    state = DashboardState(
        memory=memory,
        prefs=PrefsStore(path=tmp_path / "prefs.json"),
    )
    router = ExecutionRouter(
        adapter=PaperVenueAdapter(VENUES["mt5"], starting_cash=100_000.0),
        limits=LIMITS,
        kill_switch=KillSwitch(),
        breaker=CircuitBreaker(LIMITS, equity_base=100_000.0),
        audit=AuditLog(),
    )
    return PaperTradingService(
        FakePipelineLLM(), CONFIG, source,
        router=router, memory=memory, dashboard_state=state,
        metrics=MetricsRegistry(),
    )


def static_source():
    """Every call returns the SAME snapshot (identical last bar) — the
    rotation revisiting a symbol before its daily bar advances."""
    return pipeline_snapshot


class TestUnchangedBarSkip:
    def test_same_bar_loop_run_is_skipped(self, tmp_path):
        service = make_service(tmp_path, static_source())
        first = service.run_once()
        assert first["run_id"]
        assert len(service.dashboard.recorder.runs) == 1

        second = service.run_once()  # same driving bar → no LLM run
        assert second["skipped"] is True
        assert second["order_status"] == "skipped:unchanged_bar"
        assert second["run_id"] is None
        assert len(service.dashboard.recorder.runs) == 1
        assert service.metrics.counter("runs_skipped_unchanged_total") == 1
        assert service.metrics.counter("runs_total") == 1
        # a skip is a HEALTHY loop iteration: the dead-man heartbeat stays
        # fresh, or an all-skipped stretch would read as a stalled loop
        assert service.metrics.gauge("last_run_ts") > 0

    def test_new_bar_runs(self, tmp_path):
        # ScriptedSnapshots appends a fresh bar per call → never skipped
        service = make_service(tmp_path, ScriptedSnapshots([130.0, 131.0]))
        service.run_once()
        service.run_once()
        assert len(service.dashboard.recorder.runs) == 2
        assert service.metrics.counter("runs_skipped_unchanged_total") == 0

    def test_env_opt_out_restores_reruns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PRO_RERUN_UNCHANGED_BARS", "1")
        service = make_service(tmp_path, static_source())
        service.run_once()
        service.run_once()  # same bar, but the opt-out re-runs it
        assert len(service.dashboard.recorder.runs) == 2
        assert service.metrics.counter("runs_skipped_unchanged_total") == 0

    def test_operator_and_event_triggers_never_skipped(self, tmp_path):
        service = make_service(tmp_path, static_source())
        service.run_once()  # loop run seeds the per-symbol bar memory
        operator = service.run_once(snapshot=pipeline_snapshot(),
                                    trigger="operator")
        assert operator["run_id"]
        event = service.run_once(trigger="event:vol_spike:XAUUSD")
        assert event["run_id"]
        assert len(service.dashboard.recorder.runs) == 3
        assert service.metrics.counter("runs_skipped_unchanged_total") == 0

    def test_skip_memory_survives_restart(self, tmp_path):
        service = make_service(tmp_path, static_source())
        service.run_once()
        # a container restart must not re-spend an LLM run on the same bar
        restarted = make_service(tmp_path, static_source())
        summary = restarted.run_once()
        assert summary["skipped"] is True
        assert len(restarted.dashboard.recorder.runs) == 0
