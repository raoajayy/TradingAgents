"""Overfitting-aware validation guards (roadmap P2 / track T3): PSR, expected
max Sharpe, deflated Sharpe, and PBO via CSCV. Checked against their defining
properties and simple worked cases."""

import random

import pytest

from tradingagents.pro.backtest.validation import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
)


class TestPSR:
    def test_equals_benchmark_is_half(self):
        assert probabilistic_sharpe_ratio(0.5, 0.5, 250) == pytest.approx(0.5)

    def test_above_benchmark_with_many_obs_is_confident(self):
        assert probabilistic_sharpe_ratio(0.3, 0.0, 500) > 0.95

    def test_more_observations_raise_confidence(self):
        few = probabilistic_sharpe_ratio(0.2, 0.0, 30)
        many = probabilistic_sharpe_ratio(0.2, 0.0, 1000)
        assert 0.5 < few < many

    def test_negative_skew_fat_tails_lower_confidence(self):
        normal = probabilistic_sharpe_ratio(0.3, 0.0, 250, skew=0.0, kurtosis=3.0)
        ugly = probabilistic_sharpe_ratio(0.3, 0.0, 250, skew=-1.5, kurtosis=9.0)
        assert ugly < normal


class TestExpectedMaxSharpe:
    def test_single_trial_no_selection(self):
        assert expected_max_sharpe(0.04, 1) == 0.0

    def test_grows_with_trials_and_variance(self):
        assert expected_max_sharpe(0.04, 100) > expected_max_sharpe(0.04, 10) > 0
        assert expected_max_sharpe(0.09, 50) > expected_max_sharpe(0.04, 50)


class TestDeflatedSharpe:
    def test_single_impressive_trial_is_credible(self):
        # one trial, strong Sharpe, long sample → high DSR
        dsr = deflated_sharpe_ratio(0.35, [0.35], n_obs=750)
        assert dsr > 0.9

    def test_best_of_many_noisy_trials_deflates(self):
        # the same headline Sharpe, but selected as the best of 200 scattered
        # trials → the benchmark rises and the DSR collapses
        rng = random.Random(1)
        trials = [rng.gauss(0.0, 0.25) for _ in range(200)]
        trials[0] = 0.35
        dsr = deflated_sharpe_ratio(0.35, trials, n_obs=750)
        assert dsr < 0.5


class TestPBO:
    def _matrix(self, rows, cols, fn):
        return [[fn(c, t) for t in range(cols)] for c in range(rows)]

    def test_dominant_config_has_low_pbo(self):
        # config 0 is best every period → the IS winner is always the OOS
        # winner → PBO ~ 0
        m = self._matrix(8, 40, lambda c, t: (10.0 if c == 0 else float(c % 3)))
        assert probability_of_backtest_overfitting(m, n_slices=8) < 0.1

    def test_pure_noise_is_overfit_prone(self):
        # no persistent edge: the in-sample winner is just the luckiest sample
        # and reverses out-of-sample → high PBO (the correct "don't deploy"
        # signal), well separated from the dominant-config case (~0)
        rng = random.Random(7)
        m = self._matrix(10, 80, lambda c, t: rng.gauss(0, 1))
        pbo = probability_of_backtest_overfitting(m, n_slices=8)
        assert pbo > 0.5

    def test_too_few_configs_returns_zero(self):
        assert probability_of_backtest_overfitting([[1.0, 2.0, 3.0]]) == 0.0


# ── numpy analytics module (roadmap P2-02) ──────────────────────────────────
# tradingagents.pro.analytics.validation: purged/embargoed K-fold + the
# returns-based DSR/PBO surfaced on the backtest API. Aliased imports — the
# stdlib backtest.validation module above shares two function names.

import numpy as np  # noqa: E402

from tradingagents.pro.analytics.validation import (  # noqa: E402
    deflated_sharpe_ratio as np_dsr,
    probability_of_backtest_overfitting as np_pbo,
    purged_kfold_splits,
)


