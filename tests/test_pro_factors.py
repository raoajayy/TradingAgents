"""P3-03 factor mining: safe expression evaluator, IC math, OOS purging,
the mining loop scaffold, and ComputedFactorAgent evidence."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from tests.pro_fakes import make_bars
from tradingagents.contracts import AssetClass, Direction, MarketSnapshot, Timeframe
from tradingagents.pro.agents.computed_factor import (
    MINED_FACTORS_KEY,
    ComputedFactorAgent,
    attach_mined_factors,
    load_mined_factor_agents,
    store_survivors,
)
from tradingagents.pro.analytics.factors import (
    FactorExpr,
    evaluate_factor_oos,
    factor_ic,
    forward_returns,
    ic_decay,
)
from tradingagents.pro.evals.factor_mining import (
    FactorProposal,
    FactorProposalBatch,
    mine,
    propose_factors,
)
from tradingagents.pro.evals.scripted import FakePipelineLLM
from tradingagents.pro.store import EventStore

AS_OF = datetime(2026, 7, 6, tzinfo=timezone.utc)


def toy_frame(n: int = 30) -> pd.DataFrame:
    close = pd.Series(np.linspace(100.0, 129.0, n))
    return pd.DataFrame({
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1000.0 + np.arange(n),
    })


# --- expression safety --------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "__import__('os').system('rm -rf /')",
    "().__class__.__mro__",
    "close.__class__",
    "close.mean()",                 # attribute call escape
    "getattr(close, 'values')",     # non-whitelisted function
    "eval('1+1')",
    "exec('x=1')",
    "open('/etc/passwd')",          # open is a column, not a callable
    "close[0]",                     # subscript
    "lambda: 1",
    "[c for c in close]",
    "close if volume else open",    # conditional expression
    "close and volume",             # boolean ops
    "ts_mean(close, volume)",       # non-literal window
    "ts_mean(close, 2.5)",          # non-int window
    "ts_mean(close, w)",            # unknown name as window
    "zscore(close)",                # wrong arity
    "corr(close, volume)",          # wrong arity
    "rank(close, 5, 6)",            # wrong arity
    "ts_mean(close, 999999)",       # absurd window
    "unknown_col + 1",
    "'bullish'",                    # string constant
    "close < volume < open",        # chained comparison
    "zscore(x=close, w=5)",         # keyword args
    "close ** 2",                   # pow not whitelisted
    "close % 5",
    "",
])
def test_parse_rejects_escapes_and_junk(bad):
    with pytest.raises(ValueError):
        FactorExpr.parse(bad)


def test_parse_refuses_negative_lags_the_lookahead_smuggle():
    for leak in ("delay(close, -1)", "delta(close, -5)", "ts_mean(close, -3)"):
        with pytest.raises(ValueError, match="future|>= 1"):
            FactorExpr.parse(leak)
    with pytest.raises(ValueError):
        FactorExpr.parse("delta(close, 0)")  # delta needs lag >= 1


def test_parse_accepts_the_documented_grammar():
    expr = FactorExpr.parse(
        "zscore(delta(close, 5) / delay(close, 5), 20) "
        "- rank(volume, 10) + sign(close - ts_mean(close, 8)) "
        "* abs(log(close / open)) + corr(high, low, 6) * (close > open)")
    assert expr.names == {"close", "open", "high", "low", "volume"}


def test_parse_supports_registered_extra_columns():
    with pytest.raises(ValueError):
        FactorExpr.parse("zscore(dxy, 10)")
    expr = FactorExpr.parse("zscore(dxy, 10)", extra_columns=("dxy",))
    df = toy_frame(20)
    df["dxy"] = np.linspace(100, 105, 20)
    assert np.isfinite(expr.evaluate(df).iloc[-1])


# --- operator correctness on toy frames ---------------------------------------

def test_ops_match_hand_computed_values():
    df = toy_frame(12)  # close = 100, 101, ..., linear ramp
    close = df["close"]
    delay = FactorExpr.parse("delay(close, 2)").evaluate(df)
    assert delay.iloc[5] == pytest.approx(close.iloc[3])
    delta = FactorExpr.parse("delta(close, 3)").evaluate(df)
    assert delta.iloc[7] == pytest.approx(close.iloc[7] - close.iloc[4])
    mean = FactorExpr.parse("ts_mean(close, 4)").evaluate(df)
    assert mean.iloc[6] == pytest.approx(close.iloc[3:7].mean())
    std = FactorExpr.parse("ts_std(close, 4)").evaluate(df)
    assert std.iloc[6] == pytest.approx(close.iloc[3:7].std(ddof=0))
    z = FactorExpr.parse("zscore(close, 4)").evaluate(df)
    window = close.iloc[3:7]
    assert z.iloc[6] == pytest.approx(
        (close.iloc[6] - window.mean()) / window.std(ddof=0))
    lo = FactorExpr.parse("ts_min(low, 5)").evaluate(df)
    assert lo.iloc[8] == pytest.approx(df["low"].iloc[4:9].min())
    hi = FactorExpr.parse("ts_max(high, 5)").evaluate(df)
    assert hi.iloc[8] == pytest.approx(df["high"].iloc[4:9].max())
    # rising series: last value is the max of every window
    rank = FactorExpr.parse("rank(close, 3)").evaluate(df)
    assert rank.iloc[5] == pytest.approx(1.0)
    comp = FactorExpr.parse("(close > ts_mean(close, 3)) * 2 - 1").evaluate(df)
    assert comp.iloc[5] == pytest.approx(1.0)  # ramp is above its own mean
    corr = FactorExpr.parse("corr(close, volume, 6)").evaluate(df)
    assert corr.iloc[10] == pytest.approx(1.0)  # both linear ramps


def test_division_by_zero_yields_nan_not_inf():
    df = toy_frame(10)
    out = FactorExpr.parse("close / (close - close)").evaluate(df)
    assert out.isna().all()


def test_log_of_nonpositive_is_nan():
    df = toy_frame(10)
    out = FactorExpr.parse("log(close - close)").evaluate(df)
    assert out.isna().all()


def test_evaluate_normalizes_capitalized_columns():
    df = toy_frame(30).rename(columns=str.capitalize)  # Open, High, ...
    out = FactorExpr.parse("delta(close, 1)").evaluate(df)
    assert out.iloc[-1] == pytest.approx(1.0)


def test_evaluate_missing_column_raises():
    df = toy_frame(10).drop(columns=["volume"])
    with pytest.raises(ValueError, match="volume"):
        FactorExpr.parse("zscore(volume, 5)").evaluate(df)


def test_factor_is_causal_truncation_invariant():
    """factor[t] computed on the full frame equals factor[t] computed on a
    frame truncated at t — i.e. no value depends on later bars."""
    rng = np.random.default_rng(7)
    n = 80
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, n)))
    df = pd.DataFrame({"open": close.shift(1).fillna(close[0]),
                       "high": close + 1, "low": close - 1,
                       "close": close, "volume": 1000 + rng.integers(0, 50, n)})
    expr = FactorExpr.parse("zscore(close, 10) + delta(volume, 3) / ts_std(close, 8)")
    full = expr.evaluate(df)
    cut = 50
    truncated = expr.evaluate(df.iloc[:cut])
    pd.testing.assert_series_equal(full.iloc[:cut], truncated,
                                   check_names=False)


# --- IC math -------------------------------------------------------------------

def test_factor_ic_matches_hand_computed_spearman():
    # one swapped adjacent pair among 8 ranks: rho = 1 - 6*2/(8*63)
    f = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8])
    r = pd.Series([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 0.7])
    assert factor_ic(f, r) == pytest.approx(1 - 12 / 504)
    assert factor_ic(f, -r) == pytest.approx(-(1 - 12 / 504))
    assert factor_ic(f, r, method="pearson") == pytest.approx(
        np.corrcoef(f, r)[0, 1])
    with pytest.raises(ValueError):
        factor_ic(f, r, method="kendall")


def test_factor_ic_degenerate_inputs_return_none():
    f = pd.Series([1.0, 2, 3, 4, 5, 6, 7, 8])
    assert factor_ic(f, pd.Series([0.5] * 8)) is None          # constant returns
    assert factor_ic(pd.Series([np.nan] * 8), f) is None       # no pairs
    assert factor_ic(f.iloc[:4], f.iloc[:4]) is None           # too few obs


def test_forward_returns_indexing():
    close = pd.Series([100.0, 110.0, 121.0, 133.1])
    fwd = forward_returns(close, 1)
    assert fwd.iloc[0] == pytest.approx(0.10)
    assert np.isnan(fwd.iloc[-1])
    with pytest.raises(ValueError):
        forward_returns(close, 0)


def test_ic_decay_shrinks_for_a_one_step_signal():
    rng = np.random.default_rng(3)
    n = 300
    z = rng.normal(0, 1, n)
    r = np.empty(n)
    r[0] = 0.0
    r[1:] = 0.01 * z[:-1] + 0.001 * rng.normal(0, 1, n - 1)
    factor = pd.Series(z)
    decay = ic_decay(factor, pd.Series(r), horizons=(1, 5, 20))
    assert decay[1] > 0.8
    assert decay[1] > abs(decay[20])


# --- OOS evaluation over purged folds -------------------------------------------

def predictive_frame(n: int = 400, strength: float = 0.01,
                     seed: int = 11) -> pd.DataFrame:
    """Bars where next-bar return is driven by the volume anomaly: the
    factor zscore(volume, ...) is predictive by construction."""
    rng = np.random.default_rng(seed)
    z = rng.normal(0, 1, n)
    r = np.empty(n)
    r[0] = 0.0
    r[1:] = strength * z[:-1] + 0.001 * rng.normal(0, 1, n - 1)
    close = pd.Series(100 * np.cumprod(1 + r))
    return pd.DataFrame({
        "open": close.shift(1).fillna(close[0]),
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 1000.0 + 50.0 * z,
    })


def test_evaluate_factor_oos_finds_the_planted_signal():
    df = predictive_frame()
    res = evaluate_factor_oos("zscore(volume, 10)", df, horizon=1, k=4)
    assert res["n_folds"] >= 3
    assert res["ic_mean"] > 0.5
    assert res["ic_ir"] > 1.0
    assert set(res) >= {"ic_mean", "ic_std", "ic_ir", "n_folds", "decay"}
    # a signal-free factor scores near zero on the same frame
    noise = evaluate_factor_oos("rank(open, 5)", df, horizon=1, k=4)
    assert abs(noise["ic_mean"]) < abs(res["ic_mean"]) / 3


def test_evaluate_factor_oos_refuses_leaking_expressions():
    df = predictive_frame(100)
    with pytest.raises(ValueError, match="future"):
        evaluate_factor_oos("delay(close, -1)", df, horizon=1, k=4)


def test_evaluate_factor_oos_degenerate_frame():
    res = evaluate_factor_oos("zscore(close, 5)", toy_frame(12), horizon=1, k=4)
    assert res["ic_mean"] is None
    assert res["n_folds"] == 0


# --- mining loop -----------------------------------------------------------------

WEAK_BASELINES = {
    "mom_20_z": "zscore(delta(close, 20) / delay(close, 20), 60)",
    "rev_5_rank": "-rank(delta(close, 5) / delay(close, 5), 20)",
}

SCRIPTED = FactorProposalBatch(proposals=[
    FactorProposal(name="bad_escape", expression="close.__class__",
                   rationale="attempt an attribute escape"),
    FactorProposal(name="weak_noise", expression="rank(open, 5)",
                   rationale="random open ranking, no edge"),
    FactorProposal(name="vol_anomaly", expression="zscore(volume, 10)",
                   rationale="volume anomalies lead next-bar returns"),
])


def test_mine_end_to_end_on_fake_pipeline_llm():
    llm = FakePipelineLLM(overrides={FactorProposalBatch: SCRIPTED})
    df = predictive_frame()
    report = mine(llm, df, iterations=1, horizon=1, k=4,
                  symbol="TEST", baselines=WEAK_BASELINES)
    assert report["counts"] == {"proposed": 3, "invalid": 1, "weak": 1,
                                "duplicate": 0, "survivors": 1}
    fates = {p["name"]: p["status"] for p in report["iterations"][0]["proposals"]}
    assert fates == {"bad_escape": "invalid", "weak_noise": "weak",
                     "vol_anomaly": "survivor"}
    (survivor,) = report["survivors"]
    assert survivor["expression"] == "zscore(volume, 10)"
    assert survivor["ic_mean"] > report["ic_bar"]
    assert {"name", "expression", "rationale", "ic_mean", "ic_std",
            "ic_ir", "n_folds", "decay"} <= set(survivor)
    assert report["baselines"]["mom_20_z"]["ic_mean"] is not None
    # invalid proposal carries the parser's reason
    bad = report["iterations"][0]["proposals"][0]
    assert "forbidden" in bad["reason"] or "disallowed" in bad["reason"]
    # the proposer prompt documented the grammar
    assert "zscore" in llm.prompts["FactorProposalBatch"][0]


class SequencedLLM:
    """Returns a different proposal batch per call; records prompts."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.prompts = []

    def with_structured_output(self, schema):
        assert schema is FactorProposalBatch
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return self.batches.pop(0)


