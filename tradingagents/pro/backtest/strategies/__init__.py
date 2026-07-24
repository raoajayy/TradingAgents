"""Built-in strategies (roadmap P0.3 / architecture track T1).

One archetype per module (F8 split): the rules pipeline wrapped as ``rules_v1``
(``pipeline``), plus the native order-book reference strategies — trend
following + pyramiding (``trend``), mean reversion (``mean_reversion``),
momentum / HTF-confirmed / regime-gated (``momentum``), MA crossover
(``ma_crossover``) and the volatility-squeeze breakout (``breakout``).

Importing this package imports every submodule, which fires each strategy's
``@register`` decorator so the registry is populated exactly as before. The
public surface (classes, ``*_PARAMS`` spaces, ``PipelineStrategy`` +
``RULES_V1_PARAMS`` + ``apply_rules_v1_params``) is re-exported here, so the
historical ``from tradingagents.pro.backtest.strategies import X`` paths and the
package-root ``from tradingagents.pro.backtest import X`` re-exports are
unchanged.

``pipeline_llm`` (the real-LLM path) needs the operator's model bundle, which
is environment, not a tunable — it is registered by the dashboard job that owns
the bundle (a later increment), not here.
"""

from __future__ import annotations

from tradingagents.pro.backtest.strategies.breakout import (
    VOLATILITY_BREAKOUT_V1_PARAMS,
    VolatilityBreakoutV1,
)
from tradingagents.pro.backtest.strategies.ma_crossover import (
    MA_CROSSOVER_V1_PARAMS,
    MaCrossoverV1,
)
from tradingagents.pro.backtest.strategies.mean_reversion import (
    MEAN_REVERSION_V1_PARAMS,
    MeanReversionV1,
)
from tradingagents.pro.backtest.strategies.momentum import (
    HTF_MOMENTUM_V1_PARAMS,
    MOMENTUM_V1_PARAMS,
    REGIME_MOMENTUM_V1_PARAMS,
    HtfMomentumV1,
    MomentumV1,
    RegimeMomentumV1,
)
from tradingagents.pro.backtest.strategies.pipeline import (
    RULES_V1_PARAMS,
    PipelineStrategy,
    apply_rules_v1_params,
)
from tradingagents.pro.backtest.strategies.trend import (
    TREND_V1_PARAMS,
    TREND_V2_PARAMS,
    TrendFollowingV1,
    TrendFollowingV2,
)

__all__ = [
    "HTF_MOMENTUM_V1_PARAMS",
    "MA_CROSSOVER_V1_PARAMS",
    "MEAN_REVERSION_V1_PARAMS",
    "MOMENTUM_V1_PARAMS",
    "REGIME_MOMENTUM_V1_PARAMS",
    "RULES_V1_PARAMS",
    "TREND_V1_PARAMS",
    "TREND_V2_PARAMS",
    "VOLATILITY_BREAKOUT_V1_PARAMS",
    "HtfMomentumV1",
    "MaCrossoverV1",
    "MeanReversionV1",
    "MomentumV1",
    "PipelineStrategy",
    "RegimeMomentumV1",
    "TrendFollowingV1",
    "TrendFollowingV2",
    "VolatilityBreakoutV1",
    "apply_rules_v1_params",
]
