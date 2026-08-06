"""Kill-switch drill (P3-01) — full loop against a stateful transport
stub: min-size protected entry, venue stop verification, kill switch,
flatten, disarm, audit record. No network anywhere."""

from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

import pytest

from tradingagents.pro.arming import ArmingStore
from tradingagents.pro.drill import DrillRefused, run_kill_switch_drill
from tradingagents.pro.execution import AuditLog, KillSwitch, OrderManager
from tradingagents.pro.execution.adapters.binance_futures import (
    BinanceFuturesAdapter,
)

EXCHANGE_INFO = {
    "symbols": [{
        "symbol": "BTCUSDT",
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001",
             "minQty": "0.001"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
        ],
    }, {
        "symbol": "ETHUSDT",
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001",
             "minQty": "0.001"},
            {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
        ],
    }]
}


class _Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.headers = {}
        self._payload = {} if payload is None else payload

    def json(self):
        return self._payload


class StatefulVenue:
    """A tiny in-memory Binance futures testnet: orders mutate a position
    book; MARKET fills instantly, STOP_MARKET rests, DELETE cancels."""

    def __init__(self, mark=100_000.0):
        self.mark = mark
        self.orders: dict[str, dict] = {}
        self.position_amt = 0.0
        self._next_id = 1000

    # --- HttpClient protocol -------------------------------------------------

    def request(self, method, url, *, headers=None, timeout=30.0):
        parts = urlsplit(url)
        params = dict(parse_qsl(parts.query))
        return self._dispatch(method, parts.path, params)

    def _dispatch(self, method, path, params):
        if path == "/fapi/v1/exchangeInfo":
            return _Response(200, EXCHANGE_INFO)
        if path == "/fapi/v1/premiumIndex":
            return _Response(200, {"markPrice": str(self.mark)})
        if path == "/fapi/v2/positionRisk":
            return _Response(200, [{
                "symbol": "BTCUSDT",
                "positionAmt": f"{self.position_amt:.3f}",
                "entryPrice": str(self.mark),
            }])
        if path == "/fapi/v2/account":
            return _Response(200, {"totalMarginBalance": "10000",
                                   "availableBalance": "10000"})
        if path == "/fapi/v1/openOrders":
            return _Response(200, [o for o in self.orders.values()
                                   if o["status"] == "NEW"])
        if path == "/fapi/v1/order" and method == "POST":
            return self._place(params)
        if path == "/fapi/v1/order" and method == "DELETE":
            order = self.orders.get(params.get("origClientOrderId", ""))
            if order is None:
                return _Response(400, {"code": -2011, "msg": "Unknown order"})
            order["status"] = "CANCELED"
            return _Response(200, order)
        if path == "/fapi/v1/order" and method == "GET":
            order = self.orders.get(params.get("origClientOrderId", ""))
            if order is None:
                return _Response(400, {"code": -2013,
                                       "msg": "Order does not exist"})
            return _Response(200, order)
        return _Response(200, {})

    def _place(self, params):
        self._next_id += 1
        quantity = float(params.get("quantity") or 0)
        side = params["side"]
        order_type = params["type"]
        filled = order_type == "MARKET"
        if filled:
            signed = quantity if side == "BUY" else -quantity
            self.position_amt = round(self.position_amt + signed, 9)
        order = {
            "symbol": params["symbol"],
            "orderId": self._next_id,
            "clientOrderId": params.get("newClientOrderId", ""),
            "status": "FILLED" if filled else "NEW",
            "executedQty": params.get("quantity") if filled else "0",
            "avgPrice": str(self.mark) if filled else "0",
            "type": order_type,
            "reduceOnly": params.get("reduceOnly") == "true",
        }
        self.orders[order["clientOrderId"]] = order
        return _Response(200, order)


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "drill-key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "drill-secret")


