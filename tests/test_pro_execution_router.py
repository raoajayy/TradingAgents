"""Router + venue adapters: validation, idempotency, retries, reconciliation."""

from datetime import timedelta

import pytest

from tests.test_pro_memory_facade import make_recommendation
from tradingagents.contracts import RiskLimits, TradeAction, utc_now
from tradingagents.pro.execution import (
    VENUES,
    AdapterError,
    AuditLog,
    CircuitBreaker,
    ExecutionNotEnabled,
    ExecutionRouter,
    KillSwitch,
    LiveAdapterStub,
    OrderRequest,
    PaperVenueAdapter,
)

LIMITS = RiskLimits(max_position_pct_equity=10.0, circuit_breaker_consecutive_losses=2)


def sized_rec(quantity=0.5, notional=None, symbol="XAUUSD", **overrides):
    rec = make_recommendation(symbol=symbol)
    return rec.model_copy(update={
        "position_size": rec.position_size.model_copy(update={
            "quantity": quantity,
            "notional": notional if notional is not None else quantity * 2400.0,
        }),
        "risk_reward": None,
        **overrides,
    })


def make_router(adapter=None, **kw) -> ExecutionRouter:
    defaults = {
        "adapter": adapter or PaperVenueAdapter(VENUES["mt5"]),
        "limits": LIMITS,
        "kill_switch": KillSwitch(),
        "breaker": CircuitBreaker(LIMITS, equity_base=100_000),
        "audit": AuditLog(),
    }
    defaults.update(kw)
    return ExecutionRouter(**defaults)


class TestValidationGate:
    def test_hold_is_not_executable(self):
        router = make_router()
        result = router.submit_recommendation(
            make_recommendation(action=TradeAction.HOLD), equity=100_000
        )
        assert result.status == "rejected" and "HOLD" in result.reason

    def test_stale_recommendation_refused(self):
        rec = sized_rec(created_at=utc_now() - timedelta(hours=3))
        result = make_router().submit_recommendation(rec, equity=100_000)
        assert result.status == "rejected" and "old" in result.reason

    def test_oversized_notional_refused(self):
        rec = sized_rec(quantity=20.0, notional=48_000.0)  # cap = 10% of 100k
        result = make_router().submit_recommendation(rec, equity=100_000)
        assert result.status == "rejected" and "exceeds cap" in result.reason

    def test_unsupported_symbol_refused(self):
        rec = sized_rec(symbol="BTC-USD")  # mt5 venue is gold-only
        result = make_router().submit_recommendation(rec, equity=100_000)
        assert result.status == "rejected" and "not supported" in result.reason


class TestSafetyGates:
    def test_kill_switch_blocks_before_adapter(self):
        router = make_router()
        router.kill_switch.engage("halt")
        result = router.submit_recommendation(sized_rec(), equity=100_000)
        assert result.status == "rejected" and "kill_switch" in result.reason
        assert router.adapter.positions() == []

    def test_circuit_breaker_blocks_after_loss_streak(self):
        router = make_router()
        router.record_close("XAUUSD", -100.0)
        router.record_close("XAUUSD", -100.0)  # limit is 2
        result = router.submit_recommendation(sized_rec(), equity=100_000)
        assert result.status == "rejected" and "circuit_breaker" in result.reason
        events = [e["event"] for e in router.audit.entries]
        assert "circuit_breaker_tripped" in events


