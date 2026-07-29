"""Enumerations shared across all Pro contracts.

String-valued enums so payloads stay human-readable in JSON, logs, and the
memory store, and so LangGraph state serializes without custom encoders.
"""

from __future__ import annotations

from enum import Enum


class AssetClass(str, Enum):
    """Assets the Pro pipeline specializes in.

    The base framework keeps supporting arbitrary equity tickers; these are
    the assets that get dedicated ingestion adapters and agent rosters.
    """

    GOLD = "XAU"
    BITCOIN = "BTC"
    ETHEREUM = "ETH"
    SOLANA = "SOL"
    # daily equities (track T4) + FX majors (P2-10). Unlike the 1:1
    # crypto/gold mapping, one class spans many tickers — the per-run
    # symbol is the instrument. FX pairs are enumerated in FX_SYMBOLS
    # (mirroring how CRYPTO_WIRING keys crypto by symbol); EQUITY still
    # has no DEFAULT_SYMBOLS entry.
    EQUITY = "EQ"
    FX = "FX"


# Default broker-style symbol per asset. The existing dataflows layer
# (symbol_utils.normalize_symbol) already maps these to vendor symbols
# (XAUUSD -> GC=F on Yahoo, BTC-USD stays as is). FX defaults to EURUSD;
# other pairs are chosen per run via ProConfig(symbol=...).
DEFAULT_SYMBOLS: dict[AssetClass, str] = {
    AssetClass.GOLD: "XAUUSD",
    AssetClass.BITCOIN: "BTC-USD",
    AssetClass.ETHEREUM: "ETH-USD",
    AssetClass.SOLANA: "SOL-USD",
    AssetClass.FX: "EURUSD",
}

# Crypto assets share one ingestion/agent wiring (derivatives + on-chain
# + sentiment feeds parameterized by symbol).
CRYPTO_ASSETS: frozenset[AssetClass] = frozenset(
    {AssetClass.BITCOIN, AssetClass.ETHEREUM, AssetClass.SOLANA}
)

# FX pairs share the single AssetClass.FX (one class, symbol distinguishes
# the pair — the crypto pattern, but crypto got one enum member per coin
# before the multi-ticker convention landed). Feed wiring per pair lives
# in tradingagents.pro.main.FX_WIRING.
FX_SYMBOLS: tuple[str, ...] = ("EURUSD", "USDJPY")

# Symbol -> asset for every symbol the loop can trade. DEFAULT_SYMBOLS is
# 1:1 so its inversion misses the non-default FX pairs; this is the single
# honest map (trigger routing, backtest symbol resolution).
ASSET_BY_SYMBOL: dict[str, AssetClass] = {
    **{sym: asset for asset, sym in DEFAULT_SYMBOLS.items()},
    **dict.fromkeys(FX_SYMBOLS, AssetClass.FX),
}


class TradingMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Direction(str, Enum):
    """Directional stance attached to a single evidence claim."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class TradeAction(str, Enum):
    """Final recommendation action (uppercase per the Pro contract spec)."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"


class MarketRegime(str, Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    CRISIS = "crisis"
    UNKNOWN = "unknown"


class TradingSession(str, Enum):
    """Session awareness matters for gold (liquidity/volatility cycles)."""

    ASIA = "asia"
    LONDON = "london"
    NEW_YORK = "new_york"
    CLOSED = "closed"


class AgentTeam(str, Enum):
    """Teams from the Pro agent roster (Phase 3)."""

    EXECUTIVE = "executive"
    TECHNICAL = "technical"
    MACRO = "macro"
    NEWS_SENTIMENT = "news_sentiment"
    QUANT = "quant"
    RISK = "risk"


class SourceType(str, Enum):
    """Provenance category for a piece of evidence."""

    MARKET_DATA = "market_data"
    INDICATOR = "indicator"
    MACRO_RELEASE = "macro_release"
    NEWS = "news"
    SOCIAL = "social"
    ONCHAIN = "onchain"
    PREDICTION_MARKET = "prediction_market"
    MEMORY = "memory"
    MODEL = "model"