def test_mine_feeds_rejection_reasons_back_to_the_proposer():
    llm = SequencedLLM([
        FactorProposalBatch(proposals=[
            FactorProposal(name="leak", expression="delay(close, -1)",
                           rationale="tomorrow's close")]),
        FactorProposalBatch(proposals=[
            FactorProposal(name="ok", expression="zscore(volume, 10)",
                           rationale="volume anomaly")]),
    ])
    report = mine(llm, predictive_frame(), iterations=2, horizon=1, k=4,
                  baselines=WEAK_BASELINES)
    assert len(llm.prompts) == 2
    assert "Previous rejections" not in llm.prompts[0]
    assert "delay(close, -1)" in llm.prompts[1]  # reason fed back
    assert "future" in llm.prompts[1]
    assert [s["name"] for s in report["survivors"]] == ["ok"]


def test_mine_marks_duplicates_and_survives_proposer_failure():
    llm = FakePipelineLLM(overrides={FactorProposalBatch: SCRIPTED})
    report = mine(llm, predictive_frame(), iterations=2, horizon=1, k=4,
                  baselines=WEAK_BASELINES)
    assert report["counts"]["duplicate"] == 3  # round 2 repeats round 1
    assert report["counts"]["survivors"] == 1

    class Broken:
        def with_structured_output(self, schema):
            raise RuntimeError("no structured output")

    report = mine(Broken(), predictive_frame(), iterations=1, horizon=1, k=4,
                  baselines=WEAK_BASELINES)
    assert report["counts"]["proposed"] == 0
    assert report["survivors"] == []


