"""P4-01 corpus tooling: build and count the graded-outcome training corpus.

The RLVR fine-tune (Trading-R1 recipe) is gated on **>= 200 graded
outcomes** — real decisions the system made, each paired with what price
actually did next. This module turns the event store's run records + trade
memory into that corpus, deterministically and with ZERO model calls: it is
data plumbing for the gate, not the fine-tune itself.

One example per run that produced a recommendation:

- ``prompt_context`` — the evidence block + debate transcript exactly as
  the judge saw them (re-rendered from the recorder's accumulated state via
  the pipeline's own rendering helpers, so training inputs match inference
  inputs);
- ``verdict`` — the model's answer: action, confidence, levels, size;
- ``outcome`` — the realized result. A stored journal OUTCOME (a lived
  close, or a prior retro backfill) is always preferred; otherwise the
  ticket is retro-graded in place via ``analytics.retro.simulate_ticket``
  over the bars that followed the decision (same mechanics as
  ``backfill_outcomes``, but read-only: nothing is written to memory).
  ``None`` while unresolved — an ungraded example, never a guess;
- ``reward`` — the signed R-multiple: ``pnl / (|entry - stop| * qty)``,
  i.e. realized pnl in units of the risk the ticket itself declared.
  A stop exit is exactly -1.0 by construction; a first-target exit is
  ``(tp1 - entry) / (entry - stop)`` for a BUY. Ungraded and rejected
  examples carry reward 0.0 with ``graded: false`` so a trainer can mask
  them out;
- ``versions`` — the P3-07 provenance stamp, so corpus rows are traceable
  to the code/prompts/models that produced them.

Rejected runs (a gate or reviewer refused the trade, no recommendation)
are EXCLUDED by default: they carry no falsifiable price claim, so they
cannot be graded. With ``include_rejections=True`` they are emitted with
``outcome=None, reward=0.0`` — abstention learning: a fine-tune that only
ever sees taken trades unlearns the pipeline's ability to refuse one, and
the rejection examples preserve "given this context, the answer was NO
TRADE" as supervised signal (they still never count toward the graded
gate).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from tradingagents.contracts import TradeAction
from tradingagents.pro.analytics.retro import simulate_ticket

logger = logging.getLogger(__name__)

#: P4-01 dependency from docs/ROADMAP_TASKS.md: ">= 200 graded outcomes".
GRADED_THRESHOLD = 200


# --- rendering: the context as the model saw it -----------------------------------

def _prompt_context(run) -> str:
    """Re-render the judge-stage context from the recorder's accumulated
    state, reusing the pipeline's own helpers so the corpus prompt matches
    what the model was actually shown (evidence block + memory context +
    debate transcript). Runs recorded before a field existed degrade to
    the helpers' own empty-state text, never a crash."""
    from tradingagents.pro.pipeline.nodes import (
        _all_evidence,
        _debate_block,
        _evidence_block,
        _with_memory,
    )

    state = getattr(run, "state", None) or {}
    evidence = _all_evidence(state) if state.get("evidence_by_team") else []
    evidence_block = _with_memory(_evidence_block(evidence), state)
    debate_block = _debate_block(state.get("debate") or [])
    return f"Evidence:\n{evidence_block}\n\nDebate:\n{debate_block}"


# --- grading -----------------------------------------------------------------------

def _r_multiple(pnl: float, rec) -> float | None:
    """Signed R-multiple: pnl over the risk the ticket declared
    (|entry - stop| * qty). None when the ticket carries no risk geometry
    (HOLD, or degenerate levels) — the example stays ungraded."""
    if rec.entry_price is None or rec.stop_loss is None:
        return None
    qty = rec.position_size.quantity if rec.position_size else 1.0
    risk = abs(rec.entry_price - rec.stop_loss) * (qty or 1.0)
    if risk <= 0:
        return None
    return pnl / risk


def _stored_outcome(memory, rec):
    """The OUTCOME record already written for this recommendation (a lived
    journal close, or a prior retro backfill), if any."""
    from tradingagents.pro.memory import MemoryKind

    trade = memory.find_trade_by_recommendation(rec.id)
    if trade is None:
        return None
    for record in memory.records(MemoryKind.OUTCOME):
        if record.ref_id == trade.id:
            return record
    return None


def _grade(run, rec, memory, bars_for):
    """Resolve (outcome_dict | None, reward, graded) for a recommendation.
    Stored journal outcomes are preferred over a fresh retro simulation;
    retro grading here is read-only (no memory writes)."""
    stored = _stored_outcome(memory, rec)
    if stored is not None:
        payload = stored.payload
        pnl = payload.get("pnl")
        reward = _r_multiple(pnl, rec) if pnl is not None else None
        outcome = {
            "pnl": pnl,
            "exit_reason": payload.get("exit_reason"),
            "closed_at": payload.get("closed_at"),
            "source": ("retro_backfill" if payload.get("mode") == "retro"
                       else "journal"),
        }
        if reward is None:
            return outcome, 0.0, False
        return outcome, reward, True

    if rec.action is TradeAction.HOLD:
        return None, 0.0, False
    try:
        bars = bars_for(run)
    except Exception:
        logger.warning("bars_for failed for run %s; example stays ungraded",
                       run.run_id, exc_info=True)
        bars = []
    sim = simulate_ticket(rec, bars or [])
    if sim is None:
        return None, 0.0, False  # unresolved: skipped, not guessed
    reward = _r_multiple(sim.pnl, rec)
    outcome = {
        "pnl": sim.pnl,
        "exit_reason": sim.exit_reason,
        "closed_at": (sim.closed_at.isoformat()
                      if hasattr(sim.closed_at, "isoformat")
                      else str(sim.closed_at)),
        "source": "retro",
    }
    if reward is None:
        return outcome, 0.0, False
    return outcome, reward, True


