"""RD-Agent-style factor mining loop scaffold (roadmap P3-03).

The loop: an LLM *proposes* formulaic factors as structured output →
each proposal is parsed by the safe evaluator (invalid expressions are
rejected with a reason that is fed back into the next round's prompt) →
valid factors are scored out-of-sample with purged K-fold IC → survivors
must beat the best Alpha158-style baseline. The LLM never touches
numbers; ``analytics.factors`` owns every evaluation.

This module is the SCAFFOLD only — running it against a real model (and
registering survivors into the roster via
``agents.computed_factor.store_survivors``) is an operator decision made
through ``python -m tradingagents.pro.evals --factor-mine``.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from tradingagents.pro.analytics.factors import (
    FactorExpr,
    evaluate_factor_oos,
    function_grammar,
)

logger = logging.getLogger(__name__)

# Alpha158-flavored baselines: the survival bar. A mined factor earns a
# roster seat only by beating the best of these on OOS IC — otherwise the
# LLM is just rediscovering textbook signals at token cost.
BASELINE_EXPRESSIONS: dict[str, str] = {
    "mom_20_z": "zscore(delta(close, 20) / delay(close, 20), 60)",
    "rev_5_rank": "-rank(delta(close, 5) / delay(close, 5), 20)",
    "vol_20_z": "zscore(volume, 20)",
    "range_pos_10": "(close - ts_min(low, 10)) / (ts_max(high, 10) - ts_min(low, 10))",
    "px_vol_corr_10": "corr(close, volume, 10)",
}


class FactorProposal(BaseModel):
    """One proposed factor — the only thing the LLM produces."""

    name: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]{0,39}$",
                      description="Short snake_case identifier for the factor.")
    expression: str = Field(
        min_length=1,
        description="A formula in the documented grammar over the bar columns.")
    rationale: str = Field(
        min_length=1,
        description="One or two sentences: the market hypothesis behind it.")


class FactorProposalBatch(BaseModel):
    proposals: list[FactorProposal] = Field(min_length=1)


PROPOSER_PROMPT = """\
You are a quantitative researcher proposing formulaic alpha factors for a
single-asset {symbol} bar series ({n_bars} bars). Propose exactly {n} NEW
candidate factors as structured output. Each factor must be a single
expression in this grammar — nothing outside it will parse:

{grammar}

The evaluation is a rank information coefficient (IC) against the
{horizon}-bar forward return, measured out-of-sample on purged folds. To
survive, a factor's |IC| must beat the current bar of {bar:.4f} set by
standard baseline factors — do not re-propose plain momentum, short-term
reversal, or volume z-scores.

