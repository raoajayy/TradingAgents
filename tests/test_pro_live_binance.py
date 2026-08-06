"""Binance FUTURES testnet adapter (P3-01) — transport-stubbed.

Nothing here touches a network: every test runs against ``FakeHttp``.
The invariants under test are the pilot's safety story: HMAC signing,
call-time credentials, arming gating orders (never reads or flattens),
the public clock probe, entry+stop atomicity,
stop-failure -> flatten + alert, the hard notional cap, and the
reduce-only ladder.
"""

import time
from urllib.parse import parse_qsl, urlsplit

import pytest

from tradingagents.pro.execution import AuditLog, KillSwitch, OrderManager
from tradingagents.pro.execution.adapters.binance_auth import (
    sign_query,
    signed_query,
)
from tradingagents.pro.execution.adapters.binance_futures import (
    PROD_BASE,
    TESTNET_BASE,
    BinanceFuturesAdapter,
)
from tradingagents.pro.execution.interface import (
    AdapterError,
    BracketSpec,
    ExecutionNotEnabled,
    OrderSpec,
    OrderState,
)

# Binance API docs' published signing example (spot and futures share the
# scheme): known key/secret/query -> known signature.
DOCS_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
DOCS_QUERY = ("symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1"
              "&price=0.1&recvWindow=5000&timestamp=1499827319559")
DOCS_SIGNATURE = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"

EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": venue,
            "filters": [
                {"filterType": "LOT_SIZE", "stepSize": "0.001",
                 "minQty": "0.001"},
                {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            ],
        }
        for venue in ("BTCUSDT", "ETHUSDT")
    ]
}


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.headers = {}
        self._payload = {} if payload is None else payload

    def json(self):
        return self._payload


class FakeHttp:
    """Records every request; dispatches on (method, path). A handler is
    a FakeResponse, a list of them (consumed in order), or a callable
    taking the parsed params."""

    def __init__(self):
        self.calls: list[dict] = []
        self.routes: dict[tuple[str, str], object] = {
            ("GET", "/fapi/v1/exchangeInfo"): FakeResponse(200, EXCHANGE_INFO),
        }

    def request(self, method, url, *, headers=None, timeout=30.0):
        parts = urlsplit(url)
        params = dict(parse_qsl(parts.query))
        self.calls.append({"method": method, "path": parts.path,
                           "params": params, "headers": headers or {}})
        handler = self.routes.get((method, parts.path))
        if handler is None:
            return FakeResponse(200, {})
        if isinstance(handler, list):
            return handler.pop(0)
        if callable(handler):
            return handler(params)
        return handler

    def posts(self):
        return [c for c in self.calls
                if c["method"] == "POST" and c["path"] == "/fapi/v1/order"]


def fill_response(params, status=None):
    """Venue echo for POST /fapi/v1/order: MARKET fills, others rest."""
    if status is None:
        status = "FILLED" if params.get("type") == "MARKET" else "NEW"
    quantity = params.get("quantity", "0")
    return FakeResponse(200, {
        "symbol": params["symbol"],
        "orderId": 4200 + len(params),
        "clientOrderId": params.get("newClientOrderId", ""),
        "status": status,
        "executedQty": quantity if status == "FILLED" else "0",
        "avgPrice": "100000" if status == "FILLED" else "0",
        "type": params.get("type", ""),
        "reduceOnly": params.get("reduceOnly") == "true",
    })


COID = "ta" + "0" * 24


def entry_spec(quantity=0.001, side="BUY", coid=COID, **kwargs):
    return OrderSpec(client_order_id=coid, symbol="BTC-USD", venue_symbol="",
                     side=side, quantity=quantity,
                     reference_price=100_000.0, **kwargs)


BRACKET = BracketSpec(stop_loss_price=98_000.0)


class FakeAlerts:
    def __init__(self):
        self.emitted = []

    def emit(self, severity, event, text, **labels):
        self.emitted.append({"severity": severity, "event": event,
                             "text": text, **labels})


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "test-key-1")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "test-secret-1")


def make_adapter(http=None, armed=True, cap=1_000.0, **kwargs):
    http = http or FakeHttp()
    http.routes.setdefault(("POST", "/fapi/v1/order"), fill_response)
    adapter = BinanceFuturesAdapter(
        base_url=TESTNET_BASE,
        armed_fn=(lambda: True) if armed else None,
        max_order_notional=cap, http=http, **kwargs)
    return adapter, http


