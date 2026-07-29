"""Market-intelligence aggregation for the dashboard.

One endpoint pulls the free feeds the pipeline already trusts —
derivatives (funding/OI/mark), Fear & Greed, gold cross-asset
(DXY/US10Y/silver correlation), FRED macro, session — behind a shared TTL
cache. Each feed fails independently: an exception becomes an entry in
``missing_feeds`` (the MarketSnapshot disclosure convention) and the rest
still serve. The UI renders gaps honestly; it never fakes a reading.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable

from tradingagents.contracts import utc_now

logger = logging.getLogger(__name__)

CORRELATION_SYMBOLS = ("BTC-USD", "XAUUSD", "DXY", "SILVER", "US10Y")


def _friendly_error(exc: Exception) -> str:
    """One short human line for the UI; the raw exception goes to logs.

    Vendor exceptions embed full request URLs and repr noise (the review
    caught a raw '403 Client Error: Forbidden for url: https://...' on the
    Intel page) — the dashboard shows the kind of failure, never the
    plumbing."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        hints = {401: "auth rejected", 403: "forbidden — subscription or rate limit?",
                 404: "endpoint missing", 429: "rate limited"}
        hint = hints.get(status, "server error" if status >= 500 else "request rejected")
        return f"HTTP {status} ({hint})"
    name = type(exc).__name__
    if "Timeout" in name:
        return "timed out"
    if "Connection" in name:
        return "unreachable"
    # deliberate, short messages ("FRED_API_KEY not set") stay — minus any
    # embedded URLs; anything long is vendor plumbing and shows its kind
    text = re.sub(r"https?://\S+", "", str(exc)).strip(" ;:,-")
    if text and len(text) <= 80:
        return text
    return name