def test_propose_factors_returns_empty_on_none():
    class NoneLLM:
        def with_structured_output(self, schema):
            return self

        def invoke(self, prompt):
            return None

    assert propose_factors(NoneLLM(), {}) == []


# --- ComputedFactorAgent ------------------------------------------------------

def factor_snapshot(n_bars: int = 60) -> MarketSnapshot:
    return MarketSnapshot(symbol="XAUUSD", asset=AssetClass.GOLD,
                          as_of=AS_OF, bars=make_bars(n=n_bars))


def test_computed_factor_agent_renders_evidence_with_refs():
    agent = ComputedFactorAgent("close_z", "zscore(close, 10)",
                                ic_mean=0.08, ic_ir=1.4)
    assert agent.agent_id == "factor_close_z"
    evidence = agent.analyze(factor_snapshot())
    assert evidence is not None
    ref_names = {r.name for r in evidence.data_refs}
    assert "FACTOR_CLOSE_Z" in ref_names
    assert "FACTOR_CLOSE_Z_IC_OOS" in ref_names
    assert "FACTOR_CLOSE_Z_BARS_USED" in ref_names
    value_ref = next(r for r in evidence.data_refs if r.name == "FACTOR_CLOSE_Z")
    assert isinstance(value_ref.value, float)  # a computed number, not prose
    assert all(r.source == "quant_engine" for r in evidence.data_refs)
    assert evidence.sources[0].id == "quant_engine"
    # steadily rising closes: positive zscore x positive IC -> bullish
    assert evidence.direction == Direction.BULLISH
    assert "zscore(close, 10)" in evidence.claim
    assert 35 <= evidence.confidence <= 65