class TestSigning:
    def test_hmac_signature_matches_binance_docs_vector(self):
        assert sign_query(DOCS_SECRET, DOCS_QUERY) == DOCS_SIGNATURE

    def test_signed_query_appends_signature_over_exact_bytes(self):
        params = dict(parse_qsl(DOCS_QUERY))
        query = signed_query(params, DOCS_SECRET)
        assert query == f"{DOCS_QUERY}&signature={DOCS_SIGNATURE}"

    def test_credentials_read_at_call_time(self, creds, monkeypatch):
        adapter, http = make_adapter()
        http.routes[("GET", "/fapi/v1/openOrders")] = FakeResponse(200, [])
        adapter.open_orders()
        assert http.calls[-1]["headers"]["X-MBX-APIKEY"] == "test-key-1"
        monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "test-key-2")
        monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "test-secret-2")
        adapter.open_orders()  # same instance, fresh env read
        assert http.calls[-1]["headers"]["X-MBX-APIKEY"] == "test-key-2"

    def test_missing_credentials_refused_without_network(self, monkeypatch):
        monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)
        adapter, http = make_adapter()
        with pytest.raises(AdapterError) as excinfo:
            adapter.open_orders()
        assert "BINANCE_TESTNET_API_KEY" in str(excinfo.value)
        assert http.calls == []

    def test_secret_never_in_error_text(self, creds):
        adapter, http = make_adapter()

        def boom(params):
            raise ConnectionError("dial tcp: test-secret-1 leaked?")

        http.routes[("GET", "/fapi/v1/openOrders")] = boom
        adapter._max_read_retries = 0
        with pytest.raises(AdapterError) as excinfo:
            adapter.open_orders()
        assert "test-secret-1" not in str(excinfo.value)


class TestArmingGate:
    """Arming gates ORDERS, not eyesight: a disarmed adapter refuses to
    open new risk but must still let the operator see and close the
    book (the deadlock/blindness defect)."""

    def test_disarmed_adapter_refuses_risk_increasing_writes(self, creds):
        adapter, http = make_adapter(armed=False)
        operations = [
            lambda: adapter.place_order(entry_spec(), BRACKET),
            lambda: adapter.cancel_order(COID),
        ]
        for operation in operations:
            with pytest.raises(ExecutionNotEnabled):
                operation()
        assert http.posts() == []  # no order ever left the process

    def test_disarmed_reads_still_work(self, creds):
        """Readiness must be able to do its authenticated venue read
        BEFORE anything is armed, and a TTL demotion must never blind
        the operator to open positions."""
        adapter, http = make_adapter(armed=False)
        http.routes[("GET", "/fapi/v2/account")] = FakeResponse(
            200, {"totalMarginBalance": "1000", "availableBalance": "900"})
        http.routes[("GET", "/fapi/v2/positionRisk")] = FakeResponse(200, [
            {"symbol": "BTCUSDT", "positionAmt": "0.002",
             "entryPrice": "100000"}])
        http.routes[("GET", "/fapi/v1/openOrders")] = FakeResponse(200, [])
        http.routes[("GET", "/fapi/v1/premiumIndex")] = FakeResponse(
            200, {"markPrice": "100500"})
        http.routes[("GET", "/fapi/v1/time")] = FakeResponse(
            200, {"serverTime": int(time.time() * 1000)})

        assert adapter.account().equity == 1000.0
        assert [p.symbol for p in adapter.positions()] == ["BTC-USD"]
        assert adapter.open_orders() == []
        assert adapter.mark_price("BTC-USD") == 100_500.0
        assert adapter.has_resting_stop("BTC-USD") is False
        adapter.check_clock()  # no raise
        assert adapter.instruments.get("BTC-USD").venue_symbol == "BTCUSDT"

    def test_disarmed_flatten_is_never_blocked(self, creds):
        """Flattening is a safety action; it is most needed exactly when
        arming lapsed or a drill disarmed the pair."""
        adapter, http = make_adapter(armed=False)
        http.routes[("GET", "/fapi/v2/positionRisk")] = FakeResponse(200, [
            {"symbol": "BTCUSDT", "positionAmt": "0.002",
             "entryPrice": "100000"}])
        result = adapter.close_position("BTC-USD", 100_000.0)
        assert result.status in ("filled", "submitted")
        post = http.posts()[-1]["params"]
        assert post["reduceOnly"] == "true" and post["side"] == "SELL"

    def test_disarmed_reduce_only_order_passes(self, creds):
        adapter, http = make_adapter(armed=False)
        update = adapter.place_order(entry_spec(reduce_only=True, side="SELL"))
        assert update.state is OrderState.FILLED
        assert http.posts()[-1]["params"]["reduceOnly"] == "true"

    def test_armed_false_refuses_writes_but_not_reads(self, creds):
        adapter, http = make_adapter()
        adapter._armed_fn = lambda: False
        with pytest.raises(ExecutionNotEnabled):
            adapter.place_order(entry_spec(), BRACKET)
        assert http.posts() == []
        http.routes[("GET", "/fapi/v2/account")] = FakeResponse(
            200, {"totalMarginBalance": "7", "availableBalance": "7"})
        assert adapter.account().equity == 7.0

    def test_refusal_is_honest_about_what_is_blocked(self, creds):
        adapter, _ = make_adapter(armed=False)
        with pytest.raises(ExecutionNotEnabled) as excinfo:
            adapter.place_order(entry_spec(), BRACKET)
        message = str(excinfo.value)
        assert "arm-live" in message
        assert "reduce-only" in message.lower()