def make_rig(tmp_path, venue=None, armed_pair="BTC-USD", tier="canary"):
    venue = venue or StatefulVenue()
    audit = AuditLog(tmp_path / "audit.jsonl")
    arming = ArmingStore(tmp_path / "arming.json", audit=audit)
    if tier:
        arming.arm(armed_pair, tier, operator="tester")
    kill_switch = KillSwitch(tmp_path / "KILL")
    adapter = BinanceFuturesAdapter(
        armed_fn=lambda: arming.is_live(armed_pair),
        max_order_notional=250.0, http=venue,
        audit=audit, kill_switch=kill_switch,
    )
    oms = OrderManager(adapter, journal_path=tmp_path / "journal.jsonl",
                       audit=audit)
    oms.recover()
    return SimpleNamespace(venue=venue, audit=audit, arming=arming,
                           kill_switch=kill_switch, adapter=adapter, oms=oms)


class TestKillSwitchDrill:
    def test_happy_path_flattens_disarms_and_audits(self, creds, tmp_path):
        rig = make_rig(tmp_path)
        result = run_kill_switch_drill(
            oms=rig.oms, arming=rig.arming, kill_switch=rig.kill_switch,
            audit=rig.audit, operator="tester", symbol="BTC-USD")

        assert result["passed"] is True
        step_names = [s["step"] for s in result["steps"]]
        assert step_names == [
            "min_size_entry", "venue_stop_resting", "kill_switch_engaged",
            "flatten_all", "verified_flat", "disarmed_all",
            "kill_switch_reset",
        ]
        # venue truth: book flat, stop was resting then canceled
        assert rig.venue.position_amt == 0.0
        stop = next(o for o in rig.venue.orders.values()
                    if o["type"] == "STOP_MARKET")
        assert stop["reduceOnly"] is True
        assert stop["status"] == "CANCELED"
        # arming + kill switch handed back cleanly
        assert rig.arming.is_live("BTC-USD") is False
        assert "kill_switch_drill" in rig.arming.get("BTC-USD").disarm_reason
        assert rig.kill_switch.engaged is False
        # audit carries the full drill record and still verifies
        drill_entries = [e for e in rig.audit.entries
                         if e["event"] == "kill_switch_drill"]
        assert len(drill_entries) == 1
        assert drill_entries[0]["payload"]["passed"] is True
        assert drill_entries[0]["payload"]["operator"] == "tester"
        assert rig.audit.verify()

    def test_drill_order_is_minimum_size_and_protected(self, creds, tmp_path):
        rig = make_rig(tmp_path)
        run_kill_switch_drill(
            oms=rig.oms, arming=rig.arming, kill_switch=rig.kill_switch,
            audit=rig.audit, operator="tester", symbol="BTC-USD")
        entry = next(o for o in rig.venue.orders.values()
                     if o["type"] == "MARKET" and not o["reduceOnly"])
        assert entry["executedQty"] == "0.001"  # venue minimum, never more
        stop = next(o for o in rig.venue.orders.values()
                    if o["type"] == "STOP_MARKET")
        assert stop["clientOrderId"] == f"{entry['clientOrderId']}sl"

    def test_refuses_non_testnet_adapter(self, creds, tmp_path):
        rig = make_rig(tmp_path)
        rig.oms.adapter = SimpleNamespace(name="binance_futures:MAINNET")
        with pytest.raises(DrillRefused, match="TESTNET"):
            run_kill_switch_drill(
                oms=rig.oms, arming=rig.arming,
                kill_switch=rig.kill_switch, audit=rig.audit,
                operator="tester")
        assert rig.venue.orders == {}  # nothing was ever sent

    def test_refuses_unarmed_pair(self, creds, tmp_path):
        rig = make_rig(tmp_path, tier=None)  # never armed
        with pytest.raises(DrillRefused, match="not armed"):
            run_kill_switch_drill(
                oms=rig.oms, arming=rig.arming,
                kill_switch=rig.kill_switch, audit=rig.audit,
                operator="tester")
        assert rig.venue.orders == {}

    def test_refuses_without_operator(self, creds, tmp_path):
        rig = make_rig(tmp_path)
        with pytest.raises(DrillRefused, match="operator"):
            run_kill_switch_drill(
                oms=rig.oms, arming=rig.arming,
                kill_switch=rig.kill_switch, audit=rig.audit, operator="")

    def test_failed_verification_still_flattens_and_disarms(self, creds,
                                                            tmp_path):
        """A drill that FAILS a check must still clean up: kill switch
        stays engaged (not reset), pairs disarmed, audit records the
        failure."""
        rig = make_rig(tmp_path)
        # sabotage the stop-verification read: report no open orders even
        # though the stop was accepted
        original = rig.venue._dispatch

        def no_open_orders(method, path, params):
            if path == "/fapi/v1/openOrders":
                return _Response(200, [])
            return original(method, path, params)

        rig.venue._dispatch = no_open_orders
        result = run_kill_switch_drill(
            oms=rig.oms, arming=rig.arming, kill_switch=rig.kill_switch,
            audit=rig.audit, operator="tester", symbol="BTC-USD")
        assert result["passed"] is False
        by_name = {s["step"]: s for s in result["steps"]}
        assert by_name["venue_stop_resting"]["ok"] is False
        assert by_name["verified_flat"]["ok"] is True   # cleanup still ran
        assert by_name["disarmed_all"]["ok"] is True
        assert rig.venue.position_amt == 0.0
        assert rig.arming.is_live("BTC-USD") is False
        drill_entry = next(e for e in rig.audit.entries
                           if e["event"] == "kill_switch_drill")
        assert drill_entry["payload"]["passed"] is False


