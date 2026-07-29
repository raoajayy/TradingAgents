"""End-to-end integration: pipeline -> router -> position mgmt -> memory -> views."""

from datetime import timedelta

import pytest

from tests.pro_fakes import BASE_TS
from tests.test_pro_pipeline_graph import CONFIG, FakePipelineLLM, pipeline_snapshot
from tradingagents.contracts import OHLCVBar, RiskLimits, Timeframe
from tradingagents.pro.dashboard.app import DashboardState
from tradingagents.pro.dashboard.service import trade_journal
from tradingagents.pro.execution import (
    VENUES,
    AuditLog,
    CircuitBreaker,
    ExecutionRouter,
    KillSwitch,
    PaperVenueAdapter,
)
from tradingagents.pro.memory import MemoryKind, ProMemory
from tradingagents.pro.observability import MetricsRegistry
from tradingagents.pro.service import PaperTradingService

LIMITS = RiskLimits(max_position_pct_equity=50.0, max_leverage=1.0)


class ScriptedSnapshots:
    """Feeds snapshots whose last close follows a script, so we can steer
    the position through fill -> target."""

    def __init__(self, closes):
        self.closes = list(closes)
        self.i = 0

    def __call__(self):
        base = pipeline_snapshot()
        close = self.closes[min(self.i, len(self.closes) - 1)]
        self.i += 1
        extra_bar = OHLCVBar(
            timeframe=Timeframe.D1,
            start=BASE_TS + timedelta(days=100 + self.i),
            open=close, high=close + 2.0, low=close - 2.0, close=close,
            volume=5_000.0,
        )
        return base.model_copy(update={"bars": [*base.bars, extra_bar]})


def make_service(closes, memory=None, metrics=None) -> PaperTradingService:
    memory = memory if memory is not None else ProMemory()
    router = ExecutionRouter(
        adapter=PaperVenueAdapter(VENUES["mt5"], starting_cash=100_000.0),
        limits=LIMITS,
        kill_switch=KillSwitch(),
        breaker=CircuitBreaker(LIMITS, equity_base=100_000.0),
        audit=AuditLog(),
    )
    return PaperTradingService(
        FakePipelineLLM(), CONFIG, ScriptedSnapshots(closes),
        router=router, memory=memory,
        dashboard_state=DashboardState(memory=memory),
        metrics=metrics or MetricsRegistry(),
    )


