"""P3-10 TWAP entry slicing: config validation, slice math, schedule,
per-slice stop protection, mid-window failure handling, TCA aggregation,
env plumbing, exits-never-sliced.

No network anywhere: venues are the paper adapter or transport-stubbed
fakes (Delta / Binance FakeHttp), and where determinism matters the
timer chain runs through a synchronous ``threading.Timer`` stand-in.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_pro_memory_facade import make_recommendation
from tradingagents.contracts import RiskLimits
from tradingagents.pro.execution import (
    VENUES,
    AuditLog,
    CircuitBreaker,
    ExecutionRouter,
    KillSwitch,
    OrderManager,
    PaperVenueAdapter,
    ids,
)


def twap_limits(slices=3, window=6.0, **overrides):
    defaults = {"max_position_pct_equity": 10.0}
    defaults.update(overrides)
    return RiskLimits(twap_slices=slices, twap_window_minutes=window,
                      **defaults)


def sized_rec(quantity=0.6, symbol="XAUUSD", **overrides):
    rec = make_recommendation(symbol=symbol)
    return rec.model_copy(update={
        "position_size": rec.position_size.model_copy(update={
            "quantity": quantity,
            "notional": quantity * 2400.0,
        }),
        "risk_reward": None,
        **overrides,
    })


def make_router(adapter=None, limits=None, **kw) -> ExecutionRouter:
    limits = limits or twap_limits()
    defaults = {
        "adapter": adapter or PaperVenueAdapter(VENUES["paper"]),
        "limits": limits,
        "kill_switch": KillSwitch(),
        "breaker": CircuitBreaker(limits, equity_base=100_000),
        "audit": AuditLog(),
    }
    defaults.update(kw)
    return ExecutionRouter(**defaults)


class FakeAlerts:
    def __init__(self):
        self.emitted = []

    def emit(self, severity, event, text, **labels):
        self.emitted.append({"severity": severity, "event": event,
                             "text": text, **labels})


@pytest.fixture()
def immediate_timers(monkeypatch):
    """Replace threading.Timer (as the router sees it) with a synchronous
    stand-in that records each scheduled delay and fires inline — the whole
    slice chain completes deterministically inside the submit call."""
    import tradingagents.pro.execution.router as router_module

    timers = []

    class ImmediateTimer:
        def __init__(self, interval, function, args=(), kwargs=None):
            self.interval = interval
            self.function = function
            self.args = args
            self.kwargs = kwargs or {}
            self.daemon = False
            timers.append(self)

        def start(self):
            self.function(*self.args, **self.kwargs)

        def cancel(self):
            pass

    monkeypatch.setattr(router_module.threading, "Timer", ImmediateTimer)
    return timers


class TestConfig:
    def test_defaults_keep_slicing_off(self):
        limits = RiskLimits()
        assert limits.twap_slices == 1
        assert limits.twap_window_minutes is None

    def test_slices_without_window_refused(self):
        with pytest.raises(ValidationError, match="twap_window_minutes"):
            RiskLimits(twap_slices=3)

    def test_valid_combo_constructs(self):
        limits = RiskLimits(twap_slices=3, twap_window_minutes=15.0)
        assert limits.twap_slices == 3
        assert limits.twap_window_minutes == 15.0


class TestSliceMath:
    def test_quantities_sum_exactly_with_remainder_on_last(self):
        quantities = ExecutionRouter._twap_slice_quantities(0.5, 3)
        assert len(quantities) == 3
        assert round(sum(quantities), 10) == 0.5
        # equal head slices; the rounding remainder lands on the LAST one
        assert quantities[0] == quantities[1]
        assert quantities[-1] == round(0.5 - 2 * quantities[0], 10)

    def test_even_split_stays_even(self):
        assert ExecutionRouter._twap_slice_quantities(0.6, 3) == [0.2, 0.2, 0.2]

    def test_n1_is_the_whole_order(self):
        assert ExecutionRouter._twap_slice_quantities(0.5, 1) == [0.5]

    def test_applicability_gates(self):
        router = make_router(limits=twap_limits(slices=3, window=6.0))
        rec = sized_rec()
        assert router._twap_applies(rec, "paper") is True
        # canary is already clamped to the venue minimum — never sliced
        assert router._twap_applies(rec, "canary") is False
        # zero quantity (HOLD-shaped) never slices
        hold = rec.model_copy(update={
            "position_size": rec.position_size.model_copy(
                update={"quantity": 0.0, "notional": 0.0})})
        assert router._twap_applies(hold, "paper") is False
        # disabled config never slices
        assert make_router(limits=RiskLimits())._twap_applies(rec, "paper") \
            is False


class TestDisabledIsByteIdentical:
    def test_single_order_audit_and_book_unchanged(self):
        router = make_router(limits=RiskLimits(max_position_pct_equity=10.0))
        result = router.submit_recommendation(sized_rec(), equity=100_000)
        assert result.status == "filled"
        assert result.filled_quantity == pytest.approx(0.6)
        assert router.local_book["XAUUSD"] == pytest.approx(0.6)
        # exactly the pre-P3-10 audit trail: no twap_* events, no state
        events = [e["event"] for e in router.audit.entries]
        assert events == ["order_received", "order_result"]
        assert router.twap_executions == {}


class TestPaperSlicing:
    def test_three_slices_fill_and_book_sums(self, immediate_timers):
        router = make_router(limits=twap_limits(slices=3, window=6.0))
        rec = sized_rec(quantity=0.6)
        result = router.submit_recommendation(rec, equity=100_000)

        # the caller sees slice 1; the chain completed synchronously
        assert result.status == "filled"
        assert result.filled_quantity == pytest.approx(0.2)
        state = router.twap_executions[rec.id]
        assert state["complete"] and not state["failed"]
        assert len(state["fills"]) == 3
        # book and venue agree on the TOTAL across slices
        assert router.local_book["XAUUSD"] == pytest.approx(0.6)
        [position] = router.adapter.positions()
        assert position.quantity == pytest.approx(0.6)
        assert router.reconcile().in_sync
        events = [e["event"] for e in router.audit.entries]
        assert events.count("twap_slice") == 3
        assert "twap_started" in events and "twap_complete" in events
        assert router.audit.verify()

    def test_slices_spread_evenly_across_window(self, immediate_timers):
        router = make_router(limits=twap_limits(slices=3, window=6.0))
        router.submit_recommendation(sized_rec(), equity=100_000)
        # slice k fires at k * window/n: two timers, each window/n apart
        assert [t.interval for t in immediate_timers] == [120.0, 120.0]

    def test_resubmission_does_not_restart_the_chain(self, immediate_timers):
        router = make_router(limits=twap_limits(slices=3, window=6.0))
        rec = sized_rec()
        first = router.submit_recommendation(rec, equity=100_000)
        again = router.submit_recommendation(rec, equity=100_000)
        assert first.status == "filled" and again.status == "duplicate"
        events = [e["event"] for e in router.audit.entries]
        assert events.count("twap_started") == 1
        assert router.local_book["XAUUSD"] == pytest.approx(0.6)

    def test_arrival_prices_come_from_the_injected_source(
            self, immediate_timers):
        router = make_router(limits=twap_limits(slices=3, window=6.0))
        prices = iter([2400.0, 2410.0, 2390.0])
        router.twap_price_fn = lambda symbol: next(prices)
        rec = sized_rec()
        router.submit_recommendation(rec, equity=100_000)
        arrivals = [f["arrival_mid"]
                    for f in router.twap_executions[rec.id]["fills"]]
        assert arrivals == [2400.0, 2410.0, 2390.0]


class TestMidWindowFailure:
    def test_failed_slice_alerts_and_leaves_protected_partial(
            self, immediate_timers):
        class FlakyAdapter(PaperVenueAdapter):
            def __init__(self):
                super().__init__(VENUES["paper"])
                self.submissions = 0

            def submit(self, order):
                self.submissions += 1
                if self.submissions >= 2:
                    from tradingagents.pro.execution import AdapterError

                    raise AdapterError("venue down mid-window")
                return super().submit(order)

        adapter = FlakyAdapter()
        alerts = FakeAlerts()
        router = make_router(adapter=adapter,
                             limits=twap_limits(slices=3, window=6.0),
                             max_retries=0)
        router.alerts = alerts
        rec = sized_rec(quantity=0.6)
        result = router.submit_recommendation(rec, equity=100_000)

        assert result.status == "filled"  # slice 1 landed
        state = router.twap_executions[rec.id]
        assert state["failed"] and state["complete"]
        assert len(state["fills"]) == 1
        # only the filled slice is on the book — and it IS on the book
        # (protected via the paper venue's bar-close management)
        assert router.local_book["XAUUSD"] == pytest.approx(0.2)
        [position] = adapter.positions()
        assert position.quantity == pytest.approx(0.2)
        [alert] = alerts.emitted
        assert alert["event"] == "twap_slice_failed"
        assert alert["severity"] == "critical"
        assert "2 slice(s) unplaced" in alert["text"]
        events = [e["event"] for e in router.audit.entries]
        assert "twap_slice_failed" in events

    def test_kill_switch_mid_window_stops_the_chain(self, monkeypatch):
        import tradingagents.pro.execution.router as router_module

        router = make_router(limits=twap_limits(slices=3, window=6.0))
        alerts = FakeAlerts()
        router.alerts = alerts

        class EngagingTimer:
            def __init__(self, interval, function, args=(), kwargs=None):
                self.function, self.args = function, args
                self.daemon = False

            def start(self):
                router.kill_switch.engage("operator halt")
                self.function(*self.args)

        monkeypatch.setattr(router_module.threading, "Timer", EngagingTimer)
        rec = sized_rec(quantity=0.6)
        router.submit_recommendation(rec, equity=100_000)
        state = router.twap_executions[rec.id]
        assert state["failed"] and len(state["fills"]) == 1
        assert router.local_book["XAUUSD"] == pytest.approx(0.2)
        assert alerts.emitted[0]["event"] == "twap_slice_failed"


class TestPerSliceProtection:
    """Documented per-venue rule: no venue can rest a stop for unfilled
    quantity (reduce-only requires a position behind it), so every slice
    carries its own protection."""

    def test_synthetic_venue_places_one_stop_per_slice(self, tmp_path,
                                                       immediate_timers):
        # async venue WITHOUT native brackets -> OMS synthetic stops
        from tests.test_pro_execution_conformance import CREDS, FakeDeltaHttp
        from tests.test_pro_oms import _NoBracketDelta

        fake = FakeDeltaHttp()
        adapter = _NoBracketDelta(CREDS, http=fake, max_read_retries=0)
        adapter.instruments.refresh()
        oms = OrderManager(adapter, journal_path=tmp_path / "journal.jsonl")
        oms.recover()
        router = make_router(limits=twap_limits(slices=2, window=6.0),
                             protection_mode="venue_bracket")
        router.oms = oms
        rec = sized_rec(quantity=0.004, symbol="BTC-USD")
        result = router.submit_recommendation(rec, equity=100_000)
        assert result.status in ("filled", "submitted")

        # both slice entries exist as their own bracket units
        entries = [o for o in oms.orders.values() if o.leg == ids.ENTRY]
        assert len(entries) == 2
        # settle the entries on the venue, then poll: the OMS places one
        # synthetic reduce-only stop PER SLICE, sized to that slice
        for order in entries:
            fake.settle(order.client_order_id)
        oms.poll()
        stops = [o for o in oms.orders.values() if o.leg == ids.STOP]
        assert len(stops) == 2
        assert all(s.spec.reduce_only for s in stops)
        assert sorted(s.spec.quantity for s in stops) == sorted(
            e.filled_quantity for e in entries)
        assert sum(s.spec.quantity for s in stops) == pytest.approx(0.004)

    def test_binance_bracket_covers_every_slice(self, tmp_path, monkeypatch,
                                                immediate_timers):
        # native-bracket venue: place_order runs entry + reduce-only
        # STOP_MARKET as one unit — per slice, no naked entry ever
        from tests.test_pro_live_binance import (
            EXCHANGE_INFO,
            FakeHttp,
            FakeResponse,
            fill_response,
        )
        from tradingagents.pro.execution.adapters.binance_futures import (
            TESTNET_BASE,
            BinanceFuturesAdapter,
        )

        monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "k")
        monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "s")
        http = FakeHttp()
        http.routes[("POST", "/fapi/v1/order")] = fill_response
        http.routes[("GET", "/fapi/v1/exchangeInfo")] = FakeResponse(
            200, EXCHANGE_INFO)
        http.routes[("GET", "/fapi/v2/positionRisk")] = FakeResponse(200, [])
        http.routes[("GET", "/fapi/v2/account")] = FakeResponse(200, {
            "totalMarginBalance": "100000", "availableBalance": "100000"})
        adapter = BinanceFuturesAdapter(
            base_url=TESTNET_BASE, armed_fn=lambda: True,
            max_order_notional=1_000.0, http=http)
        live_oms = OrderManager(adapter,
                                journal_path=tmp_path / "live.jsonl")
        live_oms.recover()
        router = make_router(limits=twap_limits(slices=2, window=6.0))
        router.live_oms = live_oms
        router.arming = SimpleNamespace(effective_tier=lambda s: "live")

        rec = sized_rec(quantity=0.004, symbol="BTC-USD")
        result = router.submit_recommendation(rec, equity=100_000)
        assert result.status == "filled"

        posts = http.posts()
        markets = [p for p in posts if p["params"].get("type") == "MARKET"
                   and "reduceOnly" not in p["params"]]
        stops = [p for p in posts
                 if p["params"].get("type") == "STOP_MARKET"]
        assert len(markets) == 2 and len(stops) == 2
        # each stop is reduce-only and sized to ITS slice
        for stop in stops:
            assert stop["params"]["reduceOnly"] == "true"
            assert float(stop["params"]["quantity"]) == pytest.approx(0.002)
        # ordering: every entry is followed by its stop before the next
        # slice's entry (entry+stop as one unit, per slice)
        sequence = [p["params"].get("type") for p in posts
                    if p["params"].get("type") in ("MARKET", "STOP_MARKET")
                    and "reduceOnly" not in p["params"]
                    or p["params"].get("type") == "STOP_MARKET"]
        assert sequence[:2] == ["MARKET", "STOP_MARKET"]

    def test_entry_coid_matches_first_slice(self):
        router = make_router(limits=twap_limits(slices=3, window=6.0))
        rec = sized_rec(quantity=0.6)
        child = router._twap_child(rec, 0, 0.2)
        expected = ids.client_order_id(child.id, ids.decision_hash(child),
                                       ids.ENTRY)
        assert router.entry_coid(rec) == expected
        # disabled: falls back to the full recommendation's coid
        plain = make_router(limits=RiskLimits())
        assert plain.entry_coid(rec) == ids.client_order_id(
            rec.id, ids.decision_hash(rec), ids.ENTRY)


class TestTcaSummary:
    def _state(self, side="BUY", fills=None):
        return {
            "recommendation_id": "r1", "symbol": "XAUUSD", "side": side,
            "slices": 3, "window_minutes": 30.0, "total_quantity": 0.3,
            "quantities": [0.1, 0.1, 0.1], "fills": fills or [],
            "next_slice": 3, "failed": False, "complete": True,
            "timer": None,
        }

    @staticmethod
    def _fill(index, arrival, price, quantity=0.1, commission=0.01):
        return {"slice": index, "client_id": f"r1#{index}",
                "arrival_mid": arrival, "fill_price": price,
                "quantity": quantity, "commission": commission,
                "status": "filled", "ts": "t"}

    def test_sliced_vs_single_math(self):
        router = make_router()
        router.twap_executions["r1"] = self._state(fills=[
            self._fill(1, 100.0, 100.2),
            self._fill(2, 100.2, 100.4),
            self._fill(3, 99.8, 99.9),
        ])
        summary = router.twap_summary("r1")
        assert summary["slices_filled"] == 3
        assert summary["filled_quantity"] == pytest.approx(0.3)
        assert summary["commission"] == pytest.approx(0.03)
        wavg = (100.2 + 100.4 + 99.9) / 3
        assert summary["avg_fill_price"] == pytest.approx(wavg)
        assert summary["arrival_mid"] == 100.0
        # sliced: arrival mid of slice 1 -> weighted avg fill
        assert summary["twap_slippage_bps"] == pytest.approx(
            (wavg - 100.0) / 100.0 * 10_000.0)
        # single-order counterfactual: the slice-1 fill for everything
        assert summary["single_order_slippage_bps"] == pytest.approx(20.0)
        assert summary["improvement_bps"] == pytest.approx(
            20.0 - summary["twap_slippage_bps"])
        # per-slice slippage against each slice's OWN arrival
        assert summary["per_slice"][1]["slippage_bps"] == pytest.approx(
            (100.4 - 100.2) / 100.2 * 10_000.0)

    def test_sell_side_sign_flips(self):
        router = make_router()
        router.twap_executions["r1"] = self._state(side="SELL", fills=[
            self._fill(1, 100.0, 99.9),  # sold 10bps below arrival = paid
        ])
        summary = router.twap_summary("r1")
        assert summary["twap_slippage_bps"] == pytest.approx(10.0)

    def test_unknown_recommendation_returns_none(self):
        assert make_router().twap_summary("nope") is None


class TestServiceIntegration:
    """End to end on the paper loop: sliced entry -> position carries the
    across-slices totals -> ONE exit order at close, with the sliced-vs-
    single summary on the journal entry."""

    def _service(self, closes, immediate_timers):
        from tests.test_pro_e2e_service import ScriptedSnapshots
        from tests.test_pro_pipeline_graph import CONFIG, FakePipelineLLM
        from tradingagents.pro.dashboard.app import DashboardState
        from tradingagents.pro.memory import ProMemory
        from tradingagents.pro.observability import MetricsRegistry
        from tradingagents.pro.service import PaperTradingService

        limits = twap_limits(slices=2, window=6.0,
                             max_position_pct_equity=50.0)
        memory = ProMemory()
        router = ExecutionRouter(
            adapter=PaperVenueAdapter(VENUES["mt5"], starting_cash=100_000.0),
            limits=limits,
            kill_switch=KillSwitch(),
            breaker=CircuitBreaker(limits, equity_base=100_000.0),
            audit=AuditLog(),
        )
        service = PaperTradingService(
            FakePipelineLLM(), CONFIG, ScriptedSnapshots(closes),
            router=router, memory=memory,
            dashboard_state=DashboardState(memory=memory),
            metrics=MetricsRegistry(),
        )
        return service, memory

    def test_sliced_entry_single_exit_and_journal_summary(
            self, immediate_timers):
        service, memory = self._service([130.0, 150.0], immediate_timers)
        router = service.router

        first = service.run_once()
        assert first["order_status"] == "filled"
        position = service.open_positions["XAUUSD"]
        state = router.twap_executions[position.recommendation.id]
        assert state["complete"] and len(state["fills"]) == 2
        # the position absorbed BOTH slices: quantity/price/commission are
        # across-slices totals, so P&L stays honest
        assert position.quantity == pytest.approx(
            router.local_book["XAUUSD"])
        assert position.tca["twap"]["slices_filled"] == 2
        assert router.reconcile().in_sync

        close_calls = []
        original_close = router.adapter.close_position

        def counting_close(symbol, reference_price):
            close_calls.append(symbol)
            return original_close(symbol, reference_price)

        router.adapter.close_position = counting_close

        captured = {}
        original_close_trade = memory.close_trade

        def spy_close_trade(record_id, **kwargs):
            captured.update(kwargs.get("details") or {})
            return original_close_trade(record_id, **kwargs)

        memory.close_trade = spy_close_trade

        second = service.run_once()
        [closed] = second["closed_positions"]
        assert closed["reason"] == "take_profit"
        # the exit is ONE reduce order for the whole position — exits and
        # reduce-only orders are never sliced
        assert close_calls == ["XAUUSD"]
        assert "XAUUSD" not in service.open_positions
        # the journal entry carries the sliced-vs-single TCA aggregation
        summary = captured["tca_twap_summary"]
        assert summary["slices_planned"] == 2
        assert summary["slices_filled"] == 2
        assert "twap_slippage_bps" in summary
        assert "single_order_slippage_bps" in summary


class TestEnvPlumbing:
    def test_env_knobs_reach_the_router_limits(self, tmp_path, monkeypatch):
        from tests.test_pro_pipeline_graph import FakePipelineLLM
        from tradingagents.pro.main import build_service

        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "pro.db"))
        monkeypatch.setenv("PRO_TWAP_SLICES", "4")
        monkeypatch.setenv("PRO_TWAP_WINDOW_MIN", "20")
        service, _state = build_service(llm=FakePipelineLLM(),
                                        data_dir=tmp_path)
        assert service.router.limits.twap_slices == 4
        assert service.router.limits.twap_window_minutes == 20.0

    def test_env_unset_keeps_slicing_off(self, tmp_path, monkeypatch):
        from tests.test_pro_pipeline_graph import FakePipelineLLM
        from tradingagents.pro.main import build_service

        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "pro.db"))
        for var in ("PRO_TWAP_SLICES", "PRO_TWAP_WINDOW_MIN"):
            monkeypatch.delenv(var, raising=False)
        service, _state = build_service(llm=FakePipelineLLM(),
                                        data_dir=tmp_path)
        assert service.router.limits.twap_slices == 1
        assert service.router.limits.twap_window_minutes is None
