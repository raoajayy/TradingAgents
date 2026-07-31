"""SnapshotBuilder: compose feed adapters into a frozen MarketSnapshot.

The builder is the single place where feed failures are absorbed: a feed
that raises lands in ``missing_feeds`` (agents must treat it as unknown —
contract semantics), it never fabricates or interpolates values. Bars and
the indicator engine are the exception: without price data there is no
snapshot, so bar failures raise.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from tradingagents.contracts import (
    AssetClass,
    IndicatorReading,
    MarketSnapshot,
    MetricReading,
    OHLCVBar,
    Timeframe,
    TradingSession,
)
from tradingagents.pro.ingestion.base import BarsFeed, MetricsFeed, QuoteFeed
from tradingagents.pro.ingestion.binance import BinanceDerivativesFeed, BinanceSpotFeed
from tradingagents.pro.ingestion.fred_macro import FredMacroFeed
from tradingagents.pro.ingestion.gold_feeds import GoldCrossAssetFeed, YFinanceDailyBarsFeed
from tradingagents.pro.ingestion.indicators import DEFAULT_INDICATOR_NAMES, compute_indicators
from tradingagents.pro.ingestion.onchain import BlockchainComFeed, CoinMetricsFeed, FearGreedFeed
from tradingagents.pro.ingestion.sessions import current_session

logger = logging.getLogger(__name__)

SessionFn = Callable[[datetime], TradingSession]


class SnapshotBuilder:
    def __init__(
        self,
        *,
        bars_feed: BarsFeed,
        quote_feed: QuoteFeed | None = None,
        macro_feeds: Sequence[MetricsFeed] = (),
        onchain_feeds: Sequence[MetricsFeed] = (),
        extra_metric_fns: Sequence[Callable[[], MetricReading]] = (),
        indicator_names: Sequence[str] = DEFAULT_INDICATOR_NAMES,
        session_fn: SessionFn | None = None,
        news_feed=None,
        vintage_reader=None,
    ):
        self._bars_feed = bars_feed
        self._quote_feed = quote_feed
        self._macro_feeds = tuple(macro_feeds)
        self._onchain_feeds = tuple(onchain_feeds)
        self._extra_metric_fns = tuple(extra_metric_fns)
        self._indicator_names = tuple(indicator_names)
        self._session_fn = session_fn
        self._news_feed = news_feed
        # P3-02: anything exposing latest_as_known(name, at) -> row|None
        # (EventStore qualifies). Only consulted for explicit-as_of builds
        # (backtests/evals); the live path never touches it.
        self._vintage_reader = vintage_reader

    def build(
        self,
        symbol: str,
        asset: AssetClass,
        *,
        timeframes: Sequence[Timeframe] = (Timeframe.D1,),
        bar_limit: int = 250,
        as_of: datetime | None = None,
    ) -> MarketSnapshot:
        # an explicit as_of marks a point-in-time build (backtest/eval);
        # its macro readings must be replayed "as known then" (P3-02)
        pit_as_of = as_of
        as_of = as_of or datetime.now(timezone.utc)
        missing: list[str] = []

        bars: list[OHLCVBar] = []
        indicators: list[IndicatorReading] = []
        for timeframe in timeframes:
            tf_bars = self._bars_feed.get_bars(symbol, timeframe, limit=bar_limit, end=as_of)
            bars.extend(tf_bars)
            indicators.extend(compute_indicators(tf_bars, self._indicator_names))

        quote = None
        if self._quote_feed is not None:
            try:
                quote = self._quote_feed.get_quote(symbol)
            except Exception:
                logger.warning("quote feed %s failed", self._quote_feed.name, exc_info=True)
                missing.append(self._quote_feed.name)

        news = []
        if self._news_feed is not None:
            try:
                news = self._news_feed.get_news()
            except Exception:
                # a raising vendor degrades like any other feed (and, when
                # live-armed, blocks entries via the data-health gate)
                logger.warning("news feed %s failed", self._news_feed.name,
                               exc_info=True)
                missing.append(self._news_feed.name)
            else:
                if not news:
                    # an empty result silently benched the entire news team
                    # in production (trader review P1.3: "NEWS_SENTIMENT (0)"
                    # with no disclosure anywhere — abstaining agents never
                    # emit the per-prompt "news missing" note). A wired feed
                    # returning nothing IS degradation; disclose it.
                    logger.warning("news feed %s returned no items",
                                   self._news_feed.name)
                    missing.append(f"{self._news_feed.name}:empty")

        macro = self._collect(self._macro_feeds, missing)
        if pit_as_of is not None and self._vintage_reader is not None:
            macro = self._apply_vintages(macro, pit_as_of)
        onchain = self._collect(self._onchain_feeds, missing)
        for fn in self._extra_metric_fns:
            try:
                onchain.append(fn())
            except Exception:
                name = getattr(fn, "__name__", repr(fn))
                logger.warning("extra metric %s failed", name, exc_info=True)
                missing.append(name)

        return MarketSnapshot(
            symbol=symbol,
            asset=asset,
            as_of=as_of,
            quote=quote,
            bars=bars,
            indicators=indicators,
            macro=macro,
            onchain=onchain,
            news=news,
            session=self._session_fn(as_of) if self._session_fn else None,
            missing_feeds=missing,
        )

    def _apply_vintages(
        self, readings: list[MetricReading], at: datetime
    ) -> list[MetricReading]:
        """P3-02 point-in-time read: replay macro metrics "as known at" ``at``.

        Per reading: a vintage knowable at ``at`` replaces it (revised data
        never leaks into the past); a metric the vintage store tracks but
        had not yet observed is dropped (it was not knowable then); a metric
        the store has never vintaged passes through unchanged (cross-asset
        correlations etc. have no vintage history — best effort beats
        gutting the snapshot).
        """
        has_vintages = getattr(self._vintage_reader, "has_vintages",
                               lambda _name: False)
        out: list[MetricReading] = []
        for reading in readings:
            try:
                row = self._vintage_reader.latest_as_known(reading.name, at)
            except Exception:
                logger.warning("vintage read failed for %s", reading.name,
                               exc_info=True)
                out.append(reading)
                continue
            if row is not None:
                out.append(MetricReading(
                    name=reading.name,
                    value=row["value"],
                    unit=reading.unit,
                    as_of=row["as_of"],
                    source=row.get("source") or reading.source,
                ))
            elif not has_vintages(reading.name):
                out.append(reading)
            # else: tracked series, nothing observed by ``at`` — drop
        return out

    @staticmethod
    def _collect(feeds: Sequence[MetricsFeed], missing: list[str]) -> list[MetricReading]:
        readings: list[MetricReading] = []
        for feed in feeds:
            try:
                readings.extend(feed.get_metrics())
            except Exception:
                logger.warning("metrics feed %s failed", feed.name, exc_info=True)
                missing.append(feed.name)
        return readings


def build_gold_pipeline(
    loader=None, transport=None, correlation_window: int = 30,
    cot_cache_path=None, goldhub_csv_path=None,
    vintage_sink=None, vintage_reader=None,
) -> SnapshotBuilder:
    """Default gold (XAU) pipeline: GC=F daily bars + cross-asset context +
    FRED macro + CFTC COT positioning + GVZ implied vol + Goldhub monthly
    demand (ETF flows / central-bank buying, when the CSV is present) +
    session awareness. All feeds free; FRED needs its free key."""
    from tradingagents.pro.ingestion.goldhub import GoldhubCsvFeed
    from tradingagents.pro.ingestion.news import YahooFinanceNewsFeed
    from tradingagents.pro.ingestion.positioning import GoldCotFeed, GoldVolFeed

    bars_feed = YFinanceDailyBarsFeed(loader=loader)
    macro_feeds = [
        GoldCrossAssetFeed(bars_feed, correlation_window=correlation_window),
        FredMacroFeed(transport=transport, vintage_sink=vintage_sink),
        GoldCotFeed(transport=transport, cache_path=cot_cache_path),
        GoldVolFeed(bars_feed),
    ]
    if goldhub_csv_path is not None:
        macro_feeds.append(GoldhubCsvFeed(goldhub_csv_path))
    return SnapshotBuilder(
        bars_feed=bars_feed,
        macro_feeds=tuple(macro_feeds),
        news_feed=YahooFinanceNewsFeed("GC=F"),
        session_fn=current_session,
        vintage_reader=vintage_reader,
    )


def build_bitcoin_pipeline(transport=None) -> SnapshotBuilder:
    from tradingagents.pro.ingestion.news import YahooFinanceNewsFeed

    """Default BTC pipeline: Binance spot bars/quote/depth + perp metrics +
    on-chain (CoinMetrics, blockchain.com) + Fear & Greed. All keyless."""
    spot = BinanceSpotFeed(transport=transport)
    return SnapshotBuilder(
        bars_feed=spot,
        quote_feed=spot,
        macro_feeds=(BinanceDerivativesFeed(transport=transport),),
        onchain_feeds=(
            CoinMetricsFeed(transport=transport),
            BlockchainComFeed(transport=transport),
            FearGreedFeed(transport=transport),
        ),
        extra_metric_fns=(lambda: spot.get_orderbook_imbalance("BTCUSDT"),),
        news_feed=YahooFinanceNewsFeed("BTC-USD"),
    )
