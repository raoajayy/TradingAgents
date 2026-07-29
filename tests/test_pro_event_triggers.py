"""P2-06 event-driven triggers: calendar release (T+delay), vol-spike and
gap bars fire a pipeline run through the same run_lock-serialized path as
the hourly loop; per-symbol cooldowns are persisted so restarts never
re-fire; the feature is off by default."""

import threading
import time
from datetime import datetime, timedelta, timezone

from tests.pro_fakes import BASE_TS
from tests.test_pro_e2e_service import LIMITS, ScriptedSnapshots
from tests.test_pro_pipeline_graph import CONFIG, FakePipelineLLM
from tradingagents.contracts import (
    AssetClass,
    EventTriggerConfig,
    OHLCVBar,
    ProConfig,
    Timeframe,
)
from tradingagents.pro.alerting import AlertManager
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

ENABLED_CONFIG = ProConfig(
    asset=AssetClass.GOLD, max_debate_rounds=1,
    event_block_hours=0.0,  # keep triggered runs clear of the event gate
    event_triggers=EventTriggerConfig(enabled=True),
)

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


class CaptureSink:
    def __init__(self):
        self.alerts = []

    def deliver(self, alert):
        self.alerts.append(alert)


def steady_bars(n: int = 16, price: float = 100.0) -> list[OHLCVBar]:
    """n hourly bars with a constant 2.0 high-low range -> ATR_14 == 2.0."""
    return [
        OHLCVBar(timeframe=Timeframe.H1, start=BASE_TS + timedelta(hours=i),
                 open=price, high=price + 1.0, low=price - 1.0, close=price,
                 volume=1_000.0)
        for i in range(n)
    ]


def spike_bars() -> list[OHLCVBar]:
    """Last bar range 20 > 3x ATR(2) -> vol-spike trigger."""
    bars = steady_bars()
    last = bars[-1]
    bars[-1] = last.model_copy(update={"high": 110.0, "low": 90.0})
    return bars


def gap_bars() -> list[OHLCVBar]:
    """Last bar opens 10 above the previous close (> 2x ATR(2)) while its
    own range stays ordinary, so only the gap condition fires."""
    bars = steady_bars()
    last = bars[-1]
    bars[-1] = last.model_copy(update={
        "open": 110.0, "high": 111.0, "low": 109.0, "close": 110.0})
    return bars


def make_service(tmp_path, config=ENABLED_CONFIG, bars=None,
                 calendar_fn=None, sink=None, closes=(130.0, 131.0, 132.0)):
    """Same wiring as tests/test_pro_e2e_service.make_service, plus a tmp
    prefs store (debounce persistence) and an injected bar source so no
    trigger check ever consults real market data."""
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
    kwargs = {"calendar_fn": calendar_fn} if calendar_fn else {}
    service = PaperTradingService(
        FakePipelineLLM(), config, ScriptedSnapshots(list(closes)),
        router=router, memory=memory, dashboard_state=state,
        metrics=MetricsRegistry(),
        alerts=AlertManager(sinks=[sink] if sink else []),
        **kwargs,
    )
    service.event_bars_fn = lambda symbol: list(bars or [])
    return service


class TestDisabledByDefault:
    def test_nothing_fires_when_disabled(self, tmp_path):
        sink = CaptureSink()
        event_at = NOW + timedelta(minutes=30)
        calendar_fn = lambda: {  # noqa: E731
            "release": "CPI", "date": "2026-07-29",
            "at": event_at.isoformat(), "seconds_until": 1800}
        service = make_service(tmp_path, config=CONFIG, bars=spike_bars(),
                               calendar_fn=calendar_fn, sink=sink)
        assert service.check_event_triggers(now=NOW) == []
        assert service.check_event_triggers(
            now=event_at + timedelta(minutes=10)) == []
        assert service.dashboard.recorder.runs == []
        assert sink.alerts == []
        assert service.start_event_trigger_daemon() is None


