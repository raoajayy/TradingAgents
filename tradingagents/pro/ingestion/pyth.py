"""Pyth Network price bars, fetched through the UDF history proxy.

Pyth is an oracle: it publishes an aggregate of many publishers' prices for
a symbol. That makes it a single, consistent PRICE source across asset
classes this system otherwise stitched together from four vendors
(yfinance, OANDA, Binance, Delta) — each with its own symbology, outages,
and bar conventions.

Two properties of an oracle drive the design here:

1. **No volume.** Pyth aggregates price, not venue flow, and returns ``v``
   as zeros. This feed therefore reports volume as *unknown* rather than
   passing zeros off as real: ``get_bars`` leaves volume at 0.0 (the
   contract requires a float) and callers that have a genuine venue volume
   source overlay it with :func:`merge_volume`. Crypto keeps real volume
   from the exchange feeds; FX and metals have no meaningful spot volume to
   begin with, so zero there is honest rather than lossy.

2. **Aggregate, not a venue print.** Pyth prices are not executable and
   need not match the venue an order routes to. Bars from here drive
   ANALYSIS; execution still reconciles against the venue adapter.

Access goes through the UDF proxy (``PRO_TV_HISTORY_BASE``), the same
upstream the TradingView chart already uses, so the whole system reads one
price series and the chart cannot disagree with the decision data.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from tradingagents.contracts import OHLCVBar, Timeframe
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.pro.ingestion.base import (
    HttpTransport,
    RequestsTransport,
    build_bars,
)

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://thefundedroom.com"

# Pyth symbology, canonical home. marketdata.PYTH_SYMBOLS re-exports this
# so the chart and the pipeline can never drift onto different feeds.
PYTH_SYMBOLS: dict[str, str] = {
    "BTC-USD": "Crypto.BTC/USD",
    "ETH-USD": "Crypto.ETH/USD",
    "SOL-USD": "Crypto.SOL/USD",
    "XAUUSD": "Metal.XAU/USD",
    "EURUSD": "FX.EUR/USD",
    "USDJPY": "FX.USD/JPY",
}

#: Timeframe -> UDF resolution understood by the history endpoint
_RESOLUTION: dict[Timeframe, str] = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.H4: "240",
    Timeframe.D1: "D",
    Timeframe.W1: "W",
}

#: seconds per bar, used to size the lookback window for `limit` bars
_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1800,
    Timeframe.H1: 3600,
    Timeframe.H4: 14400,
    Timeframe.D1: 86400,
    Timeframe.W1: 604800,
}


class PythBarsFeed:
    """OHLC bars from Pyth via the UDF history proxy.

    ``symbol`` is the dashboard name (BTC-USD, XAUUSD); it is translated
    through :data:`PYTH_SYMBOLS`, so vendor symbology never leaks out.
    """

    name = "pyth"

    def __init__(self, transport: HttpTransport | None = None,
                 base: str | None = None):
        self._base = (base or os.environ.get("PRO_TV_HISTORY_BASE")
                      or DEFAULT_BASE).rstrip("/")
        self._transport = transport or RequestsTransport(timeout=15.0)

    @staticmethod
    def supports(symbol: str) -> bool:
        return symbol in PYTH_SYMBOLS

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        limit: int = 250,
        end: datetime | None = None,
    ) -> list[OHLCVBar]:
        pyth_symbol = PYTH_SYMBOLS.get(symbol)
        if pyth_symbol is None:
            raise NoMarketDataError(symbol, detail="no Pyth feed id for this symbol")
        resolution = _RESOLUTION.get(timeframe)
        if resolution is None:
            raise ValueError(f"{self.name} does not support {timeframe.value}")

        to_ts = int((end or datetime.now(timezone.utc)).timestamp())
        # over-fetch the window: sessions close, so N calendar periods yield
        # fewer than N bars for FX/metals (~213 daily bars per 250 days)
        span = _SECONDS[timeframe] * max(limit, 1)
        from_ts = to_ts - int(span * 1.6)

        payload = self._transport.get_json(
            f"{self._base}/api/pyth/history",
            {"symbol": pyth_symbol, "resolution": resolution,
             "from": from_ts, "to": to_ts},
        )
        if not isinstance(payload, dict):
            raise NoMarketDataError(symbol, detail="malformed history payload")
        status = payload.get("s")
        if status == "no_data":
            raise NoMarketDataError(
                symbol, canonical=pyth_symbol,
                detail=f"Pyth returned no bars for {timeframe.value}")
        if status != "ok":
            raise NoMarketDataError(
                symbol, canonical=pyth_symbol,
                detail=f"Pyth history error: {payload.get('errmsg', status)!r}")

        times = payload.get("t") or []
        opens, highs = payload.get("o") or [], payload.get("h") or []
        lows, closes = payload.get("l") or [], payload.get("c") or []
        if not times or not (len(times) == len(opens) == len(highs)
                             == len(lows) == len(closes)):
            raise NoMarketDataError(
                symbol, canonical=pyth_symbol,
                detail="Pyth history arrays were empty or ragged")

        def rows():
            for i, ts in enumerate(times):
                yield {
                    "timeframe": timeframe,
                    "start": datetime.fromtimestamp(int(ts), tz=timezone.utc),
                    "open": float(opens[i]),
                    "high": float(highs[i]),
                    "low": float(lows[i]),
                    "close": float(closes[i]),
                    # oracle: no venue flow. merge_volume() overlays a real
                    # source where one exists; see the module docstring.
                    "volume": 0.0,
                }

        bars = build_bars(rows(), feed=self.name, symbol=symbol)
        if not bars:
            raise NoMarketDataError(symbol, canonical=pyth_symbol,
                                    detail="no usable bars after validation")
        return bars[-limit:]


class PythPriceVenueVolumeFeed:
    """Pyth prices, venue volume, everything else delegated to the venue.

    The registry's ``feed_factory`` result serves more than bars — the live
    tick stream and quotes come off the same object — so this overrides
    ONLY ``get_bars`` and passes every other attribute through to the
    venue feed. Replacing the whole feed would have silently killed live
    ticks.

    If Pyth is unreachable the venue feed answers alone: a price-oracle
    outage must degrade to the old behavior, not to no chart at all.
    """

    def __init__(self, venue_feed, dashboard_symbol: str,
                 pyth: "PythBarsFeed | None" = None,
                 *, with_volume: bool = True):
        self.venue = venue_feed
        # callers pass the VENDOR symbol (BTCUSDT, XAU_USD, GC=F) — that is
        # what the venue feed wants, but Pyth is keyed by the dashboard
        # name, so the mapping is fixed at construction rather than guessed
        # from the incoming symbol.
        self.dashboard_symbol = dashboard_symbol
        self.pyth = pyth or PythBarsFeed()
        self.with_volume = with_volume
        self.name = f"pyth+{getattr(venue_feed, 'name', 'venue')}"

    def get_bars(self, symbol: str, timeframe: Timeframe, *,
                 limit: int = 250, end: datetime | None = None) -> list[OHLCVBar]:
        try:
            bars = self.pyth.get_bars(self.dashboard_symbol, timeframe,
                                      limit=limit, end=end)
        except Exception:
            logger.warning("pyth bars unavailable for %s %s; falling back to %s",
                           self.dashboard_symbol, timeframe.value,
                           getattr(self.venue, "name", "venue"), exc_info=True)
            return self.venue.get_bars(symbol, timeframe, limit=limit, end=end)
        if not self.with_volume:
            return bars
        try:
            venue_bars = self.venue.get_bars(symbol, timeframe,
                                             limit=limit, end=end)
        except Exception:
            logger.warning("venue volume unavailable for %s; prices ship with "
                           "volume unknown", self.dashboard_symbol, exc_info=True)
            return bars
        return merge_volume(bars, venue_bars)

    def __getattr__(self, name: str):
        # quotes, tick streams, probes — anything this class does not
        # implement belongs to the venue feed
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(object.__getattribute__(self, "venue"), name)


def merge_volume(price_bars: list[OHLCVBar],
                 volume_bars: list[OHLCVBar]) -> list[OHLCVBar]:
    """Overlay real venue volume onto Pyth's price bars, matched on time.

    Pyth carries the prices every agent reasons about; the exchange feed
    contributes only the one field an oracle cannot know. Bars with no
    matching venue bar keep volume 0.0 — the same "unknown" the rest of the
    system already sees for FX and metals, never a guess.
    """
    by_start = {bar.start: bar.volume for bar in volume_bars}
    merged: list[OHLCVBar] = []
    matched = 0
    for bar in price_bars:
        volume = by_start.get(bar.start)
        if volume is None or volume <= 0:
            merged.append(bar)
            continue
        matched += 1
        merged.append(bar.model_copy(update={"volume": volume}))
    if price_bars and not matched:
        logger.warning(
            "no venue volume matched Pyth bar times (%d price bars, %d volume "
            "bars); volume stays unknown", len(price_bars), len(volume_bars))
    return merged
