"""P2-11 liquidation reconstruction: sampled forceOrder ring + OI deltas.

All tests are hermetic — recorded/synthetic frames go straight into the
ring via ``ingest_frame`` and open interest via ``poll_oi_once`` with a
StubTransport (tests/test_pro_free_feeds.py style). ``start()`` is never
called, so no socket ever opens.
"""

import pytest

from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.pro.ingestion.liquidations import LiquidationStream


class StubTransport:
    def __init__(self, payloads):
        # payloads served in order; last one repeats
        self.payloads = list(payloads)
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        if len(self.payloads) > 1:
            return self.payloads.pop(0)
        return self.payloads[0]


class Clock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def frame(ts_s, side="SELL", price=60_000.0, qty=0.5, symbol="BTCUSDT"):
    """One recorded forceOrder frame (Binance USD-M shape)."""
    return {
        "e": "forceOrder",
        "E": int(ts_s * 1000),
        "o": {
            "s": symbol, "S": side, "o": "LIMIT", "f": "IOC",
            "q": str(qty), "p": str(price), "ap": str(price),
            "X": "FILLED", "l": str(qty), "z": str(qty),
            "T": int(ts_s * 1000),
        },
    }


def stream(clock=None):
    return LiquidationStream("BTCUSDT", transport=StubTransport([{}]),
                             now=clock or Clock())


class TestRingMath:
    def test_intensity_notional_and_ratio(self):
        clock = Clock()
        s = stream(clock)
        now = clock.t
        # two long liqs (SELL) + one short liq (BUY) inside the hour
        s.ingest_frame(frame(now - 300, "SELL", 60_000, 1.0))   # 60k
        s.ingest_frame(frame(now - 600, "SELL", 59_000, 0.5))   # 29.5k
        s.ingest_frame(frame(now - 900, "BUY", 61_000, 0.25))   # 15.25k
        by_name = {r.name: r for r in s.get_metrics()}
        assert by_name["LIQ_INTENSITY_1H"].value == 3
        assert by_name["LIQ_INTENSITY_1H"].unit == "events/h (sampled)"
        assert by_name["LIQ_NOTIONAL_1H"].value == pytest.approx(104_750.0)
        assert by_name["LIQ_NOTIONAL_1H"].unit == "USD/h (sampled floor)"
        assert by_name["LIQ_BUY_SELL_RATIO_1H"].value == pytest.approx(
            15_250.0 / 89_500.0)
        # sampling disclaimer travels in the unit string, always
        assert all("sampled" in (r.unit or "")
                   for r in by_name.values() if r.name.startswith("LIQ"))

    def test_ratio_absent_when_one_sided(self):
        s = stream()
        s.ingest_frame(frame(s._now() - 60, "SELL"))
        names = {r.name for r in s.get_metrics()}
        assert "LIQ_INTENSITY_1H" in names
        assert "LIQ_BUY_SELL_RATIO_1H" not in names

    def test_events_older_than_1h_leave_metrics_but_stay_in_ring(self):
        clock = Clock()
        s = stream(clock)
        s.ingest_frame(frame(clock.t - 90 * 60))  # 1.5h old: ring yes, 1h no
        by_name = {r.name: r for r in s.get_metrics()}
        # an empty trailing hour after real events is a REAL zero — served
        assert by_name["LIQ_INTENSITY_1H"].value == 0
        assert by_name["LIQ_NOTIONAL_1H"].value == 0
        assert s.price_buckets()  # 2h ring still feeds the heatmap

    def test_ring_bounded_to_2h(self):
        clock = Clock()
        s = stream(clock)
        s.ingest_frame(frame(clock.t - 3 * 3600))  # beyond the ring
        s.ingest_frame(frame(clock.t - 60))
        assert sum(b["count"] for b in s.price_buckets()) == 1

    def test_malformed_and_foreign_frames_dropped(self):
        s = stream()
        s.ingest_frame({})                                     # no order
        s.ingest_frame(frame(s._now(), symbol="ETHUSDT"))      # not ours
        s.ingest_frame({"o": {"s": "BTCUSDT", "S": "SELL",
                              "ap": "nan-ish", "z": "x"}})     # unparsable
        with pytest.raises(NoMarketDataError):
            s.get_metrics()

    def test_empty_ring_raises_no_market_data(self):
        with pytest.raises(NoMarketDataError) as exc:
            stream().get_metrics()
        # the Intel friendly-error path passes messages <= 80 chars verbatim
        assert len(exc.value.detail) <= 80