class TestClockProbe:
    def test_check_clock_passes_within_budget(self, creds):
        adapter, http = make_adapter()
        http.routes[("GET", "/fapi/v1/time")] = FakeResponse(
            200, {"serverTime": int(time.time() * 1000)})
        assert adapter.check_clock() is None
        call = http.calls[-1]
        assert call["path"] == "/fapi/v1/time"
        assert "signature" not in call["params"]
        assert "X-MBX-APIKEY" not in call["headers"]  # public probe

    def test_check_clock_fails_beyond_budget(self, creds):
        adapter, http = make_adapter()
        http.routes[("GET", "/fapi/v1/time")] = FakeResponse(
            200, {"serverTime": int((time.time() - 30) * 1000)})
        with pytest.raises(AdapterError, match="clock skew"):
            adapter.check_clock()

    def test_check_clock_needs_no_credentials(self, monkeypatch):
        monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
        monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)
        adapter, http = make_adapter(armed=False)
        http.routes[("GET", "/fapi/v1/time")] = FakeResponse(
            200, {"serverTime": int(time.time() * 1000)})
        adapter.check_clock()

    def test_preflight_clock_check_passes_for_binance(self, creds):
        from tradingagents.pro.preflight import ReadinessReport, check_clock

        adapter, http = make_adapter(armed=False)
        http.routes[("GET", "/fapi/v1/time")] = FakeResponse(
            200, {"serverTime": int(time.time() * 1000)})
        report = ReadinessReport()
        check_clock(report, adapter)
        assert report.checks[0].status == "pass"

    def test_mainnet_requires_explicit_ack(self, creds, monkeypatch):
        monkeypatch.setenv("BINANCE_API_KEY", "k")
        monkeypatch.setenv("BINANCE_API_SECRET", "s")
        with pytest.raises(ExecutionNotEnabled):
            BinanceFuturesAdapter(base_url=PROD_BASE)
        monkeypatch.setenv("PRO_BINANCE_MAINNET_ACK", "dust-pilot-approved")
        adapter = BinanceFuturesAdapter(base_url=PROD_BASE)
        assert adapter.name.endswith("MAINNET")


