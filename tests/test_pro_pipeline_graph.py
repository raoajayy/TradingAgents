"""End-to-end pipeline runs through the compiled LangGraph with fake LLMs."""

from datetime import timedelta

import pytest

from tests.pro_fakes import BASE_TS
from tests.test_pro_agents_base import make_snapshot
from tradingagents.contracts import (
    AssetClass,
    IndicatorReading,
    OHLCVBar,
    ProConfig,
    Timeframe,
    TradeAction,
    TradingMode,
)
from tradingagents.pro.agents import EvidenceDraft

# the scripted provider lives in the package so the dashboard's replay
# endpoint can use it in production; tests re-import from here
from tradingagents.pro.evals.scripted import (  # noqa: E402,F401
    DEFAULT_DRAFTS,
    FakePipelineLLM,
    FakeRunnable,
)
from tradingagents.pro.pipeline import (
    CriticReport,
    JudgeVerdict,
    ReflectionNote,
    run_pipeline,
)


def pipeline_snapshot(**overrides):
    base = make_snapshot()
    snapshot = make_snapshot(
        indicators=[
            *base.indicators,
            IndicatorReading(name="ATR_14", timeframe=Timeframe.D1,
                             value={"value": 2.5}, params={"period": 14}),
        ],
        **overrides,
    )
    return snapshot


def lossy_bars(n: int = 30) -> list[OHLCVBar]:
    """Wildly volatile bars: ±35% alternating swings → VaR95 ≈ 0.35, so the
    position-scaled gate (0.35 × 10% max position = 0.035) breaches the 3%
    daily-loss budget — reckless volatility the gate must still veto. The
    oscillation keeps price bounded (no zero-price contract violation)."""
    bars, price = [], 100.0
    for i in range(n):
        close = price * (0.65 if i % 2 == 0 else 1.35)
        hi = max(price, close) * 1.01
        lo = min(price, close) * 0.99
        bars.append(OHLCVBar(
            timeframe=Timeframe.D1, start=BASE_TS + timedelta(days=i),
            open=price, high=hi, low=lo, close=close, volume=1000.0,
        ))
        price = close
    return bars


CONFIG = ProConfig(asset=AssetClass.GOLD, max_debate_rounds=1)


def test_happy_path_produces_validated_buy_recommendation():
    llm = FakePipelineLLM()
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())

    assert state.get("rejection") is None
    rec = state["recommendation"]
    assert rec is not None
    assert rec.action is TradeAction.BUY
    assert rec.confidence == 72
    # levels came from the risk engine, geometry validated by the contract
    assert rec.stop_loss < rec.entry_price < rec.take_profits[0].price
    assert rec.risk_reward is not None and rec.risk_reward > 0
    assert rec.position_size.quantity > 0
    # votes recorded: every evidence item + the judge
    judge_votes = [v for v in rec.vote_breakdown.votes if v.agent_id == "judge"]
    assert len(judge_votes) == 1
    assert len(rec.vote_breakdown.votes) >= 2
    assert state["execution_status"] == "accepted:paper"
    # judge saw the computed vote tally
    assert "confidence weight" in llm.prompts["JudgeVerdict"][0]


def test_event_gate_blocks_new_entries_near_major_events():
    # review deal-breaker #2: the pipeline shorted gold on FOMC day with
    # zero FOMC awareness. Inside the window the run must decline to trade.
    from datetime import datetime, timedelta, timezone

    imminent = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    llm = FakePipelineLLM()
    state = run_pipeline(
        llm, CONFIG, pipeline_snapshot(),
        calendar_fn=lambda: {"release": "FOMC Press Release",
                             "date": "2026-07-16", "major": True,
                             "at": imminent},
    )
    assert state.get("recommendation") is None
    assert state["rejection"]["stage"] == "event_gate"
    assert "FOMC Press Release" in state["rejection"]["reasons"][0]
    assert state["gate_results"]["event"]["passed"] is False
    # R2.6: the veto happens at prepare — a gated run buys ZERO LLM calls
    # (six event-day runs each paid for a full debate before this fix)
    assert llm.prompts == {}


def test_event_gate_passes_when_no_event_is_near():
    from datetime import datetime, timedelta, timezone

    far = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    llm = FakePipelineLLM()
    state = run_pipeline(
        llm, CONFIG, pipeline_snapshot(),
        calendar_fn=lambda: {"release": "FOMC Press Release",
                             "date": "2026-07-19", "major": True, "at": far},
    )
    assert state.get("rejection") is None
    assert state["recommendation"] is not None
    assert state["gate_results"]["event"]["passed"] is True