class TestSubmission:
    def test_paper_fill_updates_book_and_audit(self):
        router = make_router()
        result = router.submit_recommendation(sized_rec(), equity=100_000)
        assert result.status == "filled"
        assert result.fill_price > 2400.0  # BUY pays slippage over reference
        assert router.local_book["XAUUSD"] == pytest.approx(result.filled_quantity)
        events = [e["event"] for e in router.audit.entries]
        assert events == ["order_received", "order_result"]
        assert router.audit.verify()

    def test_resubmission_is_idempotent(self):
        router = make_router()
        rec = sized_rec()
        first = router.submit_recommendation(rec, equity=100_000)
        second = router.submit_recommendation(rec, equity=100_000)
        assert first.status == "filled" and second.status == "duplicate"
        # book must not double-count
        assert router.local_book["XAUUSD"] == pytest.approx(first.filled_quantity)

    def test_transient_failure_retries_without_double_fill(self):
        class FlakyAdapter(PaperVenueAdapter):
            def __init__(self):
                super().__init__(VENUES["mt5"])
                self.attempts = 0

            def submit(self, order: OrderRequest):
                self.attempts += 1
                if self.attempts == 1:
                    raise AdapterError("gateway timeout")
                return super().submit(order)

        adapter = FlakyAdapter()
        router = make_router(adapter=adapter)
        result = router.submit_recommendation(sized_rec(), equity=100_000)
        assert result.status == "filled"
        assert adapter.attempts == 2
        assert len(adapter.positions()) == 1
        events = [e["event"] for e in router.audit.entries]
        assert "submit_retry" in events

    def test_persistent_failure_rejects_after_budget(self):
        class DeadAdapter(PaperVenueAdapter):
            def __init__(self):
                super().__init__(VENUES["mt5"])

            def submit(self, order):
                raise AdapterError("venue down")

        router = make_router(adapter=DeadAdapter(), max_retries=1)
        result = router.submit_recommendation(sized_rec(), equity=100_000)
        assert result.status == "rejected" and "after 2 attempts" in result.reason


class TestVenues:
    def test_every_venue_paper_fills_its_instrument(self):
        cases = {
            "binance": ("BTC-USD", 0.5, "BTCUSDT"),
            "bybit": ("BTC-USD", 0.5, "BTCUSDT"),
            "mt5": ("XAUUSD", 0.5, "XAUUSD"),
            "ibkr": ("XAUUSD", 2.0, "GC"),
            "oanda": ("XAUUSD", 5.0, "XAU_USD"),
        }
        for venue_name, (symbol, quantity, venue_symbol) in cases.items():
            adapter = PaperVenueAdapter(VENUES[venue_name])
            result = adapter.submit(OrderRequest(
                idempotency_key=f"t-{venue_name}", symbol=symbol, side="BUY",
                quantity=quantity, reference_price=2400.0,
            ))
            assert result.status == "filled", (venue_name, result.reason)
            assert result.venue_symbol == venue_symbol

    def test_venue_minimum_quantity_enforced(self):
        adapter = PaperVenueAdapter(VENUES["binance"])
        result = adapter.submit(OrderRequest(
            idempotency_key="tiny", symbol="BTC-USD", side="BUY",
            quantity=0.0001, reference_price=60_000.0,
        ))
        assert result.status == "rejected" and "below venue minimum" in result.reason

    def test_live_stubs_refuse_everything(self):
        stub = LiveAdapterStub(VENUES["binance"])
        with pytest.raises(ExecutionNotEnabled, match="paper-only"):
            stub.submit(OrderRequest(
                idempotency_key="x", symbol="BTC-USD", side="BUY",
                quantity=1.0, reference_price=60_000.0,
            ))
        with pytest.raises(ExecutionNotEnabled):
            stub.positions()


class TestReconciliation:
    def test_clean_book_reconciles(self):
        router = make_router()
        router.submit_recommendation(sized_rec(), equity=100_000)
        report = router.reconcile()
        assert report.in_sync

    def test_venue_drift_is_surfaced_not_adopted(self):
        router = make_router()
        router.submit_recommendation(sized_rec(), equity=100_000)
        # venue loses the position (e.g. liquidation the router never saw)
        router.adapter._positions.clear()
        report = router.reconcile()
        assert not report.in_sync
        assert report.missing_on_venue == ("XAUUSD",)
        # and an unknown position appears on the venue
        from tradingagents.pro.execution import BrokerPosition

        router.adapter._positions["BTC-USD"] = BrokerPosition(
            symbol="BTC-USD", side="BUY", quantity=1.0, avg_price=60_000.0
        )
        report2 = router.reconcile()
        assert "BTC-USD" in report2.unknown_on_venue
        assert router.audit.verify()

    def test_close_feeds_breaker_and_clears_book(self):
        router = make_router()
        router.submit_recommendation(sized_rec(), equity=100_000)
        router.record_close("XAUUSD", pnl=250.0)
        assert "XAUUSD" not in router.local_book
        assert router.breaker.consecutive_losses == 0