class TestSightAndFlattenSurviveDisarm:
    """The blindness defect: after a drill disarms (or an arming TTL
    silently demotes to paper) the operator must still be able to SEE the
    venue book and CLOSE it. Only opening new risk needs the ceremony."""

    def test_reads_and_flatten_work_after_drill_disarms(self, creds, tmp_path):
        from tradingagents.pro.flatten import emergency_flatten

        rig = make_rig(tmp_path)
        run_kill_switch_drill(
            oms=rig.oms, arming=rig.arming, kill_switch=rig.kill_switch,
            audit=rig.audit, operator="tester", symbol="BTC-USD")
        assert rig.arming.is_live("BTC-USD") is False

        # a position appears on the venue while nothing is armed (a stray
        # fill, a manual trade, a reconcile-time surprise)
        rig.venue.position_amt = 0.002

        # eyesight first
        assert rig.adapter.account().equity == 10_000.0
        assert [p.symbol for p in rig.adapter.positions()] == ["BTC-USD"]
        assert callable(rig.adapter.check_clock)  # readiness can probe

        # then the ability to close it
        router = SimpleNamespace(adapter=rig.adapter, oms=None,
                                 audit=rig.audit,
                                 kill_switch=rig.kill_switch)
        summary = emergency_flatten(router, arming=rig.arming,
                                    operator="tester")
        assert summary["errors"] == []
        assert summary["flattened"] == ["BTC-USD"]
        assert rig.venue.position_amt == 0.0

    def test_flatten_cancels_resting_orders_while_disarmed(self, creds,
                                                           tmp_path):
        """cancel-all is part of the flatten safety action: a resting
        entry must not survive a flatten just because arming lapsed."""
        from tradingagents.pro.flatten import emergency_flatten

        rig = make_rig(tmp_path)
        rig.arming.disarm_all("ttl lapsed", operator="tester")
        assert rig.arming.is_live("BTC-USD") is False

        # a resting order exists on the venue regardless of how it got there
        rig.venue._place({"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT",
                          "quantity": "0.001",
                          "newClientOrderId": "stray-resting"})
        # the disarmed read sees it (eyesight), the disarmed flatten-cancel
        # removes it
        assert "stray-resting" in {u.client_order_id
                                   for u in rig.adapter.open_orders()}
        update = rig.adapter.cancel_order("stray-resting", flattening=True)
        assert update.state.terminal
        assert rig.venue.orders["stray-resting"]["status"] == "CANCELED"

        router = SimpleNamespace(adapter=rig.adapter, oms=None,
                                 audit=rig.audit,
                                 kill_switch=rig.kill_switch)
        assert emergency_flatten(router, arming=rig.arming,
                                 operator="tester")["errors"] == []