{feedback}
Aim for economically motivated, low-correlation ideas; prefer bounded
transforms (rank, zscore, sign, corr) over raw prices.
"""


def propose_factors(llm, context: dict[str, Any], n: int = 4) -> list[FactorProposal]:
    """One proposal round: structured output, FakePipelineLLM-compatible
    (``llm.with_structured_output(schema).invoke(prompt)``). Returns [] when
    the model fails to produce a parseable batch — the loop just moves on."""
    feedback = context.get("feedback") or ""
    if feedback:
        feedback = ("Previous rejections (fix or avoid these mistakes):\n"
                    + feedback + "\n")
    prompt = PROPOSER_PROMPT.format(
        symbol=context.get("symbol", "the asset"),
        n_bars=context.get("n_bars", "?"),
        n=n,
        grammar=function_grammar(),
        horizon=context.get("horizon", 5),
        bar=context.get("baseline_bar", 0.0),
        feedback=feedback,
    )
    try:
        batch = llm.with_structured_output(FactorProposalBatch).invoke(prompt)
    except Exception:
        logger.warning("factor proposal call failed", exc_info=True)
        return []
    if batch is None:
        return []
    return list(batch.proposals)[:n]


def mine(
    llm,
    bars_df: pd.DataFrame,
    iterations: int = 3,
    proposals_per_iteration: int = 4,
    horizon: int = 5,
    k: int = 5,
    min_ic: float = 0.01,
    symbol: str = "asset",
    baselines: dict[str, str] | None = None,
) -> dict:
    """Run the propose → validate → evaluate → select loop.

    Survivors need ``|ic_mean| > max(best baseline |ic_mean|, min_ic)``
    with at least 2 usable folds. Returns a JSON-serializable report:
    baselines, per-iteration proposals with their fate and reason, and the
    ``survivors`` list shaped for ``computed_factor.store_survivors``.
    """
    baselines = BASELINE_EXPRESSIONS if baselines is None else baselines
    baseline_results: dict[str, dict] = {}
    baseline_bar = 0.0
    for name, expression in baselines.items():
        res = evaluate_factor_oos(expression, bars_df, horizon=horizon, k=k)
        baseline_results[name] = {"expression": expression, **_stats(res)}
        if res["ic_mean"] is not None:
            baseline_bar = max(baseline_bar, abs(res["ic_mean"]))
    bar = max(baseline_bar, min_ic)

    survivors: list[dict] = []
    iterations_log: list[dict] = []
    seen: set[str] = set()
    feedback_lines: list[str] = []
    counts = {"proposed": 0, "invalid": 0, "weak": 0, "duplicate": 0}

    for it in range(iterations):
        context = {
            "symbol": symbol,
            "n_bars": len(bars_df),
            "horizon": horizon,
            "baseline_bar": bar,
            "feedback": "\n".join(feedback_lines[-8:]),
        }
        proposals = propose_factors(llm, context, n=proposals_per_iteration)
        round_log: list[dict] = []
        for p in proposals:
            counts["proposed"] += 1
            entry: dict = {"name": p.name, "expression": p.expression,
                           "rationale": p.rationale}
            if p.name in seen or p.expression in seen:
                counts["duplicate"] += 1
                entry.update(status="duplicate", reason="already proposed")
                round_log.append(entry)
                continue
            seen.add(p.name)
            seen.add(p.expression)
            try:
                FactorExpr.parse(p.expression)
            except ValueError as exc:
                counts["invalid"] += 1
                reason = str(exc)
                entry.update(status="invalid", reason=reason)
                feedback_lines.append(f"- {p.expression!r}: rejected, {reason}")
                round_log.append(entry)
                continue
            res = evaluate_factor_oos(p.expression, bars_df, horizon=horizon, k=k)
            entry.update(_stats(res))
            ic_mean, n_folds = res["ic_mean"], res["n_folds"]
            if ic_mean is not None and n_folds >= 2 and abs(ic_mean) > bar:
                entry["status"] = "survivor"
                survivors.append({
                    "name": p.name,
                    "expression": p.expression,
                    "rationale": p.rationale,
                    **_stats(res),
                })
            else:
                counts["weak"] += 1
                shown = "n/a" if ic_mean is None else f"{ic_mean:+.4f}"
                reason = (f"OOS ic_mean {shown} over {n_folds} folds did not "
                          f"clear the bar {bar:.4f}")
                entry.update(status="weak", reason=reason)
                feedback_lines.append(f"- {p.expression!r}: {reason}")
            round_log.append(entry)
        iterations_log.append({"iteration": it + 1,
                               "n_proposals": len(proposals),
                               "proposals": round_log})

    return {
        "symbol": symbol,
        "n_bars": len(bars_df),
        "horizon": horizon,
        "k": k,
        "ic_bar": bar,
        "baselines": baseline_results,
        "iterations": iterations_log,
        "counts": {**counts, "survivors": len(survivors)},
        "survivors": survivors,
    }


def _stats(res: dict) -> dict:
    """The JSON-friendly slice of an evaluate_factor_oos result."""
    return {
        "ic_mean": res["ic_mean"],
        "ic_std": res["ic_std"],
        "ic_ir": res["ic_ir"],
        "n_folds": res["n_folds"],
        "decay": {str(h): ic for h, ic in res["decay"].items()},
    }


__all__ = [
    "BASELINE_EXPRESSIONS",
    "FactorProposal",
    "FactorProposalBatch",
    "mine",
    "propose_factors",
]