class TestEntryStopAtomicity:
    def test_entry_places_reduce_only_stop_in_same_flow(self, creds):
        adapter, http = make_adapter()
        update = adapter.place_order(entry_spec(), BRACKET)
        assert update.state is OrderState.FILLED
        posts = http.posts()
        assert len(posts) == 2, "entry and stop must be one submission flow"
        entry, stop = posts[0]["params"], posts[1]["params"]
        assert entry["type"] == "MARKET" and entry["side"] == "BUY"
        assert entry["newClientOrderId"] == COID
        # the protective stop: reduce-only STOP_MARKET at the ticket's
        # stop price, opposite side, same size, deterministic child coid
        assert stop["type"] == "STOP_MARKET"
        assert stop["side"] == "SELL"
        assert stop["reduceOnly"] == "true"
        assert stop["stopPrice"] == "98000"
        assert stop["quantity"] == entry["quantity"] == "0.001"
        assert stop["newClientOrderId"] == f"{COID}sl"
        assert stop["workingType"] == "MARK_PRICE"

    def test_naked_entry_refused_before_any_order(self, creds):
        adapter, http = make_adapter()
        update = adapter.place_order(entry_spec(), bracket=None)
        assert update.state is OrderState.REJECTED
        assert "naked entry refused" in update.reason
        assert http.posts() == []

    def test_stop_failure_flattens_entry_and_alerts(self, creds):
        alerts, audit = FakeAlerts(), AuditLog()
        adapter, http = make_adapter(alerts=alerts, audit=audit)
        http.routes[("POST", "/fapi/v1/order")] = [
            fill_response({"symbol": "BTCUSDT", "type": "MARKET",
                           "quantity": "0.001", "newClientOrderId": COID}),
            FakeResponse(400, {"code": -2021,
                               "msg": "Order would immediately trigger."}),
            fill_response({"symbol": "BTCUSDT", "type": "MARKET",
                           "quantity": "0.001",
                           "newClientOrderId": f"{COID}xx",
                           "reduceOnly": "true"}),
        ]
        update = adapter.place_order(entry_spec(), BRACKET)
        assert update.state is OrderState.REJECTED
        assert "entry flattened" in update.reason
        flatten = http.posts()[2]["params"]
        assert flatten["type"] == "MARKET"
        assert flatten["side"] == "SELL"          # opposite of the entry
        assert flatten["reduceOnly"] == "true"
        assert flatten["quantity"] == "0.001"     # exactly what filled
        assert [a["event"] for a in alerts.emitted] == ["live_entry_flattened"]
        assert alerts.emitted[0]["severity"] == "critical"
        events = [e["event"] for e in audit.entries]
        assert "live_stop_placement_failed" in events

    def test_stop_and_flatten_failure_engages_kill_switch(self, creds):
        alerts, kill = FakeAlerts(), KillSwitch()
        adapter, http = make_adapter(alerts=alerts, kill_switch=kill)
        http.routes[("POST", "/fapi/v1/order")] = [
            fill_response({"symbol": "BTCUSDT", "type": "MARKET",
                           "quantity": "0.001", "newClientOrderId": COID}),
            FakeResponse(400, {"code": -2021, "msg": "stop refused"}),
            FakeResponse(400, {"code": -2022, "msg": "flatten refused"}),
        ]
        update = adapter.place_order(entry_spec(), BRACKET)
        assert update.state is OrderState.REJECTED
        assert "manual intervention" in update.reason.lower()
        assert kill.engaged and "naked position" in kill.reason
        assert [a["event"] for a in alerts.emitted] == ["live_naked_position"]


class TestNotionalCap:
    def test_oversize_entry_refused_before_any_api_call(self, creds):
        adapter, http = make_adapter(cap=150.0)
        # 0.002 BTC @ 100k = $200 notional > $150 cap
        update = adapter.place_order(entry_spec(quantity=0.002), BRACKET)
        assert update.state is OrderState.REJECTED
        assert "max_notional_per_trade" in update.reason
        assert http.posts() == []

    def test_min_size_order_passes_the_cap(self, creds):
        adapter, http = make_adapter(cap=150.0)
        update = adapter.place_order(entry_spec(quantity=0.001), BRACKET)
        assert update.state is OrderState.FILLED

    def test_reduce_only_exempt_from_entry_cap(self, creds):
        # flattening a large position must never be blocked by the cap
        adapter, http = make_adapter(cap=150.0)
        update = adapter.place_order(
            entry_spec(quantity=0.05, side="SELL", reduce_only=True))
        assert update.state is OrderState.FILLED
        assert http.posts()[0]["params"]["reduceOnly"] == "true"

    def test_below_venue_minimum_refused(self, creds):
        adapter, http = make_adapter()
        update = adapter.place_order(entry_spec(quantity=0.0004), BRACKET)
        assert update.state is OrderState.REJECTED
        assert "below venue minimum" in update.reason
        assert http.posts() == []


