"""Deterministic analytics (Phase 3): quant features and the risk engine.

Everything here is pure, typed Python — the numeric ground truth that
quant- and risk-team agents explain but never compute (Constraint 2).
"""

from tradingagents.pro.analytics.crowding import (
    agreement_matrix,
    cohens_kappa,
    crowding_score,
    decision_series,
    rolling_crowding,
    signal_for_action,
)
from tradingagents.pro.analytics.factors import (
    FactorExpr,
    evaluate_factor_oos,
    factor_ic,
    forward_returns,
    ic_decay,
)
from tradingagents.pro.analytics.features import (
    classify_regime,
    close_zscore,
    realized_volatility,
    trend_slope,
)
from tradingagents.pro.analytics.importance import (
    FeatureImportance,
    feature_importance_report,
    negative_mse_scorer,
    permutation_importance,
)
from tradingagents.pro.analytics.regime_ml import MLRegimeModel
from tradingagents.pro.analytics.risk import (
    atr_stop_loss,
    atr_take_profits,
    fixed_risk_position_size,
    historical_cvar,
    historical_var,
    invalidation_stop_loss,
    kelly_fraction,
    take_profits_from_risk,
)

__all__ = [
    "agreement_matrix",
    "cohens_kappa",
    "crowding_score",
    "decision_series",
    "rolling_crowding",
    "signal_for_action",
    "FactorExpr",
    "evaluate_factor_oos",
    "factor_ic",
    "forward_returns",
    "ic_decay",
    "classify_regime",
    "close_zscore",
    "realized_volatility",
    "trend_slope",
    "FeatureImportance",
    "feature_importance_report",
    "negative_mse_scorer",
    "permutation_importance",
    "MLRegimeModel",
    "atr_stop_loss",
    "atr_take_profits",
    "take_profits_from_risk",
    "fixed_risk_position_size",
    "historical_cvar",
    "historical_var",
    "invalidation_stop_loss",
    "kelly_fraction",
]