def test_broken_calendar_never_blocks_the_run():
    llm = FakePipelineLLM()
    state = run_pipeline(
        llm, CONFIG, pipeline_snapshot(),
        calendar_fn=lambda: (_ for _ in ()).throw(RuntimeError("calendar down")),
    )
    assert state.get("rejection") is None
    assert state["recommendation"] is not None


def test_reflection_invalidation_price_derives_the_stop():
    llm = FakePipelineLLM(overrides={
        ReflectionNote: ReflectionNote(
            weaknesses="Momentum evidence is single-timeframe.",
            invalidation="A sustained close below 128.0 breaks the structure.",
            invalidation_price=128.0,
        ),
    })
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    rec = state["recommendation"]
    assert rec is not None and rec.action is TradeAction.BUY
    # entry 130.0 (last close), ATR 2.5: stop derives from the thesis-death
    # level 128.0 minus buffer min(0.25*2.5, max(0.25*2.0, 0.13)) = 0.5
    assert rec.invalidation_price == 128.0
    assert rec.stop_loss == pytest.approx(127.5)
    # the trade no longer outlives its thesis (old ATR stop was 125.0)
    assert rec.stop_loss > 125.0


def test_wrong_sided_invalidation_suppresses_and_stop_becomes_thesis_death():
    """R4.1: a note written for the other side must never ship as-is. With
    no regeneration available (the fake returns the same wrong-sided note),
    the prose is suppressed and the stop IS the thesis-death level."""
    llm = FakePipelineLLM(overrides={
        ReflectionNote: ReflectionNote(
            weaknesses="Momentum evidence is single-timeframe.",
            invalidation="A close above 131.0 invalidates the bear case.",
            invalidation_price=131.0,  # above entry: unusable for a BUY
        ),
    })
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    rec = state["recommendation"]
    assert rec is not None and rec.action is TradeAction.BUY
    assert rec.stop_loss == pytest.approx(125.0)  # 130 - 2*ATR(2.5)
    # directional tickets never ship null invalidation anymore
    assert rec.invalidation_price == pytest.approx(rec.stop_loss)
    reflection = state["reflection"]
    assert reflection["restated"] == "suppressed after judge direction change"
    assert "Restated for the final BUY direction" in reflection["invalidation"]
    assert "bear case" not in reflection["invalidation"]


def test_r41_sell_verdict_with_long_thesis_reflection_never_contradicts():
    """The production R4.1 shape (run 39a7db07): bearish evidence, judge
    rules SELL, but reflection wrote a LONG-thesis invalidation with a
    below-entry level. The ticket must not carry the contradictory prose,
    and invalidation_price must be non-null."""
    llm = FakePipelineLLM(overrides={
        EvidenceDraft: EvidenceDraft(
            claim="Signal points down.", direction="bearish", confidence=60,
        ),
        JudgeVerdict: JudgeVerdict(action="SELL", confidence=58,
                                   rationale="Bear side carried it."),
        ReflectionNote: ReflectionNote(
            weaknesses="Volume climax may be absorption.",
            invalidation=("For a long thesis (bullish reversal from 128): a "
                          "close below 128 would invalidate the reversal."),
            invalidation_price=128.0,  # below entry: a LONG level on a SELL
        ),
    })
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    rec = state["recommendation"]
    assert rec is not None and rec.action is TradeAction.SELL
    assert rec.invalidation_price is not None
    assert rec.invalidation_price > rec.entry_price  # right side for a SELL
    reflection = state["reflection"]
    assert "long thesis" not in reflection["invalidation"].lower()
    assert "Restated for the final SELL direction" in reflection["invalidation"]


