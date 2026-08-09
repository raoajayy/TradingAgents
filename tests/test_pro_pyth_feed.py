"""Pyth price bars via the UDF proxy, and the venue-volume overlay."""

from datetime import datetime, timedelta, timezone

import pytest

from tradingagents.contracts import OHLCVBar, Timeframe
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.pro.ingestion.pyth import (
    PYTH_SYMBOLS,
    PythBarsFeed,
    PythPriceVenueVolumeFeed,
    merge_volume,
)

BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def udf(n: int = 3, start: datetime = BASE) -> dict:
    times = [int((start + timedelta(days=i)).timestamp()) for i in range(n)]
    return {
        "s": "ok",
        "t": times,
        "o": [100.0 + i for i in range(n)],
        "h": [101.0 + i for i in range(n)],
        "l": [99.0 + i for i in range(n)],
        "c": [100.5 + i for i in range(n)],
        "v": [0] * n,  # oracle: always zero
    }


class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload


def bar(day: int, volume: float) -> OHLCVBar:
    return OHLCVBar(
        timeframe=Timeframe.D1, start=BASE + timedelta(days=day),
        open=1.0, high=2.0, low=0.5, close=1.5, volume=volume,
    )


class TestPythBarsFeed:
    def test_maps_dashboard_symbols_and_parses_bars(self):
        transport = FakeTransport(udf(3))
        feed = PythBarsFeed(transport=transport, base="https://proxy.test")
        bars = feed.get_bars("BTC-USD", Timeframe.D1, limit=10)

        url, params = transport.calls[0]
        assert url == "https://proxy.test/api/pyth/history"
        assert params["symbol"] == "Crypto.BTC/USD"  # never the raw symbol
        assert params["resolution"] == "D"
        assert len(bars) == 3
        assert bars[0].close == 100.5

    def test_every_pipeline_symbol_has_a_feed_id(self):
        for symbol in ("BTC-USD", "ETH-USD", "SOL-USD",
                       "XAUUSD", "EURUSD", "USDJPY"):
            assert PythBarsFeed.supports(symbol), symbol
            assert PYTH_SYMBOLS[symbol]

    def test_volume_is_zero_because_an_oracle_has_no_venue_flow(self):
        feed = PythBarsFeed(transport=FakeTransport(udf(2)))
        assert [b.volume for b in feed.get_bars("XAUUSD", Timeframe.D1)] == [0.0, 0.0]

    def test_over_fetches_so_session_gaps_still_yield_limit_bars(self):
        """250 calendar days give only ~213 FX/metal bars, so the window has
        to be wider than the bar count."""
        transport = FakeTransport(udf(1))
        PythBarsFeed(transport=transport).get_bars(
            "EURUSD", Timeframe.D1, limit=250)
        _, params = transport.calls[0]
        span_days = (params["to"] - params["from"]) / 86400
        assert span_days > 250

    def test_no_data_and_error_statuses_raise(self):
        for payload in ({"s": "no_data"}, {"s": "error", "errmsg": "nope"}):
            feed = PythBarsFeed(transport=FakeTransport(payload))
            with pytest.raises(NoMarketDataError):
                feed.get_bars("BTC-USD", Timeframe.D1)

    def test_ragged_arrays_raise_rather_than_mispairing_prices(self):
        broken = udf(3)
        broken["h"] = broken["h"][:2]
        feed = PythBarsFeed(transport=FakeTransport(broken))
        with pytest.raises(NoMarketDataError):
            feed.get_bars("BTC-USD", Timeframe.D1)

    def test_unknown_symbol_is_refused(self):
        feed = PythBarsFeed(transport=FakeTransport(udf()))
        with pytest.raises(NoMarketDataError):
            feed.get_bars("NOPE", Timeframe.D1)

    def test_every_timeframe_in_the_contract_is_supported(self):
        """No Timeframe currently lacks a resolution — so the ValueError
        guard in get_bars is defensive against the enum growing, not a
        reachable path today. Assert the coverage rather than the guard."""
        from tradingagents.pro.ingestion.pyth import _RESOLUTION

        assert set(_RESOLUTION) == set(Timeframe)

    def test_a_timeframe_without_a_resolution_is_refused(self, monkeypatch):
        from tradingagents.pro.ingestion import pyth as pyth_mod

        trimmed = {k: v for k, v in pyth_mod._RESOLUTION.items()
                   if k is not Timeframe.W1}
        monkeypatch.setattr(pyth_mod, "_RESOLUTION", trimmed)
        feed = PythBarsFeed(transport=FakeTransport(udf()))
        with pytest.raises(ValueError, match="does not support"):
            feed.get_bars("BTC-USD", Timeframe.W1)

    def test_malformed_rows_drop_without_killing_the_fetch(self):
        payload = udf(3)
        payload["l"][1] = 999.0  # low above open/close — contract rejects it
        feed = PythBarsFeed(transport=FakeTransport(payload))
        bars = feed.get_bars("BTC-USD", Timeframe.D1)
        assert len(bars) == 2, "one bad bar must not lose the other two"