class TestReduceOnlyLadder:
    def test_tp_ladder_placed_reduce_only_after_stop(self, creds):
        adapter, http = make_adapter()
        bracket = BracketSpec(
            stop_loss_price=98_000.0,
            take_profits=((104_000.0, 0.5), (108_000.0, 0.5)),
        )
        adapter.place_order(entry_spec(quantity=0.002), bracket)
        posts = [c["params"] for c in http.posts()]
        assert [p["type"] for p in posts] == [
            "MARKET", "STOP_MARKET", "LIMIT", "LIMIT"]
        tp1, tp2 = posts[2], posts[3]
        for index, tp in enumerate((tp1, tp2)):
            assert tp["reduceOnly"] == "true"
            assert tp["side"] == "SELL"
            assert tp["timeInForce"] == "GTC"
            assert tp["quantity"] == "0.001"  # 50% of 0.002 each
            assert tp["newClientOrderId"] == f"{COID}tp{index + 1}"
        assert tp1["price"] == "104000" and tp2["price"] == "108000"

    def test_tp_leg_failure_never_unwinds_protected_position(self, creds):
        alerts = FakeAlerts()
        adapter, http = make_adapter(alerts=alerts)
        http.routes[("POST", "/fapi/v1/order")] = [
            fill_response({"symbol": "BTCUSDT", "type": "MARKET",
                           "quantity": "0.001", "newClientOrderId": COID}),
            fill_response({"symbol": "BTCUSDT", "type": "STOP_MARKET",
                           "quantity": "0.001",
                           "newClientOrderId": f"{COID}sl"}),
            FakeResponse(400, {"code": -4164, "msg": "notional too small"}),
        ]
        bracket = BracketSpec(stop_loss_price=98_000.0,
                              take_profits=((104_000.0, 1.0),))
        update = adapter.place_order(entry_spec(), bracket)
        assert update.state is OrderState.FILLED  # stop rests; TP best-effort
        assert alerts.emitted == []
        assert len(http.posts()) == 3  # no flatten was sent


class TestReadsAndState:
    def test_positions_and_account_mapping(self, creds):
        adapter, http = make_adapter()
        http.routes[("GET", "/fapi/v2/positionRisk")] = FakeResponse(200, [
            {"symbol": "BTCUSDT", "positionAmt": "0.001",
             "entryPrice": "99500"},
            {"symbol": "ETHUSDT", "positionAmt": "0", "entryPrice": "0"},
        ])
        http.routes[("GET", "/fapi/v2/account")] = FakeResponse(200, {
            "totalMarginBalance": "1500.5", "availableBalance": "1400.25"})
        positions = adapter.positions()
        assert len(positions) == 1
        assert positions[0].symbol == "BTC-USD"
        assert positions[0].side == "BUY"
        assert positions[0].quantity == pytest.approx(0.001)
        account = adapter.account()
        assert account.equity == pytest.approx(1500.5)
        assert account.cash == pytest.approx(1400.25)

    def test_has_resting_stop_detects_venue_protection(self, creds):
        adapter, http = make_adapter()
        http.routes[("GET", "/fapi/v1/openOrders")] = FakeResponse(200, [{
            "symbol": "BTCUSDT", "orderId": 7, "clientOrderId": f"{COID}sl",
            "status": "NEW", "type": "STOP_MARKET", "reduceOnly": True,
            "executedQty": "0", "avgPrice": "0",
        }])
        assert adapter.has_resting_stop("BTC-USD") is True
        http.routes[("GET", "/fapi/v1/openOrders")] = FakeResponse(200, [])
        assert adapter.has_resting_stop("BTC-USD") is False

    def test_cancel_unknown_order_rejected_locally(self, creds):
        adapter, http = make_adapter()
        update = adapter.cancel_order("ta" + "f" * 24)
        assert update.state is OrderState.REJECTED
        assert update.reason == "unknown order"
        assert http.calls == []


class TestOmsIntegration:
    def test_oms_native_bracket_routes_entry_and_stop(self, creds, tmp_path):
        """The OMS 'venue_bracket' path hands the bracket to place_order —
        the same unit the drill and the live router exercise."""
        from tradingagents.pro.execution.orders import ExecutionPlan

        adapter, http = make_adapter()
        oms = OrderManager(adapter,
                           journal_path=tmp_path / "journal.jsonl")
        oms.recover()
        plan = ExecutionPlan(
            run_id="run-1", decision_hash="d" * 64, symbol="BTC-USD",
            side="BUY", quantity=0.001, reference_price=100_000.0,
            bracket=BracketSpec(stop_loss_price=98_000.0),
            protection_mode="venue_bracket",
        )
        order = oms.execute(plan)
        assert order.state is OrderState.FILLED
        types = [c["params"]["type"] for c in http.posts()]
        assert types == ["MARKET", "STOP_MARKET"]
        assert not oms.pending_protection  # native path: nothing synthetic
