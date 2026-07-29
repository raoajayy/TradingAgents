"""Overfitting-aware validation primitives (roadmap P2-02) — pure numpy.

Purged/embargoed K-fold splits, the Deflated Sharpe Ratio and the
Probability of Backtest Overfitting, as vectorized building blocks for the
backtest API and the P3 conformal/splitter consumers. The stdlib sibling
(`tradingagents.pro.backtest.validation`) keeps the optimizer's
trial-Sharpe-based DSR; this module works directly on return series and
index arrays.

References (formulas paraphrased, not reproduced verbatim):
- Bailey & López de Prado, "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality" (2014): the PSR
  with skew/kurtosis correction, and the expected-maximum-Sharpe benchmark
  approximated via the Euler–Mascheroni constant.
- Bailey, Borwein, López de Prado & Zhu, "The Probability of Backtest
  Overfitting" (2015): CSCV — combinatorially symmetric cross-validation.
- López de Prado, "Advances in Financial Machine Learning" (2018), ch. 7:
  purged K-fold with embargo.

All functions handle degenerate inputs (too few observations, zero
variance) by returning None fields — they never raise on bad data.
"""

from __future__ import annotations

import statistics
from itertools import combinations
from math import comb

import numpy as np

_NORMAL = statistics.NormalDist()
_EULER_MASCHERONI = 0.5772156649015329
_MIN_OBS = 10  # below this, any Sharpe inference is noise
_MAX_CSCV_COMBINATIONS = 500  # cap for tractability; sampled with a fixed seed


