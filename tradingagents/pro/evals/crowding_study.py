"""P5-04 crowding study: us vs a simulated population of LLM traders.

Research backlog #9 asks a question we cannot answer from our own logs:
**how correlated are our decisions with every other DeepSeek/GPT-prompted
trader?** If a generic LLM handed the same evidence lands on the same
calls, our differentiator is the plumbing (gates, sizing, provenance),
not the judgment — and correlated positioning is a crowding risk in its
own right, independent of whether the calls are good.

The study builds a small population of baseline "traders" from documented
prompt styles, feeds each the **same evidence pack our pipeline saw**, and
compares the resulting decision series with
``tradingagents.pro.analytics.crowding`` (agreement, Cohen's kappa,
crowding score, rolling drift).

Design notes:

- **Same evidence, different reader.** Every baseline sees the identical
  evidence block arm A's specialists produced — the only variable is the
  persona reading it. A baseline that fetched its own data would measure
  data differences, not crowding.
- **One call per baseline per point.** Baselines are single-shot: no
  debate, no critic, no reflection, no memory, no risk engine. That IS the
  population we are modelling — someone pasting a market summary into a
  chat window.
- **Same output contract.** Baselines emit the pipeline's own
  ``JudgeVerdict``, so the comparison is action-for-action.
- **The RUN is an operator decision.** ``python -m tradingagents.pro.evals
  --crowding-study`` costs real model calls; nothing here runs on import
  or in tests.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tradingagents.pro.analytics.crowding import (
    MIN_OVERLAP,
    agreement_matrix,
    crowding_score,
    decision_series,
    rolling_crowding,
    signal_for_action,
)
from tradingagents.pro.pipeline.schemas import JudgeVerdict

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BaselineStyle:
    """One generic-LLM-trader persona: a name, why it is in the population,
    and the prompt template (``{symbol}`` / ``{evidence}``)."""

    name: str
    description: str
    prompt: str

    def render(self, symbol: str, evidence: str) -> str:
        return self.prompt.format(symbol=symbol, evidence=evidence)


_SHARED_TAIL = """\
Evidence pack for {symbol}:
{evidence}