class TestCalendarTrigger:
    def test_fires_exactly_once_after_t_plus_delay(self, tmp_path):
        sink = CaptureSink()
        event_at = NOW + timedelta(minutes=30)
        calendar_fn = lambda: {  # noqa: E731
            "release": "CPI", "date": "2026-07-29",
            "at": event_at.isoformat(),
            "seconds_until": int((event_at - NOW).total_seconds())}
        service = make_service(tmp_path, calendar_fn=calendar_fn, sink=sink)

        # before the release: remembered, not fired
        assert service.check_event_triggers(now=NOW) == []
        # T+4min: still inside the delay window (default 5min)
        assert service.check_event_triggers(
            now=event_at + timedelta(minutes=4)) == []
        assert service.dashboard.recorder.runs == []

        # T+6min: exactly one run, with event provenance + the alert
        fired = service.check_event_triggers(
            now=event_at + timedelta(minutes=6))
        assert [f["reason"] for f in fired] == ["calendar:CPI"]
        runs = service.dashboard.recorder.runs
        assert len(runs) == 1
        assert runs[0].trigger == "event:calendar:CPI"
        texts = [a.text for a in sink.alerts if a.event == "event_trigger"]
        assert texts == ["event-triggered run: calendar:CPI"]

        # within cooldown AND already fired: quiet
        assert service.check_event_triggers(
            now=event_at + timedelta(minutes=10)) == []
        # even after the cooldown lapses, a fired release never re-fires
        assert service.check_event_triggers(
            now=event_at + timedelta(minutes=90)) == []
        assert len(service.dashboard.recorder.runs) == 1

    def test_restart_with_persisted_state_does_not_refire(self, tmp_path):
        event_at = NOW + timedelta(minutes=30)
        calendar_fn = lambda: {  # noqa: E731
            "release": "NFP", "date": "2026-07-29",
            "at": event_at.isoformat(), "seconds_until": 1800}
        service = make_service(tmp_path, calendar_fn=calendar_fn)
        fired = service.check_event_triggers(
            now=event_at + timedelta(minutes=6))
        assert len(fired) == 1

        # "restart": fresh service over the SAME prefs store
        reborn = make_service(tmp_path, calendar_fn=calendar_fn)
        assert reborn.check_event_triggers(
            now=event_at + timedelta(minutes=7)) == []
        assert reborn.check_event_triggers(
            now=event_at + timedelta(minutes=90)) == []
        assert reborn.dashboard.recorder.runs == []


class TestBarTriggers:
    def test_vol_spike_bar_fires(self, tmp_path):
        sink = CaptureSink()
        service = make_service(tmp_path, bars=spike_bars(), sink=sink)
        fired = service.check_event_triggers(now=NOW)
        assert [f["reason"] for f in fired] == ["vol_spike:XAUUSD"]
        runs = service.dashboard.recorder.runs
        assert len(runs) == 1 and runs[0].trigger == "event:vol_spike:XAUUSD"
        assert any(a.text == "event-triggered run: vol_spike:XAUUSD"
                   for a in sink.alerts)

    def test_gap_bar_fires(self, tmp_path):
        service = make_service(tmp_path, bars=gap_bars())
        fired = service.check_event_triggers(now=NOW)
        assert [f["reason"] for f in fired] == ["gap:XAUUSD"]
        assert service.dashboard.recorder.runs[0].trigger == "event:gap:XAUUSD"

    def test_steady_bars_stay_quiet(self, tmp_path):
        service = make_service(tmp_path, bars=steady_bars())
        assert service.check_event_triggers(now=NOW) == []
        assert service.dashboard.recorder.runs == []

    def test_cooldown_debounces_per_symbol(self, tmp_path):
        service = make_service(tmp_path, bars=spike_bars())
        assert len(service.check_event_triggers(now=NOW)) == 1
        # same condition inside the 60min window: debounced
        assert service.check_event_triggers(
            now=NOW + timedelta(minutes=30)) == []
        assert len(service.dashboard.recorder.runs) == 1
        # a fresh window may fire again (a *new* spike would be real news)
        assert len(service.check_event_triggers(
            now=NOW + timedelta(minutes=61))) == 1

    def test_restart_within_cooldown_does_not_refire(self, tmp_path):
        service = make_service(tmp_path, bars=spike_bars())
        assert len(service.check_event_triggers(now=NOW)) == 1
        reborn = make_service(tmp_path, bars=spike_bars())
        assert reborn.check_event_triggers(
            now=NOW + timedelta(minutes=5)) == []
        assert reborn.dashboard.recorder.runs == []

    def test_daily_order_cap_suppresses_trigger(self, tmp_path):
        service = make_service(tmp_path, bars=spike_bars())
        service._orders_day = NOW.date()
        service._orders_today = service.config.risk.max_orders_per_day
        # utc_now() drives the budget check; align the marker day with it
        from tradingagents.contracts import utc_now

        service._orders_day = utc_now().date()
        assert service.check_event_triggers(now=NOW) == []
        assert service.dashboard.recorder.runs == []


class TestSerialization:
    def test_event_run_waits_on_run_lock(self, tmp_path):
        """An event trigger must queue behind the loop's run_lock, never
        run concurrently."""
        sink = CaptureSink()
        service = make_service(tmp_path, bars=spike_bars(), sink=sink)
        service.run_lock.acquire()
        results = {}
        thread = threading.Thread(
            target=lambda: results.update(
                fired=service.check_event_triggers(now=NOW)))
        thread.start()
        # the alert is emitted just before run_once blocks on the lock —
        # once it lands the trigger thread is at (or past) the lock wait
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not sink.alerts:
            time.sleep(0.01)
        assert sink.alerts, "trigger thread never reached the run"
        time.sleep(0.2)  # give a buggy concurrent run time to record
        assert service.dashboard.recorder.runs == []  # blocked, not running
        service.run_lock.release()
        thread.join(timeout=30)
        assert not thread.is_alive()
        assert len(results["fired"]) == 1
        assert len(service.dashboard.recorder.runs) == 1
        assert service.dashboard.recorder.runs[0].trigger == (
            "event:vol_spike:XAUUSD")
