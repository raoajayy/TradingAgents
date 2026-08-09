"""On-demand market data for charts: symbol registry + TTL-cached bars.

Charts need arbitrary (symbol, timeframe, limit) on demand; pipeline
snapshots refresh hourly and only for the traded symbol, so this service
fetches through the same ingestion adapters the pipeline uses, behind a
small TTL cache with single-flight locking (concurrent chart loads never
stampede a vendor).

Endpoints stay sync (Starlette threadpool) so the blocking vendor calls
never touch the event loop. Capabilities are data, not code: /api/symbols
advertises exactly what each symbol supports so the UI never renders a
dead timeframe button.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from tradingagents.contracts import OHLCVBar, Timeframe
from tradingagents.pro.ingestion.pyth import (
    PYTH_SYMBOLS as _PYTH_SYMBOLS,
    PythPriceVenueVolumeFeed,
)

TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
}

MAX_LIMIT = 1000
DEFAULT_LIMIT = 300

#: Hard cap on cached bar windows. The live set is 9 symbols × up to 8
#: timeframes = 72 entries at ~300 bars each (~26 MB); the rest of the
#: budget absorbs chart paging without letting it grow without end. At
#: ~368 KB per entry this ceiling is roughly 47 MB.
DEFAULT_CACHE_ENTRIES = 128


#: crypto is the only asset class with a real spot/perp volume print worth
#: overlaying onto Pyth's prices. FX and metals have no meaningful
#: consolidated spot volume, so there the oracle's "unknown" is the honest
#: answer rather than a loss.
_VOLUME_FROM_VENUE = frozenset({"BTC-USD", "ETH-USD", "SOL-USD"})


def _pyth_priced(venue_factory, symbol: str):
    """Wrap a venue feed so BARS come from Pyth and everything else — live
    ticks, quotes, probes — still comes from the venue. Symbols Pyth does
    not carry keep the venue feed untouched."""
    if symbol not in _PYTH_SYMBOLS:
        return venue_factory
    with_volume = symbol in _VOLUME_FROM_VENUE
    return lambda: PythPriceVenueVolumeFeed(venue_factory(), symbol,
                                            with_volume=with_volume)


def _pyth_source(venue_source: str, symbol: str) -> str:
    """Provenance shown in the UI: name BOTH halves, always.

    The venue stays part of the data path even when it supplies no volume —
    it serves the live tick stream and quotes, and it is the fallback when
    Pyth is unreachable. Collapsing that to a bare "pyth" would hide which
    vendor is actually behind the symbol, which is exactly the provenance
    this field exists to show.
    """
    if symbol not in _PYTH_SYMBOLS:
        return venue_source
    return f"pyth+{venue_source}"


class UnknownSymbolError(KeyError):
    pass


class UnsupportedTimeframeError(ValueError):
    def __init__(self, symbol: str, timeframe: Timeframe, supported: Sequence[Timeframe]):
        self.supported = tuple(supported)
        super().__init__(
            f"{symbol} does not support {timeframe.value}; "
            f"available: {[t.value for t in self.supported]}"
        )


# Pyth symbology — re-exported from the feed module so the TradingView
# chart (/api/tv/history) and the pipeline's decision bars are guaranteed
# to read the same price series. Symbols without an entry have no Pyth
# source and keep their venue feed.
PYTH_SYMBOLS = _PYTH_SYMBOLS


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str                       # dashboard-facing name (BTC-USD, XAUUSD)
    vendor_symbol: str                # what the feed expects (BTCUSDT, GC=F)
    source: str                       # feed name for provenance display
    timeframes: tuple[Timeframe, ...]
    live: bool                        # true when a streaming transport exists
    feed_factory: Callable[[], object]
    tradeable: bool = False           # pipeline + venue support exists

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "vendor_symbol": self.vendor_symbol,
            "source": self.source,
            "timeframes": [t.value for t in self.timeframes],
            "live": self.live,
            "tradeable": self.tradeable,
            "pyth_symbol": PYTH_SYMBOLS.get(self.symbol),
        }


def default_registry() -> dict[str, SymbolSpec]:
    """Vendor preference is probe-gated, never assumed: Delta Exchange
    first for BTC (BTCUSD perp) and gold (XAUTUSD, Tether Gold ≈ spot)
    — the operator's reachable venue; else Binance for BTC and OANDA/
    yfinance for gold. Degradation is disclosed, never faked."""
    import logging

    from tradingagents.pro.ingestion.binance import BinanceSpotFeed
    from tradingagents.pro.ingestion.delta_exchange import DeltaExchangeFeed
    from tradingagents.pro.ingestion.gold_feeds import YFinanceDailyBarsFeed
    from tradingagents.pro.ingestion.oanda_gold import OandaFeed, OandaGoldFeed

    logger = logging.getLogger(__name__)
    delta_alive = DeltaExchangeFeed.probe()
    # one probe validates the token for gold AND the FX majors (token
    # validity is instrument-independent). Probed via the OandaGoldFeed
    # name so existing probe monkeypatches/overrides keep working.
    oanda_alive = OandaGoldFeed.configured() and OandaGoldFeed.probe()
    if OandaGoldFeed.configured() and not oanda_alive:
        logger.warning(
            "OANDA_API_TOKEN is set but the API rejected it — "
            "falling back to yfinance daily bars (gold + FX)"
        )
    if delta_alive:
        logger.info("Delta Exchange reachable — serving BTC-USD (BTCUSD perp) "
                    "and XAUUSD (XAUTUSD Tether Gold) live")

    # crypto perps share the Delta-first / Binance-fallback wiring
    def crypto_spec(symbol: str, delta_sym: str, binance_sym: str) -> SymbolSpec:
        if delta_alive:
            return SymbolSpec(
                symbol=symbol,
                vendor_symbol=delta_sym,
                source=_pyth_source("delta_exchange", symbol),
                timeframes=tuple(TIMEFRAME_SECONDS),
                live=True,
                feed_factory=_pyth_priced(DeltaExchangeFeed, symbol),
                tradeable=True,
            )
        return SymbolSpec(
            symbol=symbol,
            vendor_symbol=binance_sym,
            source=_pyth_source("binance_spot", symbol),
            timeframes=tuple(TIMEFRAME_SECONDS),
            live=True,
            feed_factory=_pyth_priced(BinanceSpotFeed, symbol),
            tradeable=True,
        )

    registry = {
        "BTC-USD": crypto_spec("BTC-USD", "BTCUSD", "BTCUSDT"),
        "ETH-USD": crypto_spec("ETH-USD", "ETHUSD", "ETHUSDT"),
        "SOL-USD": crypto_spec("SOL-USD", "SOLUSD", "SOLUSDT"),
    }
    # daily-only cross-asset series (correlation matrix, context charts)
    for symbol, vendor in (("DXY", "DX-Y.NYB"), ("SILVER", "SI=F"),
                           ("US10Y", "^TNX")):
        registry[symbol] = SymbolSpec(
            symbol=symbol,
            vendor_symbol=vendor,
            source="yfinance_daily",
            timeframes=(Timeframe.D1,),
            live=False,
            feed_factory=YFinanceDailyBarsFeed,
        )
    if delta_alive:
        registry["XAUUSD"] = SymbolSpec(
            symbol="XAUUSD",
            vendor_symbol="XAUTUSD",  # Tether Gold, ≈ spot (small basis)
            source=_pyth_source("delta_exchange", "XAUUSD"),
            timeframes=tuple(TIMEFRAME_SECONDS),
            live=True,
            feed_factory=_pyth_priced(DeltaExchangeFeed, "XAUUSD"),
            tradeable=True,
        )
    elif oanda_alive:
        registry["XAUUSD"] = SymbolSpec(
            symbol="XAUUSD",
            vendor_symbol="XAU_USD",
            source=_pyth_source("oanda_gold", "XAUUSD"),
            timeframes=tuple(OandaGoldFeed.GRANULARITY),
            live=True,
            feed_factory=_pyth_priced(OandaGoldFeed, "XAUUSD"),
            tradeable=True,
        )
    else:
        registry["XAUUSD"] = SymbolSpec(
            symbol="XAUUSD",
            vendor_symbol="GC=F",
            source=_pyth_source("yfinance_daily", "XAUUSD"),
            timeframes=(Timeframe.D1,),
            live=False,
            feed_factory=_pyth_priced(YFinanceDailyBarsFeed, "XAUUSD"),
            tradeable=True,
        )

    # FX majors (P2-10): live intraday via OANDA when the token works,
    # else yfinance daily (=X tickers) — same disclosed degradation as gold
    def fx_spec(symbol: str, oanda_sym: str, yf_sym: str) -> SymbolSpec:
        if oanda_alive:
            return SymbolSpec(
                symbol=symbol,
                vendor_symbol=oanda_sym,
                source=_pyth_source("oanda", symbol),
                timeframes=tuple(OandaFeed.GRANULARITY),
                live=True,
                feed_factory=_pyth_priced(OandaFeed, symbol),
                tradeable=True,
            )
        return SymbolSpec(
            symbol=symbol,
            vendor_symbol=yf_sym,
            source=_pyth_source("yfinance_daily", symbol),
            timeframes=(Timeframe.D1,),
            live=False,
            feed_factory=_pyth_priced(YFinanceDailyBarsFeed, symbol),
            tradeable=True,
        )

    registry["EURUSD"] = fx_spec("EURUSD", "EUR_USD", "EURUSD=X")
    registry["USDJPY"] = fx_spec("USDJPY", "USD_JPY", "USDJPY=X")
    return registry


class MarketDataService:
    def __init__(
        self,
        registry: dict[str, SymbolSpec] | None = None,
        ttl_floor: float = 5.0,
        ttl_cap: float = 300.0,
        now: Callable[[], float] = time.monotonic,
        max_entries: int = DEFAULT_CACHE_ENTRIES,
    ):
        self._registry = registry
        self.ttl_floor = ttl_floor
        self.ttl_cap = ttl_cap
        self._now = now
        self.max_entries = max(8, max_entries)
        # key: (symbol, timeframe, end_bucket|None) — None is the live window.
        # OrderedDict, not dict: this is an LRU with a hard cap. TTL only
        # governs STALENESS, never residency — a stale entry is overwritten
        # only if that exact key is asked for again, so an uncapped dict
        # grew forever. Paged windows (end != None) are the leak: chart
        # "load more" mints one entry per scroll position, and a backtest
        # pages backwards at 1000 bars (~1.2 MB) per entry, none of which
        # was ever released.
        self._cache: OrderedDict[
            tuple[str, Timeframe, int | None], tuple[float, list[OHLCVBar]]
        ] = OrderedDict()
        self._feeds: dict[str, object] = {}
        self._lock = threading.Lock()
        self._flights: dict[tuple[str, Timeframe, int | None], threading.Lock] = {}

    @property
    def registry(self) -> dict[str, SymbolSpec]:
        if self._registry is None:
            self._registry = default_registry()  # lazy: env read at first use
        return self._registry

    def symbols(self) -> list[dict]:
        return [spec.as_dict() for spec in self.registry.values()]

    def spec(self, symbol: str) -> SymbolSpec:
        try:
            return self.registry[symbol]
        except KeyError:
            raise UnknownSymbolError(symbol) from None

    def _ttl(self, timeframe: Timeframe) -> float:
        return min(max(TIMEFRAME_SECONDS[timeframe] / 2, self.ttl_floor), self.ttl_cap)

    def _feed(self, spec: SymbolSpec):
        with self._lock:
            if spec.symbol not in self._feeds:
                self._feeds[spec.symbol] = spec.feed_factory()
            return self._feeds[spec.symbol]

    def get_bars(
        self, symbol: str, timeframe: Timeframe, limit: int = DEFAULT_LIMIT,
        end: datetime | None = None,
    ) -> list[OHLCVBar]:
        """Latest ``limit`` bars, or the ``limit`` bars ending strictly
        before ``end`` (history paging — the chart's "load more"). The live
        (end=None) window keeps its short TTL cache + single-flight; paged
        windows key the cache by the end bucket so scrolling back never
        evicts the hot live window, and older history (immutable once
        closed) caches for the full cap."""
        spec = self.spec(symbol)
        if timeframe not in spec.timeframes:
            raise UnsupportedTimeframeError(symbol, timeframe, spec.timeframes)
        limit = max(1, min(limit, MAX_LIMIT))
        # paged windows bucket by end-epoch so distinct scroll positions are
        # distinct cache entries; the live window (end=None) is unchanged
        end_bucket = int(end.timestamp()) if end is not None else None
        key = (symbol, timeframe, end_bucket)
        ttl = self._ttl(timeframe) if end is None else self.ttl_cap

        with self._lock:
            cached = self._cache.get(key)
            if cached and self._now() - cached[0] < ttl:
                self._cache.move_to_end(key)  # LRU: a hit is a recent use
                return cached[1][-limit:]
            flight = self._flights.setdefault(key, threading.Lock())

        try:
            with flight:  # single-flight: one vendor call per key
                with self._lock:
                    cached = self._cache.get(key)
                    if cached and self._now() - cached[0] < ttl:
                        self._cache.move_to_end(key)
                        return cached[1][-limit:]
                bars = self._feed(spec).get_bars(
                    spec.vendor_symbol, timeframe,
                    limit=max(limit, DEFAULT_LIMIT), end=end,
                )
                with self._lock:
                    self._cache[key] = (self._now(), list(bars))
                    self._cache.move_to_end(key)
                    self._evict_locked()
                return list(bars)[-limit:]
        finally:
            # a lock per key, kept forever, leaked alongside the key it
            # guarded — same unbounded growth as the cache itself
            with self._lock:
                held = self._flights.get(key)
                if held is not None and not held.locked():
                    self._flights.pop(key, None)

    def _evict_locked(self) -> None:
        """Drop the least-recently-used windows past the cap. Call holding
        ``self._lock``."""
        while len(self._cache) > self.max_entries:
            self._cache.popitem(last=False)


def bars_view(bars: Sequence[OHLCVBar]) -> list[dict]:
    """Lightweight-Charts-native rows (unix-seconds time)."""
    return [
        {
            "time": int(bar.start.timestamp()),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]


def indicator_series_view(bars: Sequence[OHLCVBar], names: Sequence[str]) -> dict:
    """Aligned {time, value} points per indicator line, warm-up Nones dropped."""
    from tradingagents.pro.ingestion.indicators import compute_indicator_series

    times = [int(bar.start.timestamp()) for bar in bars]
    result = {}
    for name, block in compute_indicator_series(bars, names).items():
        lines = {
            key: [
                {"time": t, "value": v}
                for t, v in zip(times, values, strict=True)
                if v is not None
            ]
            for key, values in block["series"].items()
        }
        result[name] = {"params": block["params"], "series": lines}
    return result