# --- corpus ------------------------------------------------------------------------

def _base_example(run) -> dict:
    return {
        "run_id": run.run_id,
        "symbol": run.symbol,
        "timeframe": getattr(run, "timeframe", None),
        "started_at": run.started_at.isoformat(),
        "prompt_context": _prompt_context(run),
        "versions": getattr(run, "versions", None),
    }


def build_training_corpus(recorder_runs, memory, bars_for,
                          include_rejections: bool = False) -> list[dict]:
    """One training example per stored run that produced a recommendation
    (deduped by run_id; first occurrence wins). ``bars_for(run)`` returns
    the bars strictly AFTER the run's decision bar — the same contract as
    ``analytics.retro.backfill_outcomes`` (``bars_from_runs`` builds one
    from the store itself). ``include_rejections`` adds rejected runs as
    outcome-less, reward-0 abstention examples (see module docstring);
    they never count as graded."""
    examples: list[dict] = []
    seen: set[str] = set()
    for run in recorder_runs:
        if run.run_id in seen:
            continue
        seen.add(run.run_id)
        rec = getattr(run, "recommendation", None)
        if rec is None:
            rejection = getattr(run, "rejection", None)
            if not include_rejections or rejection is None:
                continue
            examples.append({
                **_base_example(run),
                "verdict": {"action": "REJECTED", "confidence": None,
                            "rejection": rejection},
                "outcome": None,
                "reward": 0.0,
                "graded": False,
            })
            continue
        outcome, reward, graded = _grade(run, rec, memory, bars_for)
        examples.append({
            **_base_example(run),
            "verdict": {
                "action": rec.action.value,
                "confidence": rec.confidence,
                "entry_price": rec.entry_price,
                "stop_loss": rec.stop_loss,
                "take_profits": [tp.price for tp in rec.take_profits],
                "quantity": (rec.position_size.quantity
                             if rec.position_size else None),
                "risk_reward": rec.risk_reward,
            },
            "outcome": outcome,
            "reward": reward,
            "graded": graded,
        })
    return examples


def bars_from_runs(recorder_runs):
    """A ``bars_for`` built from the store itself: every stored snapshot's
    bars, pooled per (symbol, timeframe). Later runs' snapshots supply the
    "bars that followed" earlier decisions — retro grading works offline,
    from data the system already recorded, with zero feed calls. A run
    whose future was never captured simply grades unresolved."""
    pool: dict[tuple, dict] = {}
    for run in recorder_runs:
        snapshot = (getattr(run, "state", None) or {}).get("snapshot")
        if snapshot is None:
            continue
        for bar in snapshot.bars:
            pool.setdefault((run.symbol, bar.timeframe.value), {})[bar.start] = bar

    def bars_for(run):
        snapshot = (getattr(run, "state", None) or {}).get("snapshot")
        cutoff = (snapshot.bars[-1].start if snapshot is not None and snapshot.bars
                  else run.started_at)
        timeframe = getattr(run, "timeframe", None)
        bars = pool.get((run.symbol, timeframe))
        if bars is None and timeframe is None:
            candidates = [v for (sym, _tf), v in pool.items() if sym == run.symbol]
            bars = candidates[0] if len(candidates) == 1 else None
        if not bars:
            return []
        return [bars[start] for start in sorted(bars) if start > cutoff]

    return bars_for


# --- output ------------------------------------------------------------------------

def write_corpus_jsonl(examples: list[dict], path: str | Path) -> Path:
    """One JSON object per line; parent directories created."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(example, sort_keys=True) + "\n" for example in examples),
        encoding="utf-8",
    )
    return out


def corpus_summary(examples: list[dict]) -> dict:
    """Operator-facing counts: total, graded vs ungraded, per symbol, and
    per outcome class (win/loss on the R sign; rejected; ungraded)."""
    def outcome_class(example: dict) -> str:
        if example["verdict"]["action"] == "REJECTED":
            return "rejected"
        if not example["graded"]:
            return "ungraded"
        return "win" if (example["outcome"] or {}).get("pnl", 0) > 0 else "loss"

    graded = sum(1 for e in examples if e["graded"])
    return {
        "total": len(examples),
        "graded": graded,
        "ungraded": len(examples) - graded,
        "by_symbol": dict(Counter(e["symbol"] for e in examples)),
        "by_outcome": dict(Counter(outcome_class(e) for e in examples)),
    }


def gate_verdict(graded: int) -> str:
    """The P4-01 gate line, verbatim for the operator/CI log."""
    status = "READY" if graded >= GRADED_THRESHOLD else "NOT READY"
    return f"{graded} graded examples (threshold {GRADED_THRESHOLD}): {status}"