def correlation_matrix(marketdata, symbols, window: int = 30,
                       deadline: float = 10.0) -> dict:
    """Pairwise Pearson correlations of daily log returns (deterministic
    math on close prices — the UI renders, never computes). Calendars are
    aligned on shared dates (BTC trades weekends, gold doesn't); symbols
    without enough overlapping data are disclosed, never zero-filled.
    Bars fetch in parallel under a deadline — one blackholed vendor must
    not make the matrix take minutes."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

    import numpy as np
    import pandas as pd

    from tradingagents.contracts import Timeframe

    closes: dict[str, pd.Series] = {}
    missing: list[str] = []
    pool = ThreadPoolExecutor(max_workers=len(symbols) or 1,
                              thread_name_prefix="corr")
    futures = {
        symbol: pool.submit(
            marketdata.get_bars, symbol, Timeframe.D1, window + 10
        )
        for symbol in symbols
    }
    started = time.monotonic()
    for symbol, future in futures.items():
        remaining = max(0.05, deadline - (time.monotonic() - started))
        try:
            bars = future.result(timeout=remaining)
            closes[symbol] = pd.Series(
                [b.close for b in bars],
                index=[b.start.date() for b in bars],
            )
        except FutureTimeout:
            future.cancel()
            missing.append(f"{symbol}: no response within {deadline:.0f}s")
        except Exception as exc:
            logger.warning("correlation bars for %s failed", symbol, exc_info=True)
            missing.append(f"{symbol}: {_friendly_error(exc)}")
    pool.shutdown(wait=False)  # never join a blackholed fetch

    matrix: dict[str, dict[str, float]] = {}
    used_days = 0
    if len(closes) >= 2:
        frame = pd.DataFrame(closes).sort_index().dropna()
        returns = np.log(frame / frame.shift(1)).dropna().tail(window)
        used_days = len(returns)
        if used_days >= 5:
            corr = returns.corr()
            matrix = {
                a: {b: round(float(corr.loc[a, b]), 3) for b in corr.columns}
                for a in corr.index
            }
        else:
            missing.append(
                f"only {used_days} overlapping daily returns; need >= 5"
            )
    return {
        "window": window,
        "used_days": used_days,
        "symbols": [s for s in symbols if s in matrix],
        "matrix": matrix,
        "missing": missing,
        "as_of": utc_now().isoformat(),
    }


# Data dictionary (trader review): every tile says WHICH series it is and
# what it means — a mislabeled series (PPIACO shown as "PPI") once steered
# the macro debate. Keys are metric names; absent keys fall back to the
# raw name in the UI.
METRIC_INFO: dict[str, dict[str, str]] = {
    "TT_FEES_USD_24H": {"label": "Protocol fees (24h)",
                        "note": "Token Terminal daily fees, USD — the chain's "
                                "cash-flow proxy (free tier, keyed)."},
    "TT_ACTIVE_USERS_24H": {"label": "Active users (24h)",
                            "note": "Token Terminal daily active users — usage "
                                    "fundamentals (free tier, keyed)."},
    "FED_FUNDS_RATE": {"label": "Fed funds rate",
                       "note": "Effective federal funds rate (FRED DFF)."},
    "US10Y": {"label": "US 10Y yield",
              "note": "10-year Treasury constant maturity (FRED DGS10)."},
    "US10Y_REAL": {"label": "US 10Y real yield",
                   "note": "10-year TIPS yield (FRED DFII10) — gold's opportunity cost."},
    "DXY_BROAD": {"label": "Dollar index (broad)",
                  "note": "Trade-weighted broad dollar index (FRED DTWEXBGS)."},
    "CPI_YOY": {"label": "CPI YoY",
                "note": "Headline consumer inflation, year over year (FRED CPIAUCSL)."},
    "PPI_YOY": {"label": "PPI YoY (final demand)",
                "note": "Headline producer prices, final demand (FRED PPIFIS) — "
                        "the series traders quote as 'PPI'."},
    "NFP_CHANGE": {"label": "NFP change",
                   "note": "Monthly change in nonfarm payrolls, thousands (FRED PAYEMS)."},
    "GDP_YOY": {"label": "GDP YoY", "note": "Nominal GDP, year over year (FRED GDP)."},
    "FUNDING_RATE": {"label": "Funding rate",
                     "note": "Perpetual funding per 8h — positive = longs pay shorts."},
    "OPEN_INTEREST": {"label": "Open interest",
                      "note": "Outstanding perp contracts on the venue."},
    "FEAR_GREED_INDEX": {"label": "Fear & Greed",
                         "note": "alternative.me crypto sentiment index, 0 (fear) – 100 (greed)."},
    "GOLD_COT_NET_NONCOMM": {"label": "Gold COT net (non-comm)",
                             "note": "CFTC net non-commercial gold futures positioning."},
    "GOLD_COT_NET_PCT_OI": {"label": "Gold COT net % of OI",
                            "note": "Net speculative positioning as % of open interest."},
    "GOLD_COT_NET_CHANGE_1W": {"label": "Gold COT 1w change",
                               "note": "Week-over-week change in net positioning."},
    "GOLD_VOL_INDEX": {"label": "Gold vol index",
                       "note": "CBOE gold ETF volatility index (GVZ)."},
    "GOLD_VOL_INDEX_CHANGE_1D": {"label": "Gold vol 1d change",
                                 "note": "Day-over-day change in GVZ."},
    "XAU_XAG_CORR_30D": {"label": "XAU/XAG corr 30d",
                         "note": "30-day gold–silver return correlation."},
    "DVOL": {"label": "BTC implied vol (DVOL)",
             "note": "Deribit 30-day annualized implied-volatility index."},
    "DVOL_CHANGE_1D": {"label": "DVOL 1d change",
                       "note": "~24h change in the Deribit DVOL index."},
    # P2-11 liquidation reconstruction — every entry repeats the sampling
    # disclaimer: Binance pushes AT MOST one forceOrder per second per
    # symbol, so these are intensity signals / floors, NEVER total volume.
    "LIQ_INTENSITY_1H": {
        "label": "Liquidation intensity (1h)",
        "note": "Sampled Binance forceOrder events in the trailing hour — "
                "sampled (max one order/second), NOT total liquidation count."},
    "LIQ_NOTIONAL_1H": {
        "label": "Liquidation notional (1h)",
        "note": "Summed notional of SAMPLED liquidations, trailing hour — a "
                "floor, not total volume (Binance samples one forceOrder/s)."},
    "LIQ_BUY_SELL_RATIO_1H": {
        "label": "Liq buy/sell ratio (1h)",
        "note": "Sampled short-liq (BUY) vs long-liq (SELL) notional — "
                ">1 means shorts squeezed. Sampled, not total volume."},
    "OI_DELTA_1H": {
        "label": "Open interest Δ (1h)",
        "note": "Binance futures open-interest % change over ~1h "
                "(polled per minute)."},
    "GOLD_ETF_FLOWS_TONNES": {
        "label": "Gold ETF flows (monthly)",
        "note": "Global gold-ETF net flows, tonnes (WGC Goldhub, monthly CSV)."},
    "CB_GOLD_NET_PURCHASES_TONNES": {
        "label": "Central-bank gold buying (monthly)",
        "note": "Central-bank net gold purchases, tonnes (WGC Goldhub, monthly CSV)."},
}


def _reading_view(reading) -> dict:
    info = METRIC_INFO.get(reading.name, {})
    return {
        "name": reading.name,
        "label": info.get("label"),
        "note": info.get("note"),
        "value": reading.value,
        "unit": reading.unit,
        "as_of": reading.as_of.isoformat() if reading.as_of else None,
        "source": reading.source,
    }


class IntelService:
    def __init__(
        self,
        feeds: dict[str, Callable[[], list]] | None = None,
        calendar_source: Callable[[int], list[dict]] | None = None,
        news_fns: dict[str, Callable[[], list]] | None = None,
        ttl: float = 60.0,
        calendar_ttl: float = 6 * 3600.0,
        deadline: float = 10.0,
        now: Callable[[], float] = time.monotonic,
        liquidations=None,  # LiquidationStream | None (P2-11; injectable)
    ):
        self._feeds = feeds
        self._liquidations = liquidations
        self._calendar_source = calendar_source
        self._news_fns = news_fns
        self.ttl = ttl
        self.calendar_ttl = calendar_ttl
        self.deadline = deadline
        self._now = now
        self._lock = threading.Lock()
        self._pool = None  # lazy ThreadPoolExecutor, shared across snapshots
        self._cached: tuple[float, dict] | None = None
        self._cached_calendar: tuple[float, dict] | None = None
        self._cached_correlations: dict[tuple, tuple[float, dict]] = {}

    # --- default wiring (built lazily so tests never construct real feeds) --------

    @property
    def feeds(self) -> dict[str, Callable[[], list]]:
        if self._feeds is None:
            from tradingagents.pro.dashboard.prefs import default_data_dir
            from tradingagents.pro.ingestion.binance import (
                BinanceDerivativesFeed,
                BinanceSpotFeed,
            )
            from tradingagents.pro.ingestion.delta_exchange import (
                DeltaExchangeFeed,
            )
            from tradingagents.pro.ingestion.deribit import DeribitVolFeed
            from tradingagents.pro.ingestion.fred_macro import FredMacroFeed
            from tradingagents.pro.ingestion.gold_feeds import (
                GoldCrossAssetFeed,
                YFinanceDailyBarsFeed,
            )
            from tradingagents.pro.ingestion.goldhub import (
                GOLDHUB_CSV_NAME,
                GoldhubCsvFeed,
            )
            from tradingagents.pro.ingestion.liquidations import (
                LiquidationStream,
            )
            from tradingagents.pro.ingestion.onchain import (
                CoinMetricsFeed,
                FearGreedFeed,
            )
            from tradingagents.pro.ingestion.positioning import (
                GoldCotFeed,
                GoldVolFeed,
            )
            from tradingagents.pro.ingestion.token_terminal import (
                TokenTerminalFeed,
            )

            derivatives = BinanceDerivativesFeed()
            spot = BinanceSpotFeed()
            delta = DeltaExchangeFeed()
            yf_daily = YFinanceDailyBarsFeed()
            if self._liquidations is None:
                # autostart: constructing here opens NO sockets — the WS +
                # OI threads start on the first get_metrics call (i.e. the
                # first real snapshot), so imports/tests stay hermetic
                self._liquidations = LiquidationStream(autostart=True)
            self._feeds = {
                "delta_derivatives": lambda: delta.get_metrics("BTCUSD"),
                "binance_derivatives": derivatives.get_metrics,
                "orderbook_imbalance":
                    lambda: [spot.get_orderbook_imbalance("BTCUSDT")],
                "fear_greed": FearGreedFeed().get_metrics,
                "coinmetrics": CoinMetricsFeed().get_metrics,
                "gold_cross_asset":
                    GoldCrossAssetFeed(yf_daily).get_metrics,
                "fred_macro": FredMacroFeed().get_metrics,
                "gold_cot": GoldCotFeed(
                    cache_path=default_data_dir() / "cot_cache.json"
                ).get_metrics,
                "gold_vol": GoldVolFeed(yf_daily).get_metrics,
                "deribit_dvol": DeribitVolFeed().get_metrics,
                "goldhub": GoldhubCsvFeed(
                    default_data_dir() / GOLDHUB_CSV_NAME
                ).get_metrics,
                "token_terminal": TokenTerminalFeed().get_metrics,
                # P2-11 sampled liquidations + OI deltas; NoMarketDataError
                # while warming up becomes a missing_feeds line
                "binance_liquidations": self._liquidations.get_metrics,
            }
        return self._feeds

    def _calendar_fetch(self, days: int) -> list[dict]:
        if self._calendar_source is not None:
            return self._calendar_source(days)
        from tradingagents.pro.ingestion.fred_macro import FredMacroFeed

        return FredMacroFeed().get_release_dates(days_ahead=days)

    @property
    def news_fns(self) -> dict[str, Callable[[], list]]:
        """Headline sources per symbol — the same feeds the pipeline's
        sentiment team reads, surfaced in the UI (trader review P1.3:
        headlines were ingested somewhere and displayed nowhere)."""
        if self._news_fns is None:
            from tradingagents.pro.ingestion.news import YahooFinanceNewsFeed

            self._news_fns = {
                "XAUUSD": YahooFinanceNewsFeed("GC=F").get_news,
                "BTC-USD": YahooFinanceNewsFeed("BTC-USD").get_news,
            }
        return self._news_fns

    # --- views ---------------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            if self._cached and self._now() - self._cached[0] < self.ttl:
                return self._cached[1]

        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

        from tradingagents.pro.ingestion.sessions import current_session

        # feeds fetch in parallel under a hard deadline: one blackholed
        # vendor (blocked egress hangs, not errors) must never make the
        # whole intelligence view take minutes — it becomes a missing_feeds
        # line instead
        metrics: list[dict] = []
        missing: list[str] = []
        feeds = dict(self.feeds)
        # persistent pool: a `with` block would join hanging threads on
        # exit, re-introducing the very stall the deadline exists to stop.
        # A blackholed fetch keeps its worker busy until the transport
        # timeout; the TTL cache bounds how often that can pile up.
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=8,
                                                thread_name_prefix="intel")
            pool = self._pool
        futures = {name: pool.submit(fetch) for name, fetch in feeds.items()}
        started = time.monotonic()
        for feed_name, future in futures.items():
            remaining = max(0.05, self.deadline - (time.monotonic() - started))
            try:
                readings = future.result(timeout=remaining)
                metrics.extend(_reading_view(r) for r in readings)
            except FutureTimeout:
                future.cancel()
                missing.append(f"{feed_name}: no response within "
                               f"{self.deadline:.0f}s (vendor unreachable?)")
            except Exception as exc:
                logger.warning("intel feed %s failed", feed_name, exc_info=True)
                missing.append(f"{feed_name}: {_friendly_error(exc)}")

        # headlines ride the same pool + deadline; empty from a wired
        # source is disclosed, never silent (review P1.3)
        headlines: list[dict] = []
        news_futures = {sym: pool.submit(fn) for sym, fn in self.news_fns.items()}
        for sym, future in news_futures.items():
            remaining = max(0.05, 2 * self.deadline - (time.monotonic() - started))
            try:
                items = future.result(timeout=remaining)
                if not items:
                    missing.append(f"news:{sym}:empty")
                for item in items:
                    headlines.append({
                        "symbol": sym,
                        "headline": item.headline,
                        "source": item.source,
                        "published_at": (item.published_at.isoformat()
                                         if item.published_at else None),
                        "url": item.url,
                    })
            except FutureTimeout:
                future.cancel()
                missing.append(f"news:{sym}: no response within "
                               f"{self.deadline:.0f}s (vendor unreachable?)")
            except Exception as exc:
                logger.warning("news for %s failed", sym, exc_info=True)
                missing.append(f"news:{sym}: {_friendly_error(exc)}")
        headlines.sort(key=lambda h: h["published_at"] or "", reverse=True)

        # P2-11 heat strip: symbol → price buckets over the sampled-event
        # ring; null while the stream is inactive or empty. Magnitudes are
        # sampled floors — the UI repeats the disclaimer under the strip.
        liquidation_heatmap = None
        liq = self._liquidations
        if liq is not None and liq.active:
            try:
                buckets = liq.price_buckets()
                if buckets:
                    liquidation_heatmap = {liq.symbol: buckets}
            except Exception:
                logger.warning("liquidation heatmap failed", exc_info=True)

        view = {
            "as_of": utc_now().isoformat(),
            "session": current_session(utc_now()).value,
            "metrics": metrics,
            # P2-07 alert builder: the data dictionary's keys are the
            # metric vocabulary users can build condition alerts over
            "metric_keys": sorted(METRIC_INFO),
            "headlines": headlines,
            "liquidation_heatmap": liquidation_heatmap,
            "missing_feeds": missing,
            # honest map of what money hasn't bought yet (UX: trust signal)
            "unsubscribed_feeds": [
                # P2-11 serves SAMPLED liquidations free; full aggregated
                # liquidation volume remains a paid feed
                {"name": "liquidations_full", "provider": "Coinglass"},
                {"name": "whale_flows", "provider": "Glassnode"},
                {"name": "etf_flows", "provider": "Farside/SoSoValue"},
                {"name": "gold_microstructure", "provider": "Databento/Polygon"},
            ],
        }
        with self._lock:
            self._cached = (self._now(), view)
        return view

    def correlations(self, marketdata, window: int = 30,
                     symbols: tuple[str, ...] = CORRELATION_SYMBOLS) -> dict:
        window = max(5, min(window, 250))
        cache_key = (window, symbols)
        with self._lock:
            cached = self._cached_correlations.get(cache_key)
            if cached and self._now() - cached[0] < 3600.0:
                return cached[1]
        view = correlation_matrix(marketdata, symbols, window)
        with self._lock:
            self._cached_correlations[cache_key] = (self._now(), view)
        return view

    def calendar(self, days: int = 30) -> dict:
        """Release calendar with a LIVE next_major.

        The vendor fetch (release dates) caches for ``calendar_ttl`` —
        dates don't move intraday. ``next_major`` is time-sensitive and is
        recomputed from the cached release list on EVERY call: serving it
        frozen let a just-passed event mask the next one for hours, and
        the pipeline's event gate slept through FOMC's approach while the
        loop opened a gold short inside the declared window (trader review
        R2.1). ``next_major_event`` scans the full list and skips past
        instants, so look-ahead beyond the first event is inherent.
        """
        from tradingagents.pro.ingestion.econ_calendar import (
            enrich_calendar,
            next_major_event,
        )

        days = max(1, min(days, 90))
        with self._lock:
            cached = self._cached_calendar
        if cached and self._now() - cached[0] < self.calendar_ttl:
            view = dict(cached[1])
            view["next_major"] = next_major_event(
                view.get("releases") or [], utc_now())
            return view
        releases: list[dict] = []
        missing: list[str] = []
        try:
            releases = enrich_calendar(self._calendar_fetch(days))
        except Exception as exc:
            logger.warning("calendar fetch failed", exc_info=True)
            missing.append(f"fred_calendar: {_friendly_error(exc)}")
        view = {"releases": releases, "missing_feeds": missing,
                "as_of": utc_now().isoformat()}
        with self._lock:
            self._cached_calendar = (self._now(), view)
        return {**view, "next_major": next_major_event(releases, utc_now())}
