"""P1-01 pass^k decision-stability harness.

Run the full pipeline k times on one frozen snapshot and measure how
often the decision flips: same inputs should mean the same call. Flip
rate = 1 − share of the modal outcome, so 0.0 is perfectly stable and
a coin-flip pipeline scores 0.5.

Real-model entry: ``python -m tradingagents.pro.evals --stability``.

ponytail: sequential k runs (an LLM pipeline run is minutes-long and
provider-rate-limited; parallelize via agent_workers inside a run, not
across runs).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import mean, pstdev

from tradingagents.contracts import ProConfig
from tradingagents.pro.pipeline import run_pipeline

# default frozen fixtures: one per traded symbol class, unambiguous by
# construction so instability is the pipeline's fault, not the market's
DEFAULT_CASE_NAMES = ("clean_uptrend_supportive_macro", "btc_h1_uptrend")


@dataclass
class StabilityResult:
    symbol: str
    case: str
    k: int
    actions: list[str]       # per-run action, or "rejected"
    confidences: list[int]   # recommendations only
    gates: list[str]         # rejection stage, or "approved"

    @staticmethod
    def _flip_rate(outcomes: list[str]) -> float:
        return 1.0 - Counter(outcomes).most_common(1)[0][1] / len(outcomes)

    @property
    def action_flip_rate(self) -> float:
        return self._flip_rate(self.actions)

    @property
    def gate_flip_rate(self) -> float:
        return self._flip_rate(self.gates)

    @property
    def confidence_stddev(self) -> float:
        return pstdev(self.confidences) if len(self.confidences) >= 2 else 0.0

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "case": self.case,
            "k": self.k,
            "action_flip_rate": round(self.action_flip_rate, 3),
            "confidence_stddev": round(self.confidence_stddev, 2),
            "gate_flip_rate": round(self.gate_flip_rate, 3),
            "actions": dict(Counter(self.actions)),
            "gates": dict(Counter(self.gates)),
            "mean_confidence": (round(mean(self.confidences), 1)
                                if self.confidences else None),
        }


def measure_stability(llm, config: ProConfig, snapshot, k: int = 10,
                      case: str = "adhoc", **kwargs) -> StabilityResult:
    if k < 2:
        raise ValueError("k must be >= 2 to measure stability")
    actions: list[str] = []
    confidences: list[int] = []
    gates: list[str] = []
    for _ in range(k):
        state = run_pipeline(llm, config, snapshot, **kwargs)
        rec = state.get("recommendation")
        rejection = state.get("rejection")
        actions.append(rec.action.value if rec else "rejected")
        if rec is not None:
            confidences.append(rec.confidence)
        gates.append(rejection.get("stage", "unknown") if rejection
                     else "approved")
    return StabilityResult(symbol=snapshot.symbol, case=case, k=k,
                           actions=actions, confidences=confidences,
                           gates=gates)


def run_stability_evals(llm, config: ProConfig, k: int = 10,
                        case_names: tuple[str, ...] = DEFAULT_CASE_NAMES,
                        **kwargs) -> list[StabilityResult]:
    from tradingagents.pro.evals.golden import golden_cases

    by_name = {c.name: c for c in golden_cases()}
    missing = [n for n in case_names if n not in by_name]
    if missing:
        raise ValueError(f"unknown golden cases: {missing}")
    return [
        measure_stability(llm, config, by_name[name].snapshot, k=k,
                          case=name, **kwargs)
        for name in case_names
    ]