class TestEndToEnd:
    def test_full_loop_fill_then_target_exit(self):
        # Fake bars close ~130; ATR levels put stop ~125, final target ~140.
        memory = ProMemory()
        metrics = MetricsRegistry()
        service = make_service([130.0, 150.0], memory=memory, metrics=metrics)

        first = service.run_once()
        assert first["order_status"] == "filled"
        assert "XAUUSD" in service.open_positions
        assert metrics.counter("orders_filled_total") == 1

        second = service.run_once()  # close breaches final target -> exit
        assert second["closed_positions"], "target breach should close the position"
        closed = second["closed_positions"][0]
        assert closed["reason"] == "take_profit" and closed["pnl"] > 0
        # exit-bar cooldown: no immediate re-entry on the bar that closed us
        assert second["order_status"] == "cooldown"
        assert "XAUUSD" not in service.open_positions

        # memory got the outcome; dashboard journal reflects it
        outcomes = memory.records(MemoryKind.OUTCOME)
        assert len(outcomes) == 1
        journal = trade_journal(memory)
        assert journal["n_trades"] == 1 and journal["total_pnl"] > 0

        # audit trail covers order + close and still verifies
        events = [e["event"] for e in service.router.audit.entries]
        assert "order_result" in events and "position_closed" in events
        assert service.router.audit.verify()

    def test_stop_exit_feeds_circuit_breaker(self):
        service = make_service([130.0, 100.0])  # crash through the stop
        service.run_once()
        summary = service.run_once()
        assert summary["closed_positions"][0]["reason"] == "stop"
        assert summary["closed_positions"][0]["pnl"] < 0
        assert service.router.breaker.consecutive_losses == 1

    def test_snapshot_source_tuple_carries_per_symbol_config(self):
        # Phase 2 rotation: the source may return (snapshot, config) so the
        # hourly loop can rotate assets with per-asset rosters
        from tradingagents.contracts import AssetClass, ProConfig

        service = make_service([130.0])
        base = service.snapshot_source
        crypto_config = ProConfig(asset=AssetClass.ETHEREUM,
                                  max_debate_rounds=1,
                                  models=CONFIG.models)
        service.snapshot_source = lambda: (base(), crypto_config)
        summary = service.run_once()
        run = service.dashboard.recorder.runs[-1]
        assert summary["run_id"] == run.run_id
        # the run recorded under the tuple-supplied config's asset universe
        assert run.state.get("execution_status") is not None

    def test_paper_venue_supports_full_universe(self):
        from tradingagents.pro.execution import VENUES

        spec = VENUES["paper"]
        for symbol in ("XAUUSD", "BTC-USD", "ETH-USD", "SOL-USD"):
            assert spec.venue_symbol(symbol)

    def test_tca_captured_on_entry_fill(self):
        # P1-04: every fill records arrival mid + signed entry slippage
        service = make_service([130.0, 150.0])
        service.run_once()
        assert "XAUUSD" in service.open_positions
        tca = service.open_positions["XAUUSD"].tca
        assert "arrival_mid" in tca and "entry_slippage_bps" in tca
        # slippage rides the outcome once the trade closes
        service.run_once()
        outcome = service.memory.records(MemoryKind.OUTCOME)[-1]
        assert isinstance(outcome.payload.get("tca"), dict)
        assert "entry_slippage_bps" in outcome.payload["tca"]

    def test_funding_accrual_on_open_perp_position(self):
        # P1-03: an open position on a snapshot carrying FUNDING_RATE
        # accrues realized funding; longs pay when the rate is positive
        from datetime import timedelta
        from types import SimpleNamespace

        from tradingagents.contracts import MetricReading, TradeAction
        from tradingagents.pro.service import OpenPosition, PaperTradingService

        rec = SimpleNamespace(action=TradeAction.BUY)
        pos = OpenPosition(recommendation=rec, fill_price=100.0,
                           quantity=2.0, entry_commission=0.0)
        t0 = BASE_TS
        bar = SimpleNamespace(start=t0 + timedelta(hours=8), close=100.0)
        snap = SimpleNamespace(
            as_of=t0,
            onchain=[MetricReading(name="FUNDING_RATE", value=0.01,
                                   unit="percent", as_of=t0,
                                   source="test")],
            macro=[],
        )
        PaperTradingService._accrue_funding(pos, snap, bar)
        # notional 200 x 0.01%/8h x 8h = 0.02 paid by the long
        assert pos.funding_paid == pytest.approx(-0.02)
        # second call, zero elapsed → no double charge
        PaperTradingService._accrue_funding(pos, snap, bar)
        assert pos.funding_paid == pytest.approx(-0.02)

    def test_stale_bars_block_new_entries(self):
        # P1-06: bars older than 3x the driving timeframe at snapshot-build
        # time must fail closed — a vendor stall never trades on old data
        service = make_service([130.0])
        base = service.snapshot_source

        def stale_source():
            snap = base()
            from datetime import timedelta
            return snap.model_copy(
                update={"as_of": snap.bars[-1].start + timedelta(days=10)})

        service.snapshot_source = stale_source
        summary = service.run_once()
        assert summary["order_status"] == "blocked:stale_data"
        assert "XAUUSD" not in service.open_positions

    def test_intel_alert_state_persists_via_prefs(self, tmp_path):
        from tradingagents.pro.dashboard.prefs import PrefsStore

        store = PrefsStore(tmp_path / "prefs.json")
        store.save_intel_alert_state({"funding_extreme": True, "cot_sign": -1})
        reloaded = PrefsStore(tmp_path / "prefs.json")
        assert reloaded.intel_alert_state() == {
            "funding_extreme": True, "cot_sign": -1}

    def test_daily_order_cap_blocks_new_entries(self):
        # paper-mode daily order budget (trader review): live arming had
        # max_orders_per_day; paper had no churn brake at all
        from tradingagents.contracts import utc_now

        service = make_service([130.0])
        service._orders_day = utc_now().date()
        service._orders_today = service.config.risk.max_orders_per_day
        summary = service.run_once()
        assert summary["order_status"] == "blocked:daily_order_cap"
        assert "XAUUSD" not in service.open_positions

    def test_venue_rejection_writes_back_to_run_state(self):
        # phantom-SELL truth gap (2026-07-16 deploy): the venue refusing the
        # order must overwrite run.state["execution_status"] — every dashboard
        # surface reads that field, and the pipeline's optimistic
        # "accepted:paper" painted an executed SELL over a flat book
        service = make_service([130.0])
        service.router.kill_switch.engage("test halt")
        summary = service.run_once()
        assert summary["order_status"] == "rejected"
        run = service.dashboard.recorder.runs[-1]
        status = run.state.get("execution_status") or ""
        assert status.startswith("rejected:order"), status
        assert "kill_switch" in status

    def test_no_double_entry_while_position_open(self):
        service = make_service([130.0, 131.0, 132.0])  # never hits stop/target
        service.run_once()
        second = service.run_once()
        assert second["order_status"] is None  # position open -> no new order
        assert len(service.router.adapter.positions()) == 1

    def test_run_forever_bounded_and_resilient(self):
        service = make_service([130.0, 131.0, 132.0])
        calls = []
        original = service.run_once
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("transient data outage")
            return original()

        service.run_once = flaky
        service.run_forever(interval_seconds=0.0, max_iterations=3,
                            sleep=lambda s: calls.append(s))
        assert state["n"] == 3  # error swallowed, loop continued
        assert service.metrics.counter("iteration_errors_total") == 1
        assert len(calls) == 2  # sleeps between iterations only