def test_r41_regeneration_repairs_the_note_when_the_model_cooperates():
    """When the targeted second call returns a right-sided level, the
    regenerated prose + price ship (no suppression)."""
    wrong = ReflectionNote(
        weaknesses="w", invalidation="close below 128 kills the long.",
        invalidation_price=128.0)
    repaired = ReflectionNote(
        weaknesses="w",
        invalidation="A close above 134 kills the short thesis.",
        invalidation_price=134.0)

    class QueueRunnable:
        """Pipeline caches the structured runnable per schema, so the
        per-call sequencing must live in invoke(), not construction."""

        def __init__(self, payloads, log):
            self.payloads = list(payloads)
            self.log = log

        def invoke(self, prompt):
            self.log.append(prompt)
            return (self.payloads.pop(0) if len(self.payloads) > 1
                    else self.payloads[0])

    class SequencedLLM(FakePipelineLLM):
        def __init__(self):
            super().__init__(overrides={
                EvidenceDraft: EvidenceDraft(
                    claim="Down.", direction="bearish", confidence=60),
                JudgeVerdict: JudgeVerdict(action="SELL", confidence=60,
                                           rationale="bear"),
            })

        def with_structured_output(self, schema):
            if schema is ReflectionNote:
                return QueueRunnable(
                    [wrong, repaired],
                    self.prompts.setdefault("ReflectionNote", []))
            return super().with_structured_output(schema)

    llm = SequencedLLM()
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    rec = state["recommendation"]
    assert rec is not None and rec.action is TradeAction.SELL
    assert rec.invalidation_price == pytest.approx(134.0)
    reflection = state["reflection"]
    assert reflection["restated"] == "regenerated after judge direction change"
    assert "above 134" in reflection["invalidation"]
    # the targeted second prompt pinned the ruled side
    regen_prompt = llm.prompts["ReflectionNote"][-1]
    assert "SHORT THESIS ONLY" in regen_prompt
    assert "ABOVE" in regen_prompt


def test_hold_ruling_yields_hold_recommendation_without_levels():
    llm = FakePipelineLLM(overrides={
        JudgeVerdict: JudgeVerdict(action="HOLD", confidence=50,
                                   rationale="Evidence is balanced."),
    })
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    rec = state["recommendation"]
    assert rec.action is TradeAction.HOLD
    assert rec.entry_price is None and rec.stop_loss is None
    assert rec.position_size.quantity == 0
    assert state["execution_status"] == "accepted:paper"


def test_debate_rounds_are_bounded_by_config():
    llm = FakePipelineLLM()
    config = ProConfig(asset=AssetClass.GOLD, max_debate_rounds=2)
    state = run_pipeline(llm, config, pipeline_snapshot())

    speakers = [entry["speaker"] for entry in state["debate"]]
    assert speakers.count("technical_bull") == 2
    assert speakers.count("technical_bear") == 2
    assert speakers.count("macro_bull") == 2
    assert speakers.count("macro_bear") == 2
    assert speakers.count("sentiment") == 1
    # order: full technical exchange precedes macro
    assert speakers.index("macro_bull") > speakers.index("technical_bear")


def test_risk_gate_rejects_var_breach_before_critic():
    llm = FakePipelineLLM()
    state = run_pipeline(llm, CONFIG, pipeline_snapshot(bars=lossy_bars()))

    assert state["rejection"]["stage"] == "risk_gate"
    assert state["recommendation"] is None
    assert state["execution_status"] == "rejected:risk_gate"
    speakers = {entry["speaker"] for entry in state["debate"]}
    assert "critic" not in speakers and "judge" not in speakers


def test_critic_fail_rejects_run():
    llm = FakePipelineLLM(overrides={
        CriticReport: CriticReport(
            verdict="fail",
            issues=["technical_bull cited agent 'ichimoku' which produced no evidence"],
        ),
    })
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    assert state["rejection"]["stage"] == "critic"
    assert state["recommendation"] is None
    assert "ichimoku" in state["rejection"]["reasons"][0]


def test_unsupported_judge_ruling_rejected_at_pm():
    # every agent said bearish, judge rules BUY -> no supporting evidence
    llm = FakePipelineLLM(overrides={
        EvidenceDraft: EvidenceDraft(
            claim="Signal points down.", direction="bearish", confidence=60,
        ),
    })
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    assert state["rejection"]["stage"] == "portfolio_manager"
    assert "no evidence supports" in state["rejection"]["reasons"][0]
    assert state["recommendation"] is None


def test_join_rejects_when_no_agent_produces_evidence():
    llm = FakePipelineLLM(overrides={EvidenceDraft: RuntimeError("all agents down")})
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    assert state["rejection"]["stage"] == "join"
    assert state["recommendation"] is None
    assert state["debate"] == []  # rejected before any debate turn


def test_live_mode_without_checkpointer_cannot_even_build():
    import pytest

    llm = FakePipelineLLM()
    config = ProConfig(
        asset=AssetClass.GOLD, mode=TradingMode.LIVE,
        live_trading_enabled=True, max_debate_rounds=1,
    )
    with pytest.raises(ValueError, match="requires a checkpointer"):
        run_pipeline(llm, config, pipeline_snapshot())


