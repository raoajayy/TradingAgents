"""P1-01 pass^k stability harness."""

import pytest

from tests.test_pro_pipeline_graph import CONFIG, pipeline_snapshot
from tradingagents.pro.evals.scripted import FakePipelineLLM
from tradingagents.pro.evals.stability import (
    StabilityResult,
    measure_stability,
    run_stability_evals,
)


def test_deterministic_pipeline_has_zero_flip_rate():
    # determinism sanity: same scripted LLM, same snapshot → no flips
    result = measure_stability(FakePipelineLLM(), CONFIG,
                               pipeline_snapshot(), k=3)
    assert result.action_flip_rate == 0.0
    assert result.gate_flip_rate == 0.0
    assert result.confidence_stddev == 0.0
    assert result.k == 3 and len(result.actions) == 3


def test_flip_rate_math():
    r = StabilityResult(symbol="XAUUSD", case="synthetic", k=4,
                        actions=["BUY", "BUY", "BUY", "SELL"],
                        confidences=[70, 70, 80, 60],
                        gates=["approved"] * 4)
    assert r.action_flip_rate == pytest.approx(0.25)
    assert r.gate_flip_rate == 0.0
    assert r.confidence_stddev == pytest.approx(7.0710678, rel=1e-4)
    d = r.as_dict()
    assert d["actions"] == {"BUY": 3, "SELL": 1}
    assert d["mean_confidence"] == 70.0


def test_k_below_two_rejected():
    with pytest.raises(ValueError):
        measure_stability(FakePipelineLLM(), CONFIG, pipeline_snapshot(), k=1)


def test_golden_case_lookup_validates_names():
    with pytest.raises(ValueError, match="unknown golden cases"):
        run_stability_evals(FakePipelineLLM(), CONFIG, k=2,
                            case_names=("no_such_case",))