class _CaptureSink:
    def __init__(self):
        self.alerts = []

    def deliver(self, alert):
        self.alerts.append(alert)


class _FakeIntel:
    def __init__(self, value):
        self.value = value

    def snapshot(self):
        return {"metrics": [{"name": "DVOL", "value": self.value}]}


class TestConditionAlerts:
    """P2-07: stored condition alerts evaluate beside the operator
    defaults, fire once per crossing, and survive restarts."""

    def _service(self, tmp_path, value: float):
        from tradingagents.pro.alerting import AlertManager
        from tradingagents.pro.dashboard.prefs import PrefsStore

        service = make_service([130.0])
        service.dashboard.prefs = PrefsStore(tmp_path / "prefs.json")
        service.dashboard.intel = _FakeIntel(value)
        sink = _CaptureSink()
        service.alerts = AlertManager(sinks=[sink])
        return service, sink

    @staticmethod
    def _fired(sink):
        return [a for a in sink.alerts if a.event == "condition_alert"]

    def test_gt_fires_once_and_never_refires_across_restart(self, tmp_path):
        service, sink = self._service(tmp_path, value=80.0)
        service.dashboard.prefs.add_condition_alert({
            "metric": "DVOL", "operator": "gt", "threshold": 60.0,
            "note": "implied vol elevated"})
        service._evaluate_intel_alerts()
        service._evaluate_intel_alerts()  # still true -> stays quiet
        fired = self._fired(sink)
        assert len(fired) == 1
        assert "implied vol elevated" in fired[0].text
        assert "BTC implied vol" in fired[0].text  # METRIC_INFO label

        # restart: new service over the SAME prefs file; state persisted
        restarted, sink2 = self._service(tmp_path, value=80.0)
        restarted._evaluate_intel_alerts()
        assert self._fired(sink2) == []

    def test_crossing_operator_needs_a_prior_reading(self, tmp_path):
        service, sink = self._service(tmp_path, value=80.0)
        service.dashboard.prefs.add_condition_alert({
            "metric": "DVOL", "operator": "crosses_above", "threshold": 60.0})
        service._evaluate_intel_alerts()  # first observation: no fire
        assert self._fired(sink) == []
        service.dashboard.intel = _FakeIntel(50.0)
        service._evaluate_intel_alerts()  # below the line
        service.dashboard.intel = _FakeIntel(80.0)
        service._evaluate_intel_alerts()  # the crossing
        assert len(self._fired(sink)) == 1

    def test_inactive_and_deleted_alerts_never_fire(self, tmp_path):
        service, sink = self._service(tmp_path, value=80.0)
        created = service.dashboard.prefs.add_condition_alert({
            "metric": "DVOL", "operator": "gt", "threshold": 60.0,
            "active": False})
        service._evaluate_intel_alerts()
        assert self._fired(sink) == []
        service.dashboard.prefs.delete_condition_alert(created["id"])
        service._evaluate_intel_alerts()
        assert self._fired(sink) == []
        # deleted alerts leave no crossing state behind
        assert not any(k.startswith("cond:") for k in service._intel_state)