def purged_kfold_splits(
    n: int, k: int, embargo_frac: float = 0.0
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Purged, embargoed K-fold splits over ``n`` sequential samples.

    Standard K-fold leaks when samples are serially dependent: train bars
    adjacent to the test block share information with it (overlapping label
    horizons, autocorrelation). Following López de Prado (2018, ch. 7) each
    fold therefore (a) *purges* the train samples immediately bordering the
    test block on both sides, and (b) *embargoes* an additional
    ``int(embargo_frac * n)`` samples AFTER the test block (information flows
    forward in time, so post-test leakage needs the wider exclusion).

    Folds are contiguous, temporal order is preserved (no shuffling), and
    every index appears in exactly one test block. Returns a list of
    ``(train_idx, test_idx)`` int arrays; empty list when the inputs are
    degenerate (``n < 2*k`` or ``k < 2``).
    """
    if k < 2 or n < 2 * k:
        return []
    embargo = int(max(0.0, embargo_frac) * n)
    indices = np.arange(n)
    bounds = np.linspace(0, n, k + 1, dtype=int)
    splits: list[tuple[np.ndarray, np.ndarray]] = []
    for fold in range(k):
        start, stop = int(bounds[fold]), int(bounds[fold + 1])
        test_idx = indices[start:stop]
        # purge one sample on each side of the test block, embargo after it
        train_mask = np.ones(n, dtype=bool)
        train_mask[max(0, start - 1) : min(n, stop + 1 + embargo)] = False
        train_idx = indices[train_mask]
        if train_idx.size:
            splits.append((train_idx, test_idx))
    return splits


def _expected_max_sharpe(sr_std: float, n_trials: int) -> float:
    """E[max SR] of ``n_trials`` independent zero-skill trials whose Sharpe
    estimates have standard deviation ``sr_std`` — Bailey & López de Prado's
    (2014) approximation using the Euler–Mascheroni constant γ:

        E[max] ≈ sr_std · ((1−γ)·Z⁻¹(1−1/N) + γ·Z⁻¹(1−1/(N·e)))

    Zero for a single trial: no selection happened, nothing to deflate."""
    if n_trials <= 1 or sr_std <= 0:
        return 0.0
    g = _EULER_MASCHERONI
    return float(
        sr_std
        * (
            (1 - g) * _NORMAL.inv_cdf(1 - 1.0 / n_trials)
            + g * _NORMAL.inv_cdf(1 - 1.0 / (n_trials * np.e))
        )
    )


def deflated_sharpe_ratio(
    returns, n_trials: int = 1, sr_benchmark: float = 0.0
) -> dict:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014) of one return
    series selected after ``n_trials`` configuration attempts.

    Two pieces:

    1. **PSR** — the probability that the true Sharpe exceeds a benchmark,
       given the observed per-period Sharpe ``sr`` over ``n`` observations,
       corrected for non-normality::

           PSR = Φ( (sr − sr*) · √(n−1) / √(1 − γ₃·sr + (γ₄−1)/4·sr²) )

       where γ₃ is skewness and γ₄ raw (non-excess) kurtosis.
    2. **Deflation** — the benchmark ``sr*`` is not ``sr_benchmark`` alone
       but the expected maximum Sharpe of ``n_trials`` skill-less trials
       (see ``_expected_max_sharpe``), with the trial-Sharpe dispersion
       approximated by the SR estimator's own standard error. More searching
       raises the bar; a Sharpe found on the 100th attempt must clear a much
       higher hurdle than one found on the first.

    ``returns`` are per-period (per-bar or per-trade) simple returns; the
    resulting ``sr``/``dsr`` are in the same per-period units. Returns
    ``{sr, dsr, n, n_trials, skew, kurt}``; the statistical fields are None
    when the series is degenerate (fewer than 10 observations or zero
    variance) — never raises.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = int(r.size)
    n_trials = max(1, int(n_trials))
    out: dict = {
        "sr": None, "dsr": None, "n": n, "n_trials": n_trials,
        "skew": None, "kurt": None,
    }
    if n < _MIN_OBS:
        return out
    std = float(r.std())  # population std, matching the moment definitions
    if std <= 0 or not np.isfinite(std):
        return out
    z = (r - r.mean()) / std
    sr = float(r.mean() / std)
    skew = float(np.mean(z**3))
    kurt = float(np.mean(z**4))  # raw kurtosis: 3.0 for a normal
    # PSR denominator — the variance of the SR estimator under non-normal
    # returns; also reused as the trial-SR dispersion for the deflation term.
    var_term = max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr**2)
    sr_std = float(np.sqrt(var_term / (n - 1)))
    hurdle = sr_benchmark + _expected_max_sharpe(sr_std, n_trials)
    dsr = float(_NORMAL.cdf((sr - hurdle) / sr_std))
    out.update({"sr": sr, "dsr": dsr, "skew": skew, "kurt": kurt})
    return out


def probability_of_backtest_overfitting(
    returns_matrix, n_splits: int = 16
) -> float | None:
    """Probability of Backtest Overfitting via CSCV (Bailey, Borwein,
    López de Prado & Zhu, 2015).

    ``returns_matrix`` is ``(n_periods, n_configs)``: one column per tried
    configuration, one row per period, all aligned. The periods are cut into
    ``n_splits`` contiguous blocks; for every way of choosing half the blocks
    as in-sample (IS), the config with the best IS Sharpe is located and its
    rank among all configs is measured out-of-sample (OOS). PBO is the
    fraction of block-combinations where that IS winner ranks in the bottom
    half OOS — i.e. the probability that selecting on the backtest picks a
    config with no out-of-sample edge.

    The number of combinations C(S, S/2) explodes with ``n_splits``, so at
    most 500 combinations are evaluated (uniform sample, fixed seed —
    deterministic across calls). Returns None on degenerate input (< 2
    configs, < 10 periods, or < 4 usable blocks) — never raises.
    """
    m = np.asarray(returns_matrix, dtype=float)
    if m.ndim != 2:
        return None
    n_periods, n_configs = m.shape
    if n_configs < 2 or n_periods < _MIN_OBS:
        return None
    s = min(int(n_splits), n_periods)
    s -= s % 2  # CSCV needs an even block count
    if s < 4:
        return None
    blocks = np.array_split(np.arange(n_periods), s)

    def _sharpes(period_idx: np.ndarray) -> np.ndarray:
        sub = m[period_idx]  # (periods, configs)
        mean = sub.mean(axis=0)
        std = sub.std(axis=0)
        return np.where(std > 0, mean / np.where(std > 0, std, 1.0), 0.0)

    all_combos = comb(s, s // 2)
    if all_combos <= _MAX_CSCV_COMBINATIONS:
        combos = list(combinations(range(s), s // 2))
    else:
        rng = np.random.default_rng(0)  # fixed seed: deterministic PBO
        combos = [
            tuple(rng.choice(s, size=s // 2, replace=False))
            for _ in range(_MAX_CSCV_COMBINATIONS)
        ]
    overfit = 0
    for is_blocks in combos:
        is_set = set(is_blocks)
        is_idx = np.concatenate([blocks[b] for b in sorted(is_set)])
        oos_idx = np.concatenate(
            [blocks[b] for b in range(s) if b not in is_set])
        best = int(np.argmax(_sharpes(is_idx)))
        oos = _sharpes(oos_idx)
        # relative OOS rank of the IS winner: 1.0 = best OOS, 0.0 = worst
        rank = float(np.sum(oos < oos[best])) / (n_configs - 1)
        if rank <= 0.5:  # IS winner at/below the OOS median → overfit draw
            overfit += 1
    return overfit / len(combos)


__all__ = [
    "deflated_sharpe_ratio",
    "probability_of_backtest_overfitting",
    "purged_kfold_splits",
]