def test_sell_ruling_builds_sell_geometry():
    llm = FakePipelineLLM(overrides={
        EvidenceDraft: EvidenceDraft(
            claim="Signal points down.", direction="bearish", confidence=60,
        ),
        JudgeVerdict: JudgeVerdict(action="SELL", confidence=64,
                                   rationale="Bear side carried it."),
    })
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    rec = state["recommendation"]
    assert rec.action is TradeAction.SELL
    assert rec.stop_loss > rec.entry_price > rec.take_profits[0].price
    assert state.get("rejection") is None


def test_counterarguments_preserve_losing_side():
    # mix: technical bullish (default), but override is global per schema, so
    # simulate by checking the happy path keeps opposing list consistent
    llm = FakePipelineLLM()
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())
    rec = state["recommendation"]
    # all fake evidence is bullish -> no counterarguments, all supporting
    assert rec.counterarguments == []
    assert all(e.direction.value == "bullish" for e in rec.evidence)


class _SequencedCriticLLM(FakePipelineLLM):
    """FakePipelineLLM whose CriticReport calls pop from a script, so the
    majority-of-N boundary (P1-01 fix) is testable sample by sample."""

    def __init__(self, critic_script: list[CriticReport], **kwargs):
        super().__init__(**kwargs)
        self._critic_script = list(critic_script)

    def with_structured_output(self, schema):
        if schema is CriticReport and self._critic_script:
            outer = self

            class _Popping:
                def invoke(self, prompt):
                    outer.prompts.setdefault("CriticReport", []).append(prompt)
                    return outer._critic_script.pop(0)

            return _Popping()
        return super().with_structured_output(schema)


def test_critic_majority_pass_overrides_one_fail():
    llm = _SequencedCriticLLM([
        CriticReport(verdict="pass", issues=[]),
        CriticReport(verdict="fail", issues=["flaky nit"]),
        CriticReport(verdict="pass", issues=[]),
    ])
    config = CONFIG.model_copy(update={"critic_samples": 3})
    state = run_pipeline(llm, config, pipeline_snapshot())
    critic = state["gate_results"]["critic"]
    assert critic["passed"] is True
    assert critic["votes_pass"] == 2 and critic["samples"] == 3
    assert (state.get("rejection") or {}).get("stage") != "critic"


def test_critic_majority_fail_merges_distinct_issues():
    llm = _SequencedCriticLLM([
        CriticReport(verdict="fail", issues=["cited ghost evidence"]),
        CriticReport(verdict="pass", issues=[]),
        CriticReport(verdict="fail", issues=["cited ghost evidence",
                                             "direction contradicts rsi"]),
    ])
    config = CONFIG.model_copy(update={"critic_samples": 3})
    state = run_pipeline(llm, config, pipeline_snapshot())
    assert state["rejection"]["stage"] == "critic"
    # deduped, order-preserving union of failing samples' issues
    assert state["rejection"]["reasons"] == [
        "cited ghost evidence", "direction contradicts rsi"]


def test_critic_tie_fails_closed():
    llm = _SequencedCriticLLM([
        CriticReport(verdict="pass", issues=[]),
        CriticReport(verdict="fail", issues=["borderline"]),
    ])
    config = CONFIG.model_copy(update={"critic_samples": 2})
    state = run_pipeline(llm, config, pipeline_snapshot())
    assert state["rejection"]["stage"] == "critic"
    assert state["gate_results"]["critic"]["votes_pass"] == 1


def test_join_names_the_provider_when_the_model_layer_is_what_failed():
    """The production symptom: every signal on /decisions rejected at join
    with "no agent produced evidence; nothing to debate" — which reads as a
    market/coverage condition — while the real cause was the provider
    refusing all ~55 calls with HTTP 402 Insufficient Balance. An operator
    reading that message investigates the strategy, not the billing.
    """
    from tradingagents.pro.agents import EvidenceDraft

    refusal = RuntimeError("Insufficient Balance")
    refusal.status_code = 402  # duck-typed like openai.APIStatusError
    llm = FakePipelineLLM(overrides={EvidenceDraft: refusal})
    state = run_pipeline(llm, CONFIG, pipeline_snapshot())

    assert state["rejection"]["stage"] == "join"
    (reason,) = state["rejection"]["reasons"]
    assert "failed the structured LLM call" in reason
    assert "REFUSED" in reason and "not a market condition" in reason
    # the honest count is carried, not a bare boolean
    assert "/" in reason


