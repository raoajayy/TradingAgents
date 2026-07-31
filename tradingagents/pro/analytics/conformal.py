"""Adaptive conformal prediction intervals on realized-vol forecasts (P3-04).

Pure numpy. The risk gate consumes the interval WIDTH as an uncertainty
measure: when the conformal band around the one-step-ahead volatility
forecast blows out, the model is telling us it does not know what
tomorrow's vol will be — new entries are blocked (or sized down) rather
than priced off a point forecast that carries no such health warning.

Three layers:

- ``har_forecast`` — the HAR-RV baseline (Corsi, 2009): tomorrow's realized
  vol regressed on today's vol and its weekly/monthly averages. Simple OLS,
  famously hard to beat one step ahead.
- ``adaptive_conformal_interval`` — ACI (Gibbs & Candes, 2021): a
  distribution-free interval from calibration residuals whose miscoverage
  level adapts online, so coverage tracks the nominal ``1 - alpha`` even
  when the residual distribution drifts.
- ``conformal_vol_gate_inputs`` — end-to-end: bars -> per-bar realized-vol
  series -> HAR fit on a purged train split (the P2-02 splitter, so the
  calibration fold shares no adjacent bars with training) -> calibration
  residuals -> current forecast plus interval.

References (methods paraphrased, not reproduced verbatim):
- Corsi, "A Simple Approximate Long-Memory Model of Realized Volatility"
  (2009): the HAR-RV daily/weekly/monthly cascade regression.
- Gibbs & Candes, "Adaptive Conformal Inference Under Distribution Shift"
  (NeurIPS 2021): the online update alpha_{t+1} = alpha_t +
  gamma * (alpha - err_t) that keeps empirical coverage near nominal.
- Parkinson, "The Extreme Value Method for Estimating the Variance of the
  Rate of Return" (1980): the high-low range vol estimator.

All functions handle degenerate inputs (too few observations, NaN) by
returning None (or None fields) — they never raise on bad data.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from tradingagents.pro.analytics.validation import purged_kfold_splits

_MIN_HAR_OBS = 30       # below this a 4-parameter OLS is noise
_WEEK_LAGS = 5          # HAR weekly component: mean of RV_{t-4..t}
_MONTH_LAGS = 22        # HAR monthly component: mean of RV_{t-21..t}
_MIN_CALIBRATION = 8    # fewer calibration residuals -> no honest quantile
_PARKINSON_FACTOR = 1.0 / (2.0 * math.sqrt(math.log(2.0)))


def _rolling_mean(x: np.ndarray, window: int) -> np.ndarray:
    """Trailing mean of the last ``window`` values, aligned to each index
    (valid from index ``window - 1`` onward)."""
    c = np.concatenate(([0.0], np.cumsum(x)))
    return (c[window:] - c[:-window]) / window


def _har_design(rv: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """HAR-RV design matrix and targets.

    Row ``i`` holds the features known at time ``t = _MONTH_LAGS - 1 + i``
    — ``[1, RV_t, mean(RV_{t-4..t}), mean(RV_{t-21..t})]`` — and the target
    ``y_i = RV_{t+1}``. Returns None on degenerate input (< 30 obs or any
    non-finite value); never raises.
    """
    rv = np.asarray(rv, dtype=float)
    if rv.ndim != 1 or rv.size < _MIN_HAR_OBS or not np.all(np.isfinite(rv)):
        return None
    # features at t = 21 .. n-2 predict rv[t+1]
    daily = rv[_MONTH_LAGS - 1 : -1]
    weekly = _rolling_mean(rv, _WEEK_LAGS)[_MONTH_LAGS - _WEEK_LAGS : -1]
    monthly = _rolling_mean(rv, _MONTH_LAGS)[:-1]
    y = rv[_MONTH_LAGS:]
    X = np.column_stack([np.ones_like(daily), daily, weekly, monthly])
    if y.size < 2:
        return None
    return X, y


def _har_features_now(rv: np.ndarray) -> np.ndarray:
    """Feature row at the LAST observation — inputs to the one-step forecast."""
    return np.array([
        1.0,
        rv[-1],
        float(rv[-_WEEK_LAGS:].mean()),
        float(rv[-_MONTH_LAGS:].mean()),
    ])


def _ols(X: np.ndarray, y: np.ndarray) -> np.ndarray | None:
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta if np.all(np.isfinite(beta)) else None


def har_forecast(daily_realized_vol: Sequence[float]) -> float | None:
    """One-step-ahead HAR-RV forecast (Corsi, 2009).

    Fits ``RV_{t+1} ~ c + b_d*RV_t + b_w*mean(RV_{t-4..t}) +
    b_m*mean(RV_{t-21..t})`` by OLS over the full history and forecasts the
    next value from the latest daily/weekly/monthly features. The cascade
    of horizons is Corsi's heterogeneous-market shorthand for long memory
    in volatility; despite three regressors it remains the standard
    one-step benchmark that fancier models are measured against.

    Volatility is non-negative, so the OLS forecast is floored at 0.
    Degenerate input (< 30 observations, or any NaN/inf) returns None —
    never raises.
    """
    rv = np.asarray(daily_realized_vol, dtype=float)
    design = _har_design(rv)
    if design is None:
        return None
    beta = _ols(*design)
    if beta is None:
        return None
    return float(max(0.0, _har_features_now(rv) @ beta))


def adaptive_conformal_interval(
    residuals: Sequence[float],
    alpha: float = 0.1,
    gamma: float = 0.01,
    center: float = 0.0,
) -> dict | None:
    """Adaptive conformal interval from calibration residuals (ACI —
    Gibbs & Candes, 2021).

    Split-conformal base: the interval half-width is the empirical
    ``1 - alpha`` quantile of the ABSOLUTE calibration residuals (with the
    conservative ``(n+1)/n`` finite-sample correction), which is
    distribution-free — no assumption on the residual law. The adaptive
    part processes the residuals in temporal order and runs the online
    update ``alpha_{t+1} = alpha_t + gamma * (alpha - err_t)`` where
    ``err_t`` is 1 when residual t escaped the interval implied by
    ``alpha_t`` so far: sustained under-coverage (a vol regime the model
    misses) drives ``alpha_t`` down and widens the band, over-coverage
    tightens it. Honest caveat: with a single static calibration set this
    buys drift-awareness WITHIN the calibration window only; the guarantee
    is approximate, not the full online-ACI coverage theorem.

    Returns ``{"lower", "upper", "width", "effective_alpha"}`` with the
    interval centered on ``center`` (pass the point forecast). Degenerate
    input (< 8 finite residuals, or gamma/alpha out of range) returns
    None — never raises.
    """
    r = np.abs(np.asarray(residuals, dtype=float))
    r = r[np.isfinite(r)]
    if r.size < _MIN_CALIBRATION or not (0.0 < alpha < 1.0) or gamma < 0.0:
        return None

    def _quantile(scores: np.ndarray, a: float) -> float:
        # conservative split-conformal quantile: ceil((n+1)(1-a))/n level
        level = min(1.0, (1.0 - a) * (scores.size + 1) / scores.size)
        return float(np.quantile(scores, level, method="higher"))

    alpha_t = alpha
    for t in range(1, r.size):
        q_t = _quantile(r[:t], alpha_t)
        err = 1.0 if r[t] > q_t else 0.0
        alpha_t = float(np.clip(alpha_t + gamma * (alpha - err), 1e-3, 1 - 1e-3))

    q = _quantile(r, alpha_t)
    return {
        "lower": center - q,
        "upper": center + q,
        "width": 2.0 * q,
        "effective_alpha": alpha_t,
    }


def conformal_vol_gate_inputs(
    bars: Sequence,
    alpha: float = 0.1,
    gamma: float = 0.01,
) -> dict:
    """End-to-end conformal vol-forecast inputs for the risk gate.

    Pipeline:

    1. **Realized-vol series** — per-bar Parkinson (1980) estimator,
       ``ln(high/low) / (2 * sqrt(ln 2))``: chosen over close-to-close
       because the high-low range yields a usable vol reading from every
       single bar (~5x the efficiency of squared returns), so no rolling
       window has to smear regimes together — HAR's daily/weekly/monthly
       cascade does the smoothing instead. Units: per-bar return fraction
       (0.02 = 2% of price).
    2. **HAR fit on a purged train split** — the P2-02
       ``purged_kfold_splits`` (Lopez de Prado's purged K-fold) provides
       the final fold as calibration: training rows stop one sample short
       of the calibration block, so the overlapping HAR feature windows
       cannot leak the calibration targets into the fit.
    3. **Calibration residuals** on that held-out fold, in temporal order,
       feed ``adaptive_conformal_interval``.
    4. **Current forecast** — HAR features at the latest bar; the interval
       is centered on it, lower edge floored at 0 (vol is non-negative).

    Returns ``{"forecast", "lower", "upper", "width", "effective_alpha",
    "n_calibration"}``; every field is None (``n_calibration`` 0) on
    insufficient/degenerate history — never raises.
    """
    empty = {"forecast": None, "lower": None, "upper": None, "width": None,
             "effective_alpha": None, "n_calibration": 0}
    try:
        rv = np.array([
            math.log(b.high / b.low) * _PARKINSON_FACTOR if b.high > b.low else 0.0
            for b in bars
        ], dtype=float)
        design = _har_design(rv)
        if design is None:
            return empty
        X, y = design
        splits = purged_kfold_splits(y.size, k=5, embargo_frac=0.01)
        if not splits:
            return empty
        train_idx, cal_idx = splits[-1]  # last fold: calibrate on the most
        train_idx = train_idx[train_idx < cal_idx[0]]  # recent data, train
        if train_idx.size < _MIN_HAR_OBS or cal_idx.size < _MIN_CALIBRATION:
            return empty                               # strictly on the past
        beta = _ols(X[train_idx], y[train_idx])
        if beta is None:
            return empty
        residuals = y[cal_idx] - X[cal_idx] @ beta
        forecast = float(max(0.0, _har_features_now(rv) @ beta))
        interval = adaptive_conformal_interval(
            residuals, alpha=alpha, gamma=gamma, center=forecast)
        if interval is None:
            return empty
        return {
            "forecast": forecast,
            "lower": max(0.0, interval["lower"]),
            "upper": interval["upper"],
            "width": interval["width"],
            "effective_alpha": interval["effective_alpha"],
            "n_calibration": int(cal_idx.size),
        }
    except Exception:  # noqa: BLE001 — gate inputs must never raise
        return empty


__all__ = [
    "adaptive_conformal_interval",
    "conformal_vol_gate_inputs",
    "har_forecast",
]