def test_computed_factor_agent_abstains_without_enough_bars():
    agent = ComputedFactorAgent("close_z", "zscore(close, 10)", ic_mean=0.08)
    assert agent.analyze(factor_snapshot(n_bars=5)) is None  # warm-up NaN
    # wrong timeframe: no matching bars at all
    h4_agent = ComputedFactorAgent("close_z", "zscore(close, 10)",
                                   ic_mean=0.08, timeframe=Timeframe.H4)
    assert h4_agent.analyze(factor_snapshot()) is None


def test_computed_factor_agent_rejects_unsafe_expression_at_construction():
    with pytest.raises(ValueError):
        ComputedFactorAgent("evil", "__import__('os')")


# --- survivor registration via the event-store kv -------------------------------

def test_empty_or_absent_kv_is_a_strict_noop(tmp_path):
    store = EventStore(tmp_path / "events.db")
    try:
        assert load_mined_factor_agents(store) == []
        roster = ["sentinel_agent"]
        assert attach_mined_factors(roster, store) is roster  # same object
        store.put_kv(MINED_FACTORS_KEY, "")
        assert load_mined_factor_agents(store) == []
        store.put_kv(MINED_FACTORS_KEY, "[]")
        assert attach_mined_factors(roster, store) is roster
        store.put_kv(MINED_FACTORS_KEY, "{not json")
        assert load_mined_factor_agents(store) == []
        store.put_kv(MINED_FACTORS_KEY, '{"name": "not-a-list"}')
        assert load_mined_factor_agents(store) == []
    finally:
        store.close()