class TestPurgedKFold:
    def test_test_blocks_cover_all_indices_exactly_once(self):
        splits = purged_kfold_splits(103, 5, embargo_frac=0.02)
        assert len(splits) == 5
        covered = np.concatenate([test for _, test in splits])
        assert sorted(covered.tolist()) == list(range(103))

    def test_no_train_test_overlap(self):
        for train, test in purged_kfold_splits(100, 4, embargo_frac=0.05):
            assert not set(train.tolist()) & set(test.tolist())

    def test_purge_drops_train_samples_adjacent_to_test(self):
        for train, test in purged_kfold_splits(100, 4, embargo_frac=0.0):
            train_set = set(train.tolist())
            assert test[0] - 1 not in train_set  # purged before the block
            assert test[-1] + 1 not in train_set  # purged after the block

    def test_embargo_respected_after_test_block(self):
        n, embargo_frac = 100, 0.05
        embargo = int(embargo_frac * n)
        for train, test in purged_kfold_splits(n, 4, embargo_frac):
            train_set = set(train.tolist())
            for j in range(test[-1] + 1, min(n, test[-1] + 1 + embargo)):
                assert j not in train_set

    def test_temporal_order_no_shuffling(self):
        splits = purged_kfold_splits(60, 3)
        for train, test in splits:
            assert (np.diff(test) == 1).all()  # contiguous test block
            assert (np.diff(train) > 0).all()  # sorted train indices
        starts = [int(test[0]) for _, test in splits]
        assert starts == sorted(starts)

    def test_degenerate_inputs_return_empty(self):
        assert purged_kfold_splits(5, 4) == []  # n < 2k
        assert purged_kfold_splits(100, 1) == []  # k < 2


class TestNumpyDSR:
    def test_pure_noise_with_many_trials_is_insignificant(self):
        rng = np.random.default_rng(42)
        returns = rng.normal(0.0, 0.01, 500)
        out = np_dsr(returns, n_trials=200)
        assert out["dsr"] is not None and out["dsr"] < 0.2
        assert out["n"] == 500 and out["n_trials"] == 200

    def test_strong_consistent_returns_single_trial_is_credible(self):
        rng = np.random.default_rng(7)
        returns = rng.normal(0.003, 0.01, 500)  # per-period SR ~ 0.3
        out = np_dsr(returns, n_trials=1)
        assert out["dsr"] > 0.95
        assert out["sr"] == pytest.approx(0.3, abs=0.15)

    def test_dsr_deflates_as_n_trials_grows(self):
        # published-example-style monotonicity: same observed Sharpe, more
        # trials → higher expected-max hurdle → strictly lower DSR
        rng = np.random.default_rng(3)
        returns = rng.normal(0.002, 0.01, 250)
        d1 = np_dsr(returns, n_trials=1)["dsr"]
        d10 = np_dsr(returns, n_trials=10)["dsr"]
        d100 = np_dsr(returns, n_trials=100)["dsr"]
        assert d1 > d10 > d100

    def test_benchmark_raises_the_bar(self):
        rng = np.random.default_rng(11)
        returns = rng.normal(0.002, 0.01, 250)
        assert (np_dsr(returns, 1, sr_benchmark=0.0)["dsr"]
                > np_dsr(returns, 1, sr_benchmark=0.1)["dsr"])

    def test_moments_reported(self):
        rng = np.random.default_rng(5)
        out = np_dsr(rng.normal(0, 0.01, 2000), n_trials=1)
        assert out["skew"] == pytest.approx(0.0, abs=0.2)
        assert out["kurt"] == pytest.approx(3.0, abs=0.5)

    def test_degenerate_inputs_return_none_fields(self):
        for bad in ([], [0.01] * 5, [0.0] * 50):  # short / short / zero-var
            out = np_dsr(bad, n_trials=10)
            assert out["sr"] is None and out["dsr"] is None
            assert out["n_trials"] == 10  # metadata still reported


class TestNumpyPBO:
    def test_random_configs_have_high_pbo(self):
        # no persistent edge: the IS winner's OOS rank is uniform, so PBO
        # sits near 0.5 (vs ~0 for a dominant config). One seed gives a
        # coarse estimate, so average a few for a stable statistic.
        pbos = []
        for seed in range(5):
            rng = np.random.default_rng(seed)
            matrix = rng.normal(0, 0.01, size=(160, 12))  # periods × configs
            pbos.append(np_pbo(matrix, n_splits=16))
        assert all(p is not None for p in pbos)
        assert float(np.mean(pbos)) > 0.35  # >> the dominant-config ~0

    def test_dominant_config_has_low_pbo(self):
        rng = np.random.default_rng(1)
        matrix = rng.normal(0, 0.01, size=(160, 12))
        matrix[:, 0] += 0.02  # one config wins by construction
        pbo = np_pbo(matrix, n_splits=8)
        assert pbo is not None and pbo < 0.1

    def test_combination_cap_is_deterministic(self):
        # C(16, 8) = 12870 > 500 → the seeded sampling path; identical calls
        # must produce identical PBO
        rng = np.random.default_rng(2)
        matrix = rng.normal(0, 0.01, size=(256, 8))
        assert np_pbo(matrix, n_splits=16) == np_pbo(matrix, n_splits=16)

    def test_degenerate_inputs_return_none(self):
        assert np_pbo(np.zeros((100, 1))) is None  # one config
        assert np_pbo(np.zeros((4, 5))) is None  # too few periods
        assert np_pbo([1.0, 2.0, 3.0]) is None  # not a matrix