class TestVolumeOverlay:
    def test_venue_volume_lands_on_matching_pyth_bars(self):
        priced = [bar(0, 0.0), bar(1, 0.0)]
        volumes = [bar(0, 1690.9), bar(1, 42.0)]
        assert [b.volume for b in merge_volume(priced, volumes)] == [1690.9, 42.0]

    def test_unmatched_bars_keep_volume_unknown_rather_than_guessing(self):
        merged = merge_volume([bar(0, 0.0), bar(5, 0.0)], [bar(0, 7.0)])
        assert [b.volume for b in merged] == [7.0, 0.0]

    def test_prices_are_never_altered_by_the_overlay(self):
        priced = [bar(0, 0.0)]
        merged = merge_volume(priced, [bar(0, 9.0)])
        assert (merged[0].open, merged[0].high, merged[0].low, merged[0].close) == \
               (priced[0].open, priced[0].high, priced[0].low, priced[0].close)


class _Venue:
    name = "venue"

    def __init__(self, bars=None, boom=False):
        self._bars = bars or []
        self._boom = boom
        self.seen_symbols = []

    def get_bars(self, symbol, timeframe, *, limit=250, end=None):
        self.seen_symbols.append(symbol)
        if self._boom:
            raise RuntimeError("venue down")
        return self._bars

    def get_quote(self, symbol):
        return f"quote:{symbol}"


class TestCompositeFeed:
    def _composite(self, venue, payload=None, **kw):
        pyth = PythBarsFeed(transport=FakeTransport(payload or udf(2)))
        return PythPriceVenueVolumeFeed(venue, "BTC-USD", pyth, **kw)

    def test_venue_gets_the_vendor_symbol_pyth_gets_the_dashboard_one(self):
        """The service passes spec.vendor_symbol (BTCUSDT); Pyth is keyed by
        the dashboard name, so the two must not be confused."""
        venue = _Venue([bar(0, 5.0), bar(1, 6.0)])
        composite = self._composite(venue)
        composite.get_bars("BTCUSDT", Timeframe.D1, limit=5)
        assert venue.seen_symbols == ["BTCUSDT"]

    def test_price_from_pyth_volume_from_venue(self):
        venue = _Venue([bar(0, 5.0), bar(1, 6.0)])
        bars = self._composite(venue).get_bars("BTCUSDT", Timeframe.D1)
        assert [b.close for b in bars] == [100.5, 101.5]   # Pyth prices
        assert [b.volume for b in bars] == [5.0, 6.0]      # venue volume

    def test_with_volume_false_skips_the_venue_entirely(self):
        """FX/metals have no consolidated spot volume — do not even ask."""
        venue = _Venue([bar(0, 5.0)])
        composite = self._composite(venue, with_volume=False)
        bars = composite.get_bars("XAU_USD", Timeframe.D1)
        assert venue.seen_symbols == []
        assert all(b.volume == 0.0 for b in bars)

    def test_pyth_outage_falls_back_to_the_venue(self):
        venue = _Venue([bar(0, 5.0)])
        composite = self._composite(venue, payload={"s": "error", "errmsg": "x"})
        bars = composite.get_bars("BTCUSDT", Timeframe.D1)
        assert bars == venue._bars, "a price-oracle outage must degrade, not blank"

    def test_venue_outage_still_ships_pyth_prices(self):
        composite = self._composite(_Venue(boom=True))
        bars = composite.get_bars("BTCUSDT", Timeframe.D1)
        assert [b.close for b in bars] == [100.5, 101.5]
        assert all(b.volume == 0.0 for b in bars)

    def test_non_bar_calls_still_reach_the_venue(self):
        """The registry's feed also serves live ticks and quotes — replacing
        the whole feed would have silently killed them."""
        composite = self._composite(_Venue())
        assert composite.get_quote("BTCUSDT") == "quote:BTCUSDT"


class TestClaudeCliConcurrencyBound:
    """Every claude-cli call forks a Node runtime (~200-300MB RSS).

    The pipeline fans five team nodes out in parallel, so an unbounded
    stack of them OOM-killed the 1Gi Cloud Run container mid-run —
    repeatedly, and a container that dies mid-run leaves no run record at
    all, so the symptom read as a silent loop rather than a crash.
    """

    def test_concurrent_subprocesses_are_capped(self, monkeypatch):
        import threading

        from tradingagents.llm_clients import claude_cli_client as cli

        live = 0
        peak = 0
        lock = threading.Lock()

        def fake_run(cmd, **kwargs):
            nonlocal live, peak
            with lock:
                live += 1
                peak = max(peak, live)
            try:
                import time
                time.sleep(0.05)
            finally:
                with lock:
                    live -= 1

            class R:
                returncode = 0
                stdout = '{"result": "ok"}'
                stderr = ""
            return R()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        chat = cli.ClaudeCLIChat("m", "/bin/true", timeout=5.0)

        threads = [threading.Thread(target=lambda: chat.complete("hi"))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert peak <= cli._MAX_CONCURRENT_CLI, (
            f"{peak} concurrent CLI processes exceeded the "
            f"{cli._MAX_CONCURRENT_CLI} cap — memory is unbounded again")
        assert peak > 0

    def test_cap_is_env_tunable_and_never_zero(self):
        from tradingagents.llm_clients import claude_cli_client as cli

        assert cli._MAX_CONCURRENT_CLI >= 1