class TestDriftResolution:
    """Operator remediation: flatten venue positions the book doesn't know."""

    def test_flattens_only_unknown_positions(self):
        from tradingagents.pro.execution import BrokerPosition

        router = make_router()
        # unknown-position symbols must be closable on the venue; the
        # mt5 fixture venue is gold-only, so widen its universe
        router.adapter.venue.symbol_map.update(
            {"ETH-USD": "ETH-USD", "SOL-USD": "SOL-USD"})
        router.submit_recommendation(sized_rec(), equity=100_000)  # known
        router.adapter._positions["ETH-USD"] = BrokerPosition(
            symbol="ETH-USD", side="BUY", quantity=2.0, avg_price=2500.0)
        router.adapter._positions["SOL-USD"] = BrokerPosition(
            symbol="SOL-USD", side="SELL", quantity=10.0, avg_price=150.0)
        assert not router.reconcile().in_sync

        summary = router.resolve_unknown_positions(
            marks={"ETH-USD": 2600.0}, operator="test")
        assert sorted(summary["flattened"]) == ["ETH-USD", "SOL-USD"]
        assert summary["errors"] == []
        # the known position survives; drift is gone; audit chain intact
        assert router.reconcile().in_sync
        assert "XAUUSD" in {p.symbol for p in router.adapter.positions()}
        assert router.audit.verify()

    def test_per_symbol_errors_do_not_abort(self, monkeypatch):
        from tradingagents.pro.execution import BrokerPosition

        router = make_router()
        router.adapter.venue.symbol_map.update(
            {"ETH-USD": "ETH-USD", "SOL-USD": "SOL-USD"})
        router.adapter._positions["ETH-USD"] = BrokerPosition(
            symbol="ETH-USD", side="BUY", quantity=2.0, avg_price=2500.0)
        router.adapter._positions["SOL-USD"] = BrokerPosition(
            symbol="SOL-USD", side="BUY", quantity=1.0, avg_price=150.0)
        real = router.adapter.close_position

        def flaky(symbol, reference_price):
            if symbol == "ETH-USD":
                raise RuntimeError("venue hiccup")
            return real(symbol, reference_price)

        monkeypatch.setattr(router.adapter, "close_position", flaky)
        summary = router.resolve_unknown_positions()
        assert summary["flattened"] == ["SOL-USD"]
        assert len(summary["errors"]) == 1 and "ETH-USD" in summary["errors"][0]


class TestReconcileResolveEndpoint:
    def _client_with_router(self):
        from fastapi.testclient import TestClient

        from tradingagents.pro.dashboard.app import DashboardState, create_app
        from tradingagents.pro.execution import BrokerPosition
        from tradingagents.pro.memory import ProMemory

        router = make_router()
        router.adapter.venue.symbol_map.update(
            {"ETH-USD": "ETH-USD", "SOL-USD": "SOL-USD"})
        router.adapter._positions["ETH-USD"] = BrokerPosition(
            symbol="ETH-USD", side="BUY", quantity=2.0, avg_price=2500.0)
        state = DashboardState(memory=ProMemory())
        state.router = router  # class-level attr, not a dataclass field
        return TestClient(create_app(state)), router

    def test_requires_typed_confirmation(self):
        client, _ = self._client_with_router()
        assert client.post("/api/reconcile/resolve",
                           json={}).status_code == 422

    def test_resolves_drift_and_reports_in_sync(self):
        client, router = self._client_with_router()
        assert not router.reconcile().in_sync
        response = client.post("/api/reconcile/resolve",
                               json={"confirm": "RESOLVE"})
        assert response.status_code == 200
        body = response.json()
        assert body["flattened"] == ["ETH-USD"]
        assert body["in_sync"] is True

    def test_503_without_router(self):
        from fastapi.testclient import TestClient

        from tradingagents.pro.dashboard.app import DashboardState, create_app
        from tradingagents.pro.memory import ProMemory

        bare = TestClient(create_app(DashboardState(memory=ProMemory())))
        assert bare.post("/api/reconcile/resolve",
                         json={"confirm": "RESOLVE"}).status_code == 503
