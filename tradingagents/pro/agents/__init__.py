"""Pro agent layer (Phase 3): one EvidenceAgent runtime, config-driven roster."""

from tradingagents.pro.agents.base import (
    EvidenceAgent,
    EvidenceDraft,
    build_team,
    load_team_template,
    run_agents,
)
from tradingagents.pro.agents.computed_factor import (
    MINED_FACTORS_KEY,
    ComputedFactorAgent,
    attach_mined_factors,
    load_mined_factor_agents,
    store_survivors,
)
from tradingagents.pro.agents.metrics import (
    compute_neutral_risk_metrics,
    compute_quant_metrics,
    compute_risk_metrics,
)
from tradingagents.pro.agents.rendering import (
    RenderedContext,
    active_masker,
    anonymization_scope,
    render_context,
)
from tradingagents.pro.agents.roster import (
    MACRO_SPECS,
    NEWS_SENTIMENT_SPECS,
    QUANT_SPECS,
    RISK_SPECS,
    ROSTER,
    SPECS_BY_TEAM,
    TECHNICAL_SPECS,
    spec_by_id,
    specs_for_asset,
)
from tradingagents.pro.agents.specs import AgentSpec

__all__ = [
    "EvidenceAgent",
    "EvidenceDraft",
    "build_team",
    "load_team_template",
    "run_agents",
    "MINED_FACTORS_KEY",
    "ComputedFactorAgent",
    "attach_mined_factors",
    "load_mined_factor_agents",
    "store_survivors",
    "compute_neutral_risk_metrics",
    "compute_quant_metrics",
    "compute_risk_metrics",
    "RenderedContext",
    "active_masker",
    "anonymization_scope",
    "render_context",
    "MACRO_SPECS",
    "NEWS_SENTIMENT_SPECS",
    "QUANT_SPECS",
    "RISK_SPECS",
    "ROSTER",
    "SPECS_BY_TEAM",
    "TECHNICAL_SPECS",
    "spec_by_id",
    "specs_for_asset",
    "AgentSpec",
]