class TestPriceBuckets:
    def test_bucket_edges_and_totals(self):
        clock = Clock()
        s = stream(clock)
        s.ingest_frame(frame(clock.t - 10, "SELL", 60_000, 1.0))
        s.ingest_frame(frame(clock.t - 20, "SELL", 60_500, 1.0))
        s.ingest_frame(frame(clock.t - 30, "BUY", 66_000, 2.0))
        buckets = s.price_buckets(n=12)
        assert len(buckets) == 12
        assert buckets[0]["low"] == pytest.approx(60_000)
        assert buckets[-1]["high"] == pytest.approx(66_000)
        # contiguous edges, no gaps
        for prev, nxt in zip(buckets, buckets[1:], strict=False):
            assert prev["high"] == pytest.approx(nxt["low"])
        # half-open buckets, width 500: 60000 → bucket 0, 60500 sits on the
        # edge → bucket 1, 66000 (the max) → the last bucket
        assert buckets[0]["count"] == 1
        assert buckets[0]["notional"] == pytest.approx(60_000.0)
        assert buckets[1]["count"] == 1
        assert buckets[1]["notional"] == pytest.approx(60_500.0)
        assert buckets[-1]["count"] == 1
        assert buckets[-1]["notional"] == pytest.approx(132_000.0)
        assert sum(b["count"] for b in buckets) == 3

    def test_single_price_collapses_to_one_bucket(self):
        clock = Clock()
        s = stream(clock)
        s.ingest_frame(frame(clock.t - 10, "SELL", 60_000, 1.0))
        s.ingest_frame(frame(clock.t - 20, "BUY", 60_000, 1.0))
        buckets = s.price_buckets(n=12)
        assert buckets == [{"low": 60_000.0, "high": 60_000.0,
                            "notional": pytest.approx(120_000.0), "count": 2}]

    def test_empty_ring_returns_empty_list(self):
        assert stream().price_buckets() == []


class TestOpenInterestDelta:
    def test_delta_from_stubbed_poller(self):
        clock = Clock()
        transport = StubTransport([
            {"openInterest": "80000.0", "symbol": "BTCUSDT",
             "time": int((clock.t - 3600) * 1000)},
            {"openInterest": "84000.0", "symbol": "BTCUSDT",
             "time": int(clock.t * 1000)},
        ])
        s = LiquidationStream("BTCUSDT", transport=transport, now=clock)
        s.poll_oi_once()
        s.poll_oi_once()
        url, params = transport.calls[0]
        assert url.endswith("/fapi/v1/openInterest")
        assert params == {"symbol": "BTCUSDT"}
        by_name = {r.name: r for r in s.get_metrics()}
        assert by_name["OI_DELTA_1H"].value == pytest.approx(5.0)
        assert by_name["OI_DELTA_1H"].unit == "% (1h)"

    def test_short_span_yields_no_delta(self):
        # a 2-minute baseline must not masquerade as an hourly delta
        clock = Clock()
        s = stream(clock)
        s.record_oi(80_000.0, clock.t - 120)
        s.record_oi(84_000.0, clock.t)
        with pytest.raises(NoMarketDataError):
            s.get_metrics()

    def test_oi_delta_serves_alone_without_events(self):
        clock = Clock()
        s = stream(clock)
        s.record_oi(80_000.0, clock.t - 3600)
        s.record_oi(76_000.0, clock.t)
        readings = s.get_metrics()
        assert [r.name for r in readings] == ["OI_DELTA_1H"]
        assert readings[0].value == pytest.approx(-5.0)


class TestIntelIntegration:
    def test_snapshot_heatmap_and_metric_flow(self):
        """AC: metrics flow to intel (and thus P2-07 condition alerts read
        them from the same snapshot["metrics"] path); the heatmap block
        appears when the stream is active."""
        from tradingagents.pro.dashboard.intel import METRIC_INFO, IntelService

        clock = Clock()
        s = stream(clock)
        s.ingest_frame(frame(clock.t - 60, "SELL", 60_000, 1.0))
        s._ws_thread = object()  # active without opening a socket
        service = IntelService(
            feeds={"binance_liquidations": s.get_metrics},
            news_fns={}, liquidations=s,
        )
        view = service.snapshot()
        names = {m["name"] for m in view["metrics"]}
        assert {"LIQ_INTENSITY_1H", "LIQ_NOTIONAL_1H"} <= names
        assert "BTCUSDT" in view["liquidation_heatmap"]
        assert sum(b["count"]
                   for b in view["liquidation_heatmap"]["BTCUSDT"]) == 1
        # the alert-builder vocabulary includes the new metrics, and the
        # data dictionary carries the sampling disclaimer
        assert {"LIQ_INTENSITY_1H", "LIQ_NOTIONAL_1H",
                "LIQ_BUY_SELL_RATIO_1H", "OI_DELTA_1H"} <= set(view["metric_keys"])
        for name in ("LIQ_INTENSITY_1H", "LIQ_NOTIONAL_1H",
                     "LIQ_BUY_SELL_RATIO_1H"):
            assert "sampled" in METRIC_INFO[name]["note"].lower()
            assert "not total" in METRIC_INFO[name]["note"].lower()

    def test_snapshot_heatmap_null_when_inactive(self):
        from tradingagents.pro.dashboard.intel import IntelService

        service = IntelService(feeds={"x": lambda: []}, news_fns={})
        assert service.snapshot()["liquidation_heatmap"] is None

    def test_warming_up_disclosed_in_missing_feeds(self):
        from tradingagents.pro.dashboard.intel import IntelService

        s = stream()
        service = IntelService(
            feeds={"binance_liquidations": s.get_metrics}, news_fns={},
            liquidations=s,
        )
        view = service.snapshot()
        assert view["liquidation_heatmap"] is None
        assert any(m.startswith("binance_liquidations:")
                   for m in view["missing_feeds"])