def test_survivors_round_trip_and_bad_records_are_skipped(tmp_path):
    store = EventStore(tmp_path / "events.db")
    try:
        store_survivors(store, [
            {"name": "vol_anomaly", "expression": "zscore(volume, 10)",
             "ic_mean": 0.12, "ic_ir": 1.8},
            {"name": "corrupt", "expression": "__import__('os')"},  # skipped
            {"name": "incomplete"},                                  # skipped
            "not-a-dict",                                            # skipped
        ])
        agents = load_mined_factor_agents(store, timeframe=Timeframe.D1)
        assert [a.agent_id for a in agents] == ["factor_vol_anomaly"]
        assert agents[0].ic_mean == pytest.approx(0.12)
        roster = attach_mined_factors(["existing"], store)
        assert roster[0] == "existing"
        assert roster[1].agent_id == "factor_vol_anomaly"
        # and the loaded agent actually produces evidence
        evidence = roster[1].analyze(factor_snapshot())
        assert evidence is not None
        assert evidence.agent_id == "factor_vol_anomaly"
    finally:
        store.close()


# --- P3-03 wiring: survivors reach real pipeline runs ----------------------

def test_pipeline_run_includes_mined_factor_evidence(tmp_path):
    """The audited gap: attach_mined_factors existed but no pipeline path
    called it. With a factor_store wired, a scripted run's QUANT evidence
    must include the computed factor agent's deterministic claim."""
    from tests.test_pro_pipeline_graph import CONFIG, pipeline_snapshot
    from tradingagents.pro.pipeline import run_pipeline

    store = EventStore(tmp_path / "pro.db")
    try:
        store_survivors(store, [
            {"name": "vol_anomaly", "expression": "zscore(volume, 10)",
             "ic_mean": 0.12, "ic_ir": 1.8},
        ])
        state = run_pipeline(FakePipelineLLM(), CONFIG, pipeline_snapshot(),
                             factor_store=store)
        quant = state["evidence_by_team"]["quant"]
        factor_evidence = [e for e in quant
                           if e.agent_id == "factor_vol_anomaly"]
        assert len(factor_evidence) == 1
        assert "zscore(volume, 10)" in factor_evidence[0].claim
    finally:
        store.close()


def test_pipeline_run_without_store_is_unchanged(tmp_path):
    from tests.test_pro_pipeline_graph import CONFIG, pipeline_snapshot
    from tradingagents.pro.pipeline import run_pipeline

    state = run_pipeline(FakePipelineLLM(), CONFIG, pipeline_snapshot())
    assert not [e for e in state["evidence_by_team"]["quant"]
                if e.agent_id.startswith("factor_")]


def test_build_service_wires_the_event_store_into_runs(tmp_path, monkeypatch):
    """End-to-end through the prod assembly: survivors seeded in the P2-01
    event store surface as evidence in a service run (main.py passes
    factor_store=event_store into the pipeline kwargs)."""
    pytest.importorskip("fastapi")
    import tradingagents.pro.main as main_module
    from tests.test_pro_pipeline_graph import pipeline_snapshot
    from tradingagents.pro.ingestion import builder as builder_module

    db_path = tmp_path / "pro.db"
    monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(db_path))
    seed = EventStore(db_path)
    store_survivors(seed, [
        {"name": "vol_anomaly", "expression": "zscore(volume, 10)",
         "ic_mean": 0.12, "ic_ir": 1.8},
    ])
    seed.close()

    class FakeBuilder:
        def build(self, symbol, asset, **kwargs):
            return pipeline_snapshot()

    monkeypatch.setattr(builder_module, "build_gold_pipeline",
                        lambda *a, **k: FakeBuilder())
    service, state = main_module.build_service(llm=FakePipelineLLM(),
                                               data_dir=tmp_path)
    # hermetic: the real calendar_fn reaches for the network-backed intel
    # service; no upcoming event = the event gate passes open
    service.pipeline_kwargs["calendar_fn"] = lambda: None
    summary = service.run_once()
    assert summary["run_id"]
    run = state.latest_run()
    quant = run.state["evidence_by_team"]["quant"]
    assert any(e.agent_id == "factor_vol_anomaly" for e in quant)