def test_join_keeps_the_plain_reason_when_agents_simply_had_no_data():
    """The inverse: a genuine no-data abstention must NOT be dressed up as
    an infrastructure failure, or the new message cries wolf."""
    from tradingagents.pro.pipeline.nodes import NO_EVIDENCE_REASON, _no_evidence_reason

    assert _no_evidence_reason({}) == NO_EVIDENCE_REASON
    assert _no_evidence_reason(
        {"abstentions_by_team": {"technical": {"no_data": 12}}}
    ) == NO_EVIDENCE_REASON


def test_non_retryable_provider_refusal_does_not_burn_the_retry_budget():
    """402/401 can never succeed on retry. Retrying spent the full budget
    plus its backoff sleeps on every one of ~55 agent calls per run."""
    from tradingagents.pro.pipeline import DebateTurn
    from tradingagents.pro.pipeline.nodes import PipelineNodes

    calls = {"n": 0}

    class _Refusing:
        def with_structured_output(self, schema):
            return self

        def invoke(self, prompt):
            calls["n"] += 1
            exc = RuntimeError("Insufficient Balance")
            exc.status_code = 402
            raise exc

    slept: list[float] = []
    nodes = PipelineNodes(_Refusing(), CONFIG, llm_retries=3)
    nodes._sleep = slept.append
    assert nodes._invoke(DebateTurn, "why?") is None
    assert calls["n"] == 1, "a 402 must not be retried"
    assert slept == [], "and must not sleep between attempts"

    # a retryable error still uses the full budget
    calls["n"] = 0

    class _Flaky(_Refusing):
        def invoke(self, prompt):
            calls["n"] += 1
            raise RuntimeError("connection reset")  # no status_code

    nodes2 = PipelineNodes(_Flaky(), CONFIG, llm_retries=3)
    nodes2._sleep = slept.append
    assert nodes2._invoke(DebateTurn, "why?") is None
    assert calls["n"] == 4  # 1 + 3 retries


def test_debate_abstentions_are_counted_and_never_read_as_confidence_zero():
    """A debate turn whose structured call fails must be recorded as an
    abstention, not as a real confidence-0 vote.

    Observed in live records: technical_bull/bear and macro_bull/bear
    logged 'conf 0 (abstained: structured output failed)'. That string was
    fed straight back into the judge's prompt as
    'confidence 0: (abstained...)', which reads as a genuine
    zero-conviction argument, and the UI could not tell the two apart.
    """
    from tradingagents.pro.observability import MetricsRegistry
    from tradingagents.pro.pipeline import DebateTurn
    from tradingagents.pro.pipeline.nodes import _debate_block

    metrics = MetricsRegistry()
    # every DebateTurn call raises -> retries exhausted -> abstain
    llm = FakePipelineLLM(overrides={DebateTurn: RuntimeError("no parse")})
    state = run_pipeline(llm, CONFIG, pipeline_snapshot(),
                         llm_retries=0, metrics=metrics)

    # the debater turns only — critic/reflection/judge use other schemas
    # and still succeed, which is exactly the situation being tested
    turns = [e for e in state["debate"]
             if e["speaker"].endswith(("_bull", "_bear")) or e["speaker"] == "sentiment"]
    assert turns, "the debate still runs; turns are recorded as abstentions"
    assert all(e["abstained"] for e in turns)
    # confidence is absent, not fabricated as 0
    assert all(e["confidence"] is None for e in turns)
    # counted where an operator can see it
    assert metrics.counter("structured_output_failures_total",
                           schema="DebateTurn") > 0
    assert metrics.counter("debate_abstentions_total",
                           speaker="technical_bull") == 1

    # ...and the judge is told to disregard, not handed a phantom vote
    block = _debate_block(turns)
    assert "confidence 0" not in block
    assert "disregard" in block


def test_debate_block_still_renders_real_turns_with_confidence():
    from tradingagents.pro.pipeline.nodes import _debate_block

    block = _debate_block([
        {"speaker": "technical_bull", "stance": "bull", "confidence": 71,
         "argument": "trend intact", "cited": ["rsi"], "abstained": False},
    ])
    assert block == (
        "technical_bull (bull, confidence 71): trend intact [cites: rsi]"
    )
