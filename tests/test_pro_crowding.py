"""P5-04 crowding primitives + the LLM-trader baseline population.

Kappa is checked against hand-computed values (including the classic
mostly-flat case where raw agreement is high and kappa is ~0), the
crowding score against hand-counted matches, and every degenerate path
against None. The baseline runner is exercised on the scripted
FakePipelineLLM — zero model calls, zero network.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import TradeAction
from tradingagents.pro.analytics.crowding import (
    agreement_matrix,
    cohens_kappa,
    crowding_score,
    decision_series,
    rolling_crowding,
    signal_for_action,
)
from tradingagents.pro.evals.crowding_study import (
    baseline_prompts,
    run_baselines,
    snapshot_evidence_block,
)
from tradingagents.pro.evals.scripted import FakePipelineLLM
from tradingagents.pro.pipeline.schemas import JudgeVerdict


def ts(i: int):
    return BASE_TS + timedelta(hours=i)


def series(signals, start: int = 0):
    """A decision series from a list of signals, one per hour."""
    return [{"ts": ts(start + i), "signal": s} for i, s in enumerate(signals)]


def make_run(action, run_id="run-1", symbol="BTC-USD", offset=0,
             rejection=None, confidence=70):
    """A recorder-shaped run; ``action=None`` means no recommendation."""
    rec = (None if action is None else
           SimpleNamespace(action=TradeAction(action), confidence=confidence))
    return SimpleNamespace(run_id=run_id, symbol=symbol, started_at=ts(offset),
                           recommendation=rec, rejection=rejection)


class TestDecisionSeries:
    def test_actions_map_to_signed_signals(self):
        runs = [make_run("BUY", "r1", offset=0),
                make_run("SELL", "r2", offset=1),
                make_run("HOLD", "r3", offset=2)]
        assert [e["signal"] for e in decision_series(runs)] == [1, -1, 0]
        assert [e["action"] for e in decision_series(runs)] == [
            "BUY", "SELL", "HOLD"]
        assert signal_for_action(TradeAction.BUY) == 1
        assert signal_for_action("SELL") == -1
        assert signal_for_action(None) == 0

    def test_rejected_run_is_flat_and_kept(self):
        # a refused trade is a real footprint (no order) — it must stay in
        # the series as 0, not vanish and inflate our directional rate
        runs = [make_run("BUY", "r1", offset=0),
                make_run(None, "r2", offset=1,
                         rejection={"stage": "risk_gate"})]
        entries = decision_series(runs)
        assert len(entries) == 2
        assert entries[1]["signal"] == 0
        assert entries[1]["action"] == "REJECTED"
        assert entries[1]["rejected"] == "risk_gate"
        assert entries[1]["confidence"] is None

    def test_filters_by_symbol_and_sorts_by_time(self):
        runs = [make_run("SELL", "r1", symbol="BTC-USD", offset=5),
                make_run("BUY", "r2", symbol="XAUUSD", offset=1),
                make_run("BUY", "r3", symbol="BTC-USD", offset=0)]
        entries = decision_series(runs, symbol="BTC-USD")
        assert [e["run_id"] for e in entries] == ["r3", "r1"]
        assert [e["ts"] for e in entries] == [ts(0), ts(5)]


class TestCohensKappa:
    def test_perfect_agreement_is_one(self):
        assert cohens_kappa([1, 0, -1, 1], [1, 0, -1, 1]) == pytest.approx(1.0)

    def test_hand_computed_kappa(self):
        # a = [1, 1, 0, 0, -1, -1], b = [1, 0, 0, 0, -1, -1]
        # p_o = 5/6; marginals a = (2/6, 2/6, 2/6), b = (1/6, 3/6, 2/6)
        # p_e = 2/6*1/6 + 2/6*3/6 + 2/6*2/6 = 12/36 = 1/3
        # kappa = (5/6 - 1/3) / (1 - 1/3) = 0.5 / (2/3) = 0.75
        a = [1, 1, 0, 0, -1, -1]
        b = [1, 0, 0, 0, -1, -1]
        assert cohens_kappa(a, b) == pytest.approx(0.75)

    def test_mostly_flat_series_high_agreement_but_kappa_near_zero(self):
        # THE case this metric exists for: two sources that sit on their
        # hands 9 bars in 10 agree 80% of the time while sharing no
        # directional view at all.
        a = [1] + [0] * 9
        b = [0, 1] + [0] * 8
        raw = sum(x == y for x, y in zip(a, b, strict=True)) / len(a)
        assert raw == pytest.approx(0.8)
        # p_e = 0.9*0.9 + 0.1*0.1 = 0.82 → (0.8 - 0.82) / 0.18 = -0.111...
        assert cohens_kappa(a, b) == pytest.approx(-0.1111111, abs=1e-6)
        assert abs(cohens_kappa(a, b)) < 0.2  # ~0: no shared signal

    def test_systematic_disagreement_is_negative_one(self):
        assert cohens_kappa([1, 1, 0, 0], [0, 0, 1, 1]) == pytest.approx(-1.0)

    def test_both_constant_is_undefined_not_a_fabricated_one(self):
        # p_e == 1: raw agreement is a meaningless 100%, kappa has no value
        assert cohens_kappa([0, 0, 0, 0], [0, 0, 0, 0]) is None

    def test_one_constant_source_scores_exactly_chance(self):
        # a caller who always says HOLD agrees with anyone at chance rate:
        # p_o = 0.75, p_e = 1.0*0.75 = 0.75 → kappa = 0
        assert cohens_kappa([0, 0, 0, 0], [0, 1, 0, 0]) == pytest.approx(0.0)

    def test_empty_or_mismatched_input_is_none(self):
        assert cohens_kappa([], []) is None
        assert cohens_kappa([1, 0], [1]) is None


class TestAgreementMatrix:
    def test_pairwise_alignment_on_shared_timestamps(self):
        ours = series([1, 1, 0, 0, -1, -1])
        # same signals as the kappa hand-check, plus one unshared point
        theirs = series([1, 0, 0, 0, -1, -1]) + [{"ts": ts(99), "signal": 1}]
        report = agreement_matrix({"ours": ours, "theirs": theirs},
                                  min_overlap=5)
        assert report["sources"] == ["ours", "theirs"]
        assert report["overlap"]["ours"]["theirs"] == 6  # ts(99) is unshared
        assert report["agreement"]["ours"]["theirs"] == pytest.approx(5 / 6)
        assert report["kappa"]["ours"]["theirs"] == pytest.approx(0.75)
        assert report["agreement"]["ours"]["ours"] == pytest.approx(1.0)
        pair = report["pairs"][0]
        assert (pair["a"], pair["b"], pair["n"]) == ("ours", "theirs", 6)
        assert len(report["pairs"]) == 1  # upper triangle only

    def test_flat_pair_reports_agreement_but_no_kappa(self):
        flat = series([0, 0, 0, 0, 0, 0])
        report = agreement_matrix({"a": flat, "b": flat}, min_overlap=5)
        assert report["agreement"]["a"]["b"] == pytest.approx(1.0)
        assert report["kappa"]["a"]["b"] is None  # never a fabricated 1.0

    def test_too_few_overlapping_points_is_none(self):
        report = agreement_matrix({"a": series([1, -1, 1]),
                                   "b": series([1, -1, 1])}, min_overlap=5)
        assert report["overlap"]["a"]["b"] == 3
        assert report["agreement"]["a"]["b"] is None
        assert report["kappa"]["a"]["b"] is None

    def test_no_overlap_at_all_is_none(self):
        report = agreement_matrix({"a": series([1, 1, 1, 1, 1, 1], start=0),
                                   "b": series([1, 1, 1, 1, 1, 1], start=50)},
                                  min_overlap=5)
        assert report["overlap"]["a"]["b"] == 0
        assert report["agreement"]["a"]["b"] is None
        assert report["kappa"]["a"]["b"] is None


class TestCrowdingScore:
    #        t0  t1  t2  t3  t4  t5
    # ours    1   1  -1   0   1  -1
    # b1      1   1   1   0   1   1
    # b2      1  -1   1   0  -1   1
    # b3      1   1  -1   0   1  -1
    # majority 1   1   1   0   1   1
    # our directional points: t0 t1 t2 t4 t5 → 5 scored, 3 matched (t0/t1/t4)
    OURS = [1, 1, -1, 0, 1, -1]
    BASELINES = {
        "b1": [1, 1, 1, 0, 1, 1],
        "b2": [1, -1, 1, 0, -1, 1],
        "b3": [1, 1, -1, 0, 1, -1],
    }

    def _score(self, **kwargs):
        return crowding_score(series(self.OURS),
                              {k: series(v) for k, v in self.BASELINES.items()},
                              **kwargs)

    def test_hand_counted_majority_match_share(self):
        score = self._score(min_overlap=5)
        assert score["n_aligned"] == 6
        assert score["n_directional"] == 5
        assert score["n_scored"] == 5
        assert score["n_matched"] == 3
        assert score["crowding"] == pytest.approx(0.6)

    def test_distinctiveness_complements_crowding(self):
        score = self._score(min_overlap=5)
        assert score["distinctiveness"] == pytest.approx(0.4)
        assert score["crowding"] + score["distinctiveness"] == pytest.approx(1.0)

    def test_per_baseline_breakdown_and_chance_correction(self):
        score = self._score(min_overlap=5)
        # b3 matches us on every directional point; b1 only on t0/t1/t4
        assert score["per_baseline"]["b3"]["match_rate"] == pytest.approx(1.0)
        assert score["per_baseline"]["b1"]["match_rate"] == pytest.approx(0.6)
        assert score["per_baseline"]["b3"]["kappa"] == pytest.approx(1.0)
        assert score["kappa_vs_consensus"] is not None

    def test_identical_twin_is_fully_crowded(self):
        ours = series([1, -1, 1, -1, 1, -1])
        twin = {name: series([1, -1, 1, -1, 1, -1]) for name in ("b1", "b2")}
        score = crowding_score(ours, twin, min_overlap=5)
        assert score["crowding"] == pytest.approx(1.0)
        assert score["distinctiveness"] == pytest.approx(0.0)

    def test_tie_between_baselines_has_no_consensus(self):
        ours = series([1, 1, 1, 1, 1, 1])
        split = {"b1": series([1, 1, 1, 1, 1, 1]),
                 "b2": series([-1, -1, -1, -1, -1, -1])}
        score = crowding_score(ours, split, min_overlap=5)
        # every point is a 1-1 tie: no crowd to be crowded with
        assert score["n_no_consensus"] == 6
        assert score["n_scored"] == 0
        assert score["crowding"] is None
        assert score["distinctiveness"] is None

    def test_all_flat_decisions_score_none(self):
        ours = series([0, 0, 0, 0, 0, 0])
        base = {"b1": series([1, 1, 1, 1, 1, 1]),
                "b2": series([1, 1, 1, 1, 1, 1])}
        score = crowding_score(ours, base, min_overlap=5)
        assert score["n_directional"] == 0
        assert score["crowding"] is None

    def test_too_few_points_is_none_not_a_made_up_number(self):
        score = crowding_score(series([1, 1, -1]),
                               {"b1": series([1, 1, 1])}, min_overlap=5)
        assert score["n_scored"] == 3
        assert score["crowding"] is None
        assert score["kappa_vs_consensus"] is None

    def test_no_overlap_is_none(self):
        score = crowding_score(series([1, 1, -1, 1, 1, -1], start=0),
                               {"b1": series([1, 1, 1, 1, 1, 1], start=50)},
                               min_overlap=5)
        assert score["n_aligned"] == 0
        assert score["crowding"] is None

    def test_no_baselines_is_none(self):
        score = crowding_score(series([1, 1, -1, 1, 1, -1]), {}, min_overlap=5)
        assert score["crowding"] is None
        assert score["per_baseline"] == {}


class TestRollingCrowding:
    def test_window_shape(self):
        ours = series([1] * 12)
        base = {"b1": series([1] * 12), "b2": series([1] * 12)}
        rows = rolling_crowding(ours, base, window=5, min_overlap=5)
        assert len(rows) == 12 - 5 + 1
        assert all(row["n"] == 5 for row in rows)
        assert rows[0]["start"] == rows[0]["start"]  # keys are sortable
        assert all(row["crowding"] == pytest.approx(1.0) for row in rows)

    def test_step_strides_the_window(self):
        ours = series([1] * 12)
        base = {"b1": series([1] * 12), "b2": series([1] * 12)}
        rows = rolling_crowding(ours, base, window=5, step=3, min_overlap=5)
        assert len(rows) == len(range(0, 12 - 5 + 1, 3))

    def test_drift_from_distinct_to_crowded_is_visible(self):
        # first half we take the opposite side of the crowd, second half we
        # converge on it — a single whole-sample number would average this away
        ours = series([-1] * 5 + [1] * 5)
        base = {"b1": series([1] * 10), "b2": series([1] * 10)}
        rows = rolling_crowding(ours, base, window=5, min_overlap=5)
        assert rows[0]["crowding"] == pytest.approx(0.0)
        assert rows[-1]["crowding"] == pytest.approx(1.0)
        assert rows[0]["distinctiveness"] == pytest.approx(1.0)

    def test_too_few_aligned_points_is_empty(self):
        ours = series([1, 1, 1])
        base = {"b1": series([1, 1, 1])}
        assert rolling_crowding(ours, base, window=5) == []
        assert rolling_crowding(ours, base, window=0) == []

    def test_thin_window_reports_none_not_zero(self):
        ours = series([0] * 6 + [1] * 6)  # first windows are all flat
        base = {"b1": series([1] * 12), "b2": series([1] * 12)}
        rows = rolling_crowding(ours, base, window=6, min_overlap=5)
        assert rows[0]["crowding"] is None
        assert rows[-1]["crowding"] == pytest.approx(1.0)


# --- the baseline population -------------------------------------------------------

class ScriptedVerdicts:
    """A structured-output model that hands back a scripted verdict per
    call (cycling), recording every prompt it saw."""

    def __init__(self, verdicts, fail_on=None):
        self.verdicts = list(verdicts)
        self.fail_on = fail_on or ()
        self.prompts: list[str] = []
        self.calls = 0

    def with_structured_output(self, schema):
        assert schema is JudgeVerdict
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        index = self.calls
        self.calls += 1
        if index in self.fail_on:
            raise RuntimeError("provider blew up")
        return self.verdicts[index % len(self.verdicts)]


def snapshot(as_of_hour: int = 0):
    from tests.test_pro_agents_base import make_snapshot

    return make_snapshot(as_of=ts(as_of_hour))


class TestBaselinePrompts:
    def test_three_documented_styles(self):
        styles = baseline_prompts()
        assert [s.name for s in styles] == [
            "naive_momentum", "news_sentiment", "indicator_checklist"]
        assert len(styles) % 2 == 1  # odd population → majority vote resolves
        for style in styles:
            assert style.description.strip()
            assert "{symbol}" in style.prompt and "{evidence}" in style.prompt

    def test_every_style_receives_the_same_evidence_pack(self):
        styles = baseline_prompts()
        rendered = [s.render(symbol="BTC-USD", evidence="EV-BLOCK-42")
                    for s in styles]
        assert all("EV-BLOCK-42" in text and "BTC-USD" in text
                   for text in rendered)
        assert len(set(rendered)) == 3  # personas differ

    def test_snapshot_evidence_block_is_deterministic_and_model_free(self):
        snap = snapshot()
        block = snapshot_evidence_block(snap)
        assert block == snapshot_evidence_block(snap)
        assert "last close" in block


class TestRunBaselines:
    def test_scripted_verdicts_become_signed_series(self):
        model = ScriptedVerdicts([
            JudgeVerdict(action="BUY", confidence=70, rationale="up."),
            JudgeVerdict(action="SELL", confidence=60, rationale="down."),
            JudgeVerdict(action="HOLD", confidence=50, rationale="flat."),
        ])
        out = run_baselines(model, [snapshot(0), snapshot(1)])
        assert set(out) == {"naive_momentum", "news_sentiment",
                            "indicator_checklist"}
        # 3 styles x 2 snapshots, verdicts cycling BUY/SELL/HOLD
        assert model.calls == 6
        assert [e["signal"] for e in out["naive_momentum"]] == [1, 1]
        assert [e["signal"] for e in out["news_sentiment"]] == [-1, -1]
        assert [e["signal"] for e in out["indicator_checklist"]] == [0, 0]
        entry = out["naive_momentum"][0]
        assert entry["ts"] == ts(0)
        assert entry["action"] == "BUY" and entry["confidence"] == 70
        assert entry["style"] == "naive_momentum"

    def test_runs_on_the_scripted_fake_pipeline_llm(self):
        # FakePipelineLLM is the package's canned structured-output provider:
        # every baseline gets the default BUY verdict, zero real calls
        out = run_baselines(FakePipelineLLM(), [snapshot(0), snapshot(1)])
        assert all(len(entries) == 2 for entries in out.values())
        assert {e["action"] for entries in out.values() for e in entries} == {
            "BUY"}
        crowded = crowding_score(
            [{"ts": ts(i), "signal": 1} for i in range(2)], out,
            min_overlap=2)
        assert crowded["crowding"] == pytest.approx(1.0)

    def test_overridden_fake_verdict_flows_through(self):
        llm = FakePipelineLLM(overrides={
            JudgeVerdict: JudgeVerdict(action="SELL", confidence=44,
                                       rationale="down.")})
        out = run_baselines(llm, [snapshot(0)])
        assert [e["signal"] for e in out["news_sentiment"]] == [-1]
        prompt = llm.prompts["JudgeVerdict"][0]
        assert snapshot(0).symbol in prompt  # the pack is symbol-scoped

    def test_evidence_for_override_feeds_the_pipeline_pack(self):
        model = ScriptedVerdicts([
            JudgeVerdict(action="BUY", confidence=70, rationale="up.")])
        run_baselines(model, [snapshot(0)],
                      evidence_for=lambda snap: "PIPELINE-PACK")
        assert all("PIPELINE-PACK" in prompt for prompt in model.prompts)
        assert len(model.prompts) == 3  # one per style, same pack

    def test_study_report_combines_ours_and_the_population(self):
        # the pipeline arm runs on the scripted FakePipelineLLM (canned BUY);
        # the baselines get their own scripted model, so the two arms differ
        import json

        from tests.test_pro_pipeline_graph import CONFIG, pipeline_snapshot
        from tradingagents.pro.evals.crowding_study import study

        base = pipeline_snapshot()
        snaps = [pipeline_snapshot(as_of=base.as_of + timedelta(hours=i))
                 for i in range(4)]
        model = ScriptedVerdicts([
            JudgeVerdict(action="BUY", confidence=70, rationale="up."),
            JudgeVerdict(action="SELL", confidence=60, rationale="down."),
        ])
        report = study(FakePipelineLLM(), CONFIG, snaps, model=model,
                       window=2, min_overlap=2)
        assert report["n_points"] == 4
        assert report["errors"] == []
        assert [e["action"] for e in report["ours"]] == ["BUY"] * 4
        assert set(report["baselines"]) == {
            "naive_momentum", "news_sentiment", "indicator_checklist"}
        assert report["agreement"]["sources"][0] == "ours"
        assert report["crowding"]["n_aligned"] == 4
        assert len(report["rolling"]) == 3  # 4 aligned points, window 2
        assert len(report["styles"]) == 3
        # the population read the PIPELINE's evidence pack (agent ids), not
        # a re-rendered snapshot — that identity is the point of the study
        assert "[rsi]" in model.prompts[0]
        json.loads(json.dumps(report))  # artifact is plain JSON

    def test_failed_call_skips_the_point_instead_of_faking_a_hold(self):
        model = ScriptedVerdicts(
            [JudgeVerdict(action="BUY", confidence=70, rationale="up.")],
            fail_on=(0,))  # first style, first snapshot
        out = run_baselines(model, [snapshot(0), snapshot(1)])
        assert len(out["naive_momentum"]) == 1  # the failed point is absent
        assert out["naive_momentum"][0]["ts"] == ts(1)
        assert len(out["news_sentiment"]) == 2
