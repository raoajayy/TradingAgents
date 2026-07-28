"""P1-02 token-matched single-model ablation.

Does the multi-agent debate earn its tokens? Same frozen snapshot, two
arms:

  A. the full pipeline (evidence → debate → critic → judge → PM)
  B. one strong model given the IDENTICAL evidence pack in one prompt

Both arms share every deterministic stage — evidence agents, risk-engine
levels, sizing, risk/quality gates — so the only variable is who turns
evidence into a verdict. Arm B reuses arm A's evidence and the pipeline's
own ``JudgeVerdict`` schema; its ticket is built by the same
``compute_risk_metrics`` + gates the PM uses. Both tickets are graded by
``analytics.retro.simulate_ticket`` on the bars that followed.

ponytail: arm B piggybacks on arm A's evidence (zero duplicate agent
calls); a fully independent arm B would re-run the evidence teams for
double the cost with the same inputs.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from tradingagents.contracts import (
    PositionSize,
    ProConfig,
    TakeProfitLevel,
    TradeAction,
    TradeRecommendation,
)
from tradingagents.pro.agents import compute_risk_metrics
from tradingagents.pro.agents.metrics import infer_timeframe
from tradingagents.pro.analytics.retro import simulate_ticket
from tradingagents.pro.pipeline import run_pipeline
from tradingagents.pro.pipeline.gates import risk_gate, trade_quality_gate
from tradingagents.pro.pipeline.nodes import _all_evidence, _evidence_block
from tradingagents.pro.pipeline.schemas import JudgeVerdict

SINGLE_MODEL_PROMPT = """\
You are a senior discretionary trader making a single decision for
{symbol}. Below is the complete evidence pack produced by specialist
analysts (technical, macro, news/sentiment, quant, risk). There is no
debate and no second opinion: weigh the evidence yourself and rule.

Evidence:
{evidence}

Rule BUY, SELL, or HOLD with a 0-100 confidence and a short rationale
referencing the agent ids you weighed most.
"""


@dataclass
class ArmResult:
    arm: str                      # "pipeline" | "single_model"
    action: str | None            # None when rejected pre-verdict
    confidence: int | None
    rejected: str | None          # rejection stage, if any
    recommendation: TradeRecommendation | None
    outcome: object | None = None  # RetroOutcome | None (unresolved)

    def as_dict(self) -> dict:
        return {
            "arm": self.arm,
            "action": self.action,
            "confidence": self.confidence,
            "rejected": self.rejected,
            "pnl": self.outcome.pnl if self.outcome else None,
            "exit_reason": self.outcome.exit_reason if self.outcome else None,
        }


def _ticket_from_verdict(verdict, snapshot, config: ProConfig, equity: float,
                         evidence, regime,
                         vote_breakdown) -> tuple[TradeRecommendation | None, str | None]:
    """Deterministic half of the PM, shared verbatim with arm A: engine
    levels for the ruled side, then the same risk + quality gates."""
    action = TradeAction(verdict.action)
    if action is TradeAction.HOLD:
        return TradeRecommendation(
            symbol=snapshot.symbol, asset=snapshot.asset,
            action=TradeAction.HOLD, confidence=verdict.confidence,
            position_size=PositionSize(quantity=0), evidence=evidence,
            market_regime=regime, vote_breakdown=vote_breakdown,
        ), None
    timeframe = infer_timeframe(snapshot)
    sided = compute_risk_metrics(snapshot, config.risk, equity,
                                 side=action.value, timeframe=timeframe)
    gate = risk_gate(sided, config, proposed_action=action)
    if not gate.passed:
        return None, f"risk_gate: {'; '.join(gate.reasons)}"
    quality = trade_quality_gate(sided, config)
    if not quality.passed:
        return None, f"quality_gate: {'; '.join(quality.reasons)}"
    try:
        return TradeRecommendation(
            symbol=snapshot.symbol, asset=snapshot.asset, action=action,
            confidence=verdict.confidence,
            entry_price=sided["ENTRY_REF_PRICE"].value,
            stop_loss=sided["ATR_STOP"].value,
            take_profits=[
                TakeProfitLevel(price=sided[f"ATR_TP{i + 1}"].value,
                                size_fraction=fraction)
                for i, fraction in enumerate(config.risk.tp_fractions)
            ],
            position_size=PositionSize(
                quantity=sided["POSITION_SIZE_UNITS"].value,
                notional=sided["POSITION_NOTIONAL"].value,
                pct_of_equity=sided["POSITION_PCT_EQUITY"].value,
            ),
            evidence=evidence,
            market_regime=regime, vote_breakdown=vote_breakdown,
        ), None
    except (ValidationError, KeyError) as exc:
        return None, f"contract: {exc}"


def run_ablation(llm, config: ProConfig, snapshot, future_bars,
                 single_model=None, equity: float = 100_000.0,
                 **kwargs) -> list[ArmResult]:
    """Run both arms on one frozen snapshot; grade on ``future_bars``
    (the bars AFTER the snapshot). ``single_model`` defaults to the
    bundle's deep model."""
    state = run_pipeline(llm, config, snapshot, **kwargs)
    rec = state.get("recommendation")
    rejection = state.get("rejection")
    arm_a = ArmResult(
        arm="pipeline",
        action=rec.action.value if rec else None,
        confidence=rec.confidence if rec else None,
        rejected=rejection.get("stage") if rejection else None,
        recommendation=rec,
        outcome=simulate_ticket(rec, future_bars) if rec else None,
    )

    evidence = _all_evidence(state)
    model = single_model if single_model is not None else getattr(llm, "deep", llm)
    prompt = SINGLE_MODEL_PROMPT.format(symbol=snapshot.symbol,
                                        evidence=_evidence_block(evidence))
    verdict = model.with_structured_output(JudgeVerdict).invoke(prompt)
    single_rec, rejected = _ticket_from_verdict(
        verdict, snapshot, config, equity, evidence,
        regime=state["regime"], vote_breakdown=state["vote_breakdown"])
    arm_b = ArmResult(
        arm="single_model",
        action=verdict.action,
        confidence=verdict.confidence,
        rejected=rejected,
        recommendation=single_rec,
        outcome=simulate_ticket(single_rec, future_bars) if single_rec else None,
    )
    return [arm_a, arm_b]
