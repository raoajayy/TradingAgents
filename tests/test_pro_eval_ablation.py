"""P1-02 token-matched single-model ablation."""

from datetime import timedelta

from tests.pro_fakes import BASE_TS
from tests.test_pro_pipeline_graph import CONFIG, FakePipelineLLM, pipeline_snapshot
from tradingagents.contracts import OHLCVBar, Timeframe
from tradingagents.pro.evals.ablation import run_ablation
from tradingagents.pro.pipeline.schemas import JudgeVerdict


class FakeVerdictModel:
    def __init__(self, verdict: JudgeVerdict):
        self.verdict = verdict
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        assert schema is JudgeVerdict
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return self.verdict


def future_bars(closes):
    return [
        OHLCVBar(timeframe=Timeframe.D1,
                 start=BASE_TS + timedelta(days=200 + i),
                 open=c, high=c + 5.0, low=c - 2.0, close=c, volume=1000.0)
        for i, c in enumerate(closes)
    ]


def test_both_arms_graded_on_same_future():
    single = FakeVerdictModel(JudgeVerdict(
        action="BUY", confidence=80,
        rationale="trend and macro agree per rsi and federal_reserve."))
    # walk price up through TP1 without touching the stop
    arms = run_ablation(FakePipelineLLM(), CONFIG, pipeline_snapshot(),
                        future_bars([131.0, 140.0, 150.0]),
                        single_model=single)
    a, b = arms
    assert a.arm == "pipeline" and b.arm == "single_model"
    assert a.action == "BUY" and b.action == "BUY"
    assert a.recommendation is not None and b.recommendation is not None
    # both tickets resolved on the same future bars
    assert a.outcome is not None and a.outcome.exit_reason == "take_profit"
    assert b.outcome is not None and b.outcome.exit_reason == "take_profit"
    assert a.outcome.pnl > 0 and b.outcome.pnl > 0
    d = b.as_dict()
    assert d["exit_reason"] == "take_profit" and d["rejected"] is None


def test_single_model_sees_evidence_pack_but_no_debate():
    single = FakeVerdictModel(JudgeVerdict(
        action="HOLD", confidence=50, rationale="balanced."))
    arms = run_ablation(FakePipelineLLM(), CONFIG, pipeline_snapshot(),
                        future_bars([131.0]), single_model=single)
    prompt = single.prompts[0]
    assert "[rsi]" in prompt              # identical evidence pack, by id
    assert "Debate record" not in prompt  # arm B never sees the debate
    b = arms[1]
    assert b.action == "HOLD"
    assert b.recommendation is not None
    assert b.outcome is None  # HOLD tickets are ungradable by design