class TestBenchmarks:
    """Loose performance guards: catch order-of-magnitude regressions, not
    micro-variance. Fake-LLM pipeline runs are pure orchestration cost."""

    def test_pipeline_run_under_two_seconds(self):
        import time

        llm = FakePipelineLLM()
        snapshot = pipeline_snapshot()
        start = time.perf_counter()
        from tradingagents.pro.pipeline import run_pipeline

        run_pipeline(llm, CONFIG, snapshot)
        elapsed = time.perf_counter() - start
        assert elapsed < 2.0, f"pipeline orchestration took {elapsed:.2f}s"

    def test_memory_retrieval_under_100ms_at_1k_records(self):
        import time

        from tests.test_pro_memory_facade import make_recommendation

        memory = ProMemory()
        for _ in range(500):
            trade = memory.record_trade(make_recommendation())
            memory.close_trade(trade.id, pnl=1.0)
        start = time.perf_counter()
        memory.historical_analogs("XAUUSD trending_up BUY", k=3)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.1, f"retrieval took {elapsed * 1000:.0f}ms"


class TestEventHookAndRecorderCap:
    def test_on_event_receives_run_position_status(self):
        events = []
        memory = ProMemory()
        service = make_service([130.0, 150.0], memory=memory)
        service.on_event = lambda t, d: events.append((t, d))

        service.run_once()
        types = [t for t, _ in events]
        assert types.count("run") == 1 and types.count("status") == 1
        opened = next(d for t, d in events if t == "position")
        assert opened["state"] == "opened" and opened["symbol"] == "XAUUSD"

        events.clear()
        service.run_once()  # target breach -> close
        closed = next(d for t, d in events if t == "position")
        assert closed["state"] == "closed" and closed["pnl"] > 0
        status = next(d for t, d in events if t == "status")
        assert status["attached"] is True and "equity" in status

    def test_raising_consumer_never_breaks_the_loop(self):
        service = make_service([130.0])

        def broken(t, d):
            raise RuntimeError("ui crashed")

        service.on_event = broken
        summary = service.run_once()  # must not raise
        assert summary["order_status"] == "filled"

    def test_recorder_caps_run_history(self):
        from tradingagents.pro.dashboard.recorder import PipelineRecorder

        recorder = PipelineRecorder(max_runs=2)
        ids = [
            recorder.record_run(FakePipelineLLM(), CONFIG, pipeline_snapshot()).run_id
            for _ in range(3)
        ]
        assert [r.run_id for r in recorder.runs] == ids[1:]  # oldest dropped
