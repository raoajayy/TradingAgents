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
from tradingagents.pro.pipeline import build_vote_breakdown, run_pipeline
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
        try:
            return TradeRecommendation(
                symbol=snapshot.symbol, asset=snapshot.asset,
                action=TradeAction.HOLD, confidence=verdict.confidence,
                position_size=PositionSize(quantity=0), evidence=evidence,
                market_regime=regime, vote_breakdown=vote_breakdown,
            ), None
        except ValidationError as exc:
            return None, f"contract: {exc}"
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
    # a pipeline arm that rejected pre-vote leaves no vote_breakdown in
    # state; arm B still needs one on its ticket — build it from the same
    # evidence (the helper the pipeline itself uses). Missing regime or
    # empty evidence surfaces as a contract rejection, not a crash.
    vote_breakdown = state.get("vote_breakdown") or (
        build_vote_breakdown(evidence) if evidence else None)
    single_rec, rejected = _ticket_from_verdict(
        verdict, snapshot, config, equity, evidence,
        regime=state.get("regime"), vote_breakdown=vote_breakdown)
    arm_b = ArmResult(
        arm="single_model",
        action=verdict.action,
        confidence=verdict.confidence,
        rejected=rejected,
        recommendation=single_rec,
        outcome=simulate_ticket(single_rec, future_bars) if single_rec else None,
    )
    return [arm_a, arm_b]


def run_ablation_series(llm, config: ProConfig, symbol: str = "BTC-USD",
                        vendor: str = "BTCUSD", points: int = 5,
                        horizon: int = 42, vintage_reader=None,
                        **kwargs) -> list[dict]:
    """Real-data ablation: N historical cut points on Delta bars. Each
    snapshot is bars + deterministic indicators as of the cut — no
    macro/news feeds, so there is zero look-ahead leakage and both arms
    see byte-identical inputs. Tickets are graded on the ``horizon`` bars
    that actually followed.

    ``vintage_reader`` (P3-02): anything exposing ``latest_as_known(name,
    at)`` / ``has_vintages(name)`` — the EventStore qualifies. Every cut
    is an explicit-``as_of`` (point-in-time) build, so the builder replays
    any vintaged metric "as known at the cut", never the revised value.
    With the default bars-only feed set there is nothing to replay yet;
    wiring the reader here keeps the PIT contract honest the moment a
    macro feed joins this series."""
    from tradingagents.contracts import Timeframe
    from tradingagents.pro.ingestion.builder import SnapshotBuilder
    from tradingagents.pro.ingestion.delta_exchange import DeltaExchangeFeed
    from tradingagents.pro.ingestion.sessions import current_session
    from tradingagents.pro.main import _MappedBars

    feed = DeltaExchangeFeed()
    tf = Timeframe.H4
    bars = feed.get_bars(vendor, tf, limit=500)
    builder = SnapshotBuilder(
        bars_feed=_MappedBars(feed, {symbol: vendor}),
        session_fn=current_session,
        vintage_reader=vintage_reader,
    )
    first = 250  # need a full lookback window behind every cut
    last = len(bars) - horizon
    if last <= first:
        raise ValueError(f"not enough bars: have {len(bars)}, "
                         f"need > {first + horizon}")
    step = max(1, (last - first) // points)
    cuts = list(range(first, last, step))[:points]

    rows: list[dict] = []
    for cut in cuts:
        as_of = bars[cut].start
        # a multi-hour series must not die on one transient miss (observed:
        # a single Delta read-timeout killed a 40-minute run and lost every
        # finished point) — retry the flaky part, then skip the point with
        # an error row so the rest of the series still lands
        try:
            snapshot = _retrying(
                lambda cut_ts=as_of: builder.build(
                    symbol, config.asset, timeframes=(tf,),
                    bar_limit=250, as_of=cut_ts))
            # strictly after: the cut bar is IN the snapshot; grading on it
            # would let the ticket resolve on data the decision already saw
            future = [b for b in bars if b.start > as_of][:horizon]
            arms = run_ablation(llm, config, snapshot, future, **kwargs)
        except Exception as exc:  # noqa: BLE001 — record and continue
            rows.append({"as_of": as_of.isoformat(),
                         "error": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append({"as_of": as_of.isoformat(),
                     "arms": [a.as_dict() for a in arms]})
    return rows


def _retrying(fn, attempts: int = 3, base_delay: float = 5.0):
    """Retry transient I/O (feed fetch) with growing backoff."""
    import time

    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — caller decides fatality
            last = exc
            if i < attempts - 1:
                time.sleep(base_delay * (3 ** i))
    raise last  # type: ignore[misc]