Rule BUY, SELL, or HOLD with a 0-100 confidence and a short rationale.
Decide from the evidence above only; you have no other data, no colleagues
to consult, and no second round.
"""

_NAIVE_MOMENTUM = BaselineStyle(
    name="naive_momentum",
    description=(
        "The trend-follower: reads price action and momentum, buys strength "
        "and sells weakness. The single most common way a retail user "
        "prompts an LLM about a chart."
    ),
    prompt=(
        "You are an experienced trader who trades momentum. Your rule of "
        "thumb is simple: go with the prevailing move — buy what is going "
        "up, sell what is going down, and stand aside only when direction "
        "is genuinely unclear. You weigh trend and momentum readings above "
        "everything else.\n\n" + _SHARED_TAIL
    ),
)

_NEWS_SENTIMENT = BaselineStyle(
    name="news_sentiment",
    description=(
        "The headline reader: trades the tone of news and sentiment, "
        "largely ignoring the tape. Models the LLM trader who asks 'what's "
        "the news saying?' — the persona most exposed to herding on the "
        "same wire stories everyone else pasted in."
    ),
    prompt=(
        "You are a discretionary trader who trades the news. You form your "
        "view from headlines, narrative and sentiment: positive tone and "
        "supportive macro mean buy, negative tone and hostile macro mean "
        "sell. Technical readings interest you only as confirmation.\n\n"
        + _SHARED_TAIL
    ),
)

_INDICATOR_CHECKLIST = BaselineStyle(
    name="indicator_checklist",
    description=(
        "The checklist mechanic: walks the standard indicator set (RSI, "
        "MACD, moving averages, ATR) and tallies bullish vs bearish ticks. "
        "Models the 'act as a technical analyst and check these "
        "indicators' prompt that ships in every trading-bot tutorial."
    ),
    prompt=(
        "You are a technical analyst working a fixed checklist. Go through "
        "each indicator shown, mark it bullish, bearish or neutral by its "
        "textbook interpretation, then rule for the side with more ticks. "
        "State HOLD when the tally is level. Do not improvise beyond the "
        "checklist.\n\n" + _SHARED_TAIL
    ),
)


def baseline_prompts() -> list[BaselineStyle]:
    """The simulated LLM-trader population: three documented prompt styles
    that between them cover how a generic LLM trader is actually prompted
    (chart reader, news reader, indicator checklist).

    Three is deliberate, not arbitrary: a majority vote needs an odd
    population, and each style leans on a different slice of the SAME
    evidence pack, so their consensus is a crowd rather than one persona
    sampled three times.
    """
    return [_NAIVE_MOMENTUM, _NEWS_SENTIMENT, _INDICATOR_CHECKLIST]


def snapshot_evidence_block(snapshot, max_news: int = 5) -> str:
    """A deterministic, model-free rendering of a snapshot: indicators,
    last close, macro/on-chain metrics and headlines.

    Used as the default evidence pack when baselines are run WITHOUT our
    pipeline (``run_baselines`` on bare snapshots — cheap, zero pipeline
    calls). :func:`study` overrides it with the pipeline's own evidence
    block so the population reads exactly what our judge read.
    """
    lines: list[str] = []
    bars = list(getattr(snapshot, "bars", []) or [])
    if bars:
        last = bars[-1]
        lines.append(f"[{last.timeframe.value}] last close: {last.close:.6g} "
                     f"(as of {last.start.isoformat()})")
    for reading in getattr(snapshot, "indicators", []) or []:
        parts = ", ".join(f"{k}={v:.4f}" for k, v in reading.value.items())
        lines.append(f"[{reading.timeframe.value}] {reading.name}: {parts}")
    for metric in [*(getattr(snapshot, "macro", []) or []),
                   *(getattr(snapshot, "onchain", []) or [])]:
        unit = f" {metric.unit}" if getattr(metric, "unit", None) else ""
        lines.append(f"{metric.name}: {metric.value:.6g}{unit}")
    for item in (getattr(snapshot, "news", []) or [])[:max_news]:
        lines.append(f"news: {item.headline}")
    missing = list(getattr(snapshot, "missing_feeds", []) or [])
    if missing:
        lines.append(f"(unavailable feeds, treat as unknown: {', '.join(missing)})")
    return "\n".join(lines) if lines else "(no evidence available)"


def run_baselines(llm, snapshots, styles: list[BaselineStyle] | None = None,
                  model=None, evidence_for=None) -> dict[str, list[dict]]:
    """Run the baseline population over ``snapshots``; one decision series
    per style.

    ``llm`` may be a model bundle (its deep model is used, as in the P1-02
    ablation) or any object exposing ``with_structured_output(schema)
    .invoke(prompt)`` — the scripted ``FakePipelineLLM`` qualifies, which
    is how this is tested without a provider.

    ``evidence_for(snapshot) -> str`` supplies the evidence pack; the
    default renders the snapshot deterministically
    (:func:`snapshot_evidence_block`).

    Returns ``{style_name: [{"ts", "signal", "action", "confidence",
    "rationale", "style"}, ...]}``, ready for the crowding primitives. A
    baseline call that fails is LOGGED AND SKIPPED for that timestamp —
    the point drops out of that baseline's series (and out of any
    alignment that needs it) instead of being scored as a fabricated HOLD.
    """
    styles = styles or baseline_prompts()
    resolve_evidence = evidence_for or snapshot_evidence_block
    model = model if model is not None else getattr(llm, "deep", llm)
    series: dict[str, list[dict]] = {style.name: [] for style in styles}
    for snapshot in snapshots:
        evidence = resolve_evidence(snapshot)
        for style in styles:
            prompt = style.render(symbol=snapshot.symbol, evidence=evidence)
            try:
                # bound per call, like every pipeline node: a scripted or
                # rate-limited model may hand back a fresh runner each time
                verdict = model.with_structured_output(JudgeVerdict).invoke(prompt)
            except Exception:  # noqa: BLE001 — one bad call must not kill the study
                logger.warning("baseline %s failed at %s; point skipped",
                               style.name, snapshot.as_of, exc_info=True)
                continue
            series[style.name].append({
                "ts": snapshot.as_of,
                "signal": signal_for_action(verdict.action),
                "action": verdict.action,
                "confidence": verdict.confidence,
                "rationale": verdict.rationale,
                "style": style.name,
            })
    return series


def study(llm, config, snapshots, styles: list[BaselineStyle] | None = None,
          model=None, window: int = 10, min_overlap: int = MIN_OVERLAP,
          **kwargs) -> dict:
    """The full crowding report: our pipeline vs the baseline population
    over the same frozen snapshots.

    For each snapshot: run the real pipeline once (arm "ours"), then hand
    each baseline the evidence pack that run produced. Rejected runs stay
    in our series as flat decisions — a refusal is a real market footprint
    (no order), and dropping them would flatter our distinctiveness.

    ``kwargs`` pass through to ``run_pipeline`` (e.g. ``agent_workers``).
    Returns a JSON-ready dict; timestamps are ISO strings. With too few
    cut points the crowding fields come back None — a short study is a
    thin study, and the report says so instead of rounding a two-decision
    sample into a percentage.
    """
    from tradingagents.pro.pipeline import run_pipeline
    from tradingagents.pro.pipeline.nodes import _all_evidence, _evidence_block

    styles = styles or baseline_prompts()
    ours_runs: list[object] = []
    evidence_by_ts: dict[str, str] = {}
    errors: list[dict] = []
    used: list[object] = []
    for index, snapshot in enumerate(snapshots):
        try:
            state = run_pipeline(llm, config, snapshot, **kwargs)
        except Exception as exc:  # noqa: BLE001 — record and continue
            errors.append({"as_of": snapshot.as_of.isoformat(),
                           "arm": "ours",
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        evidence = (_all_evidence(state) if state.get("evidence_by_team")
                    else [])
        evidence_by_ts[snapshot.as_of.isoformat()] = _evidence_block(evidence)
        rec = state.get("recommendation")
        rejection = state.get("rejection")
        ours_runs.append(_RunView(
            run_id=f"crowding-{index}", symbol=snapshot.symbol,
            started_at=snapshot.as_of, recommendation=rec,
            rejection=rejection))
        used.append(snapshot)

    ours = decision_series(ours_runs)
    baselines = run_baselines(
        llm, used, styles=styles, model=model,
        evidence_for=lambda snap: evidence_by_ts[snap.as_of.isoformat()])

    sources = {"ours": ours, **baselines}
    report = {
        "symbol": used[0].symbol if used else None,
        "n_points": len(used),
        "window": window,
        "styles": [{"name": s.name, "description": s.description}
                   for s in styles],
        "ours": [_jsonable(e) for e in ours],
        "baselines": {name: [_jsonable(e) for e in entries]
                      for name, entries in baselines.items()},
        "agreement": agreement_matrix(sources, min_overlap=min_overlap),
        "crowding": crowding_score(ours, baselines, min_overlap=min_overlap),
        "rolling": rolling_crowding(ours, baselines, window=window,
                                    min_overlap=min_overlap),
        "errors": errors,
    }
    return report


def historical_snapshots(points: int = 5, symbol: str = "BTC-USD",
                         vendor: str = "BTCUSD", bar_limit: int = 250,
                         asset=None, vintage_reader=None) -> list:
    """``points`` frozen snapshots at evenly spaced historical cut points on
    Delta bars — the same real-data construction the P1-02 ablation series
    uses, minus the grading horizon (crowding measures agreement, not
    P&L, so every cut can be used right up to the present).

    Each snapshot is an explicit-``as_of`` (point-in-time) build, so both
    arms see byte-identical, leak-free inputs.
    """
    from tradingagents.contracts import AssetClass, Timeframe
    from tradingagents.pro.ingestion.builder import SnapshotBuilder
    from tradingagents.pro.ingestion.delta_exchange import DeltaExchangeFeed
    from tradingagents.pro.ingestion.sessions import current_session
    from tradingagents.pro.main import _MappedBars

    asset = asset if asset is not None else AssetClass.BITCOIN
    feed = DeltaExchangeFeed()
    tf = Timeframe.H4
    bars = feed.get_bars(vendor, tf, limit=500)
    builder = SnapshotBuilder(
        bars_feed=_MappedBars(feed, {symbol: vendor}),
        session_fn=current_session,
        vintage_reader=vintage_reader,
    )
    first = bar_limit  # need a full lookback window behind every cut
    if len(bars) <= first:
        raise ValueError(f"not enough bars: have {len(bars)}, need > {first}")
    step = max(1, (len(bars) - first) // max(1, points))
    cuts = list(range(first, len(bars), step))[:points]
    return [
        builder.build(symbol, asset, timeframes=(tf,),
                      bar_limit=bar_limit, as_of=bars[cut].start)
        for cut in cuts
    ]


@dataclass
class _RunView:
    """The minimal run shape ``analytics.crowding.decision_series`` reads —
    lets the study reuse the same extraction the stored-run path uses."""

    run_id: str
    symbol: str
    started_at: object
    recommendation: object
    rejection: dict | None


def _jsonable(entry: dict) -> dict:
    ts = entry.get("ts")
    return {**entry,
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else ts}


__all__ = [
    "BaselineStyle",
    "baseline_prompts",
    "historical_snapshots",
    "run_baselines",
    "snapshot_evidence_block",
    "study",
]
