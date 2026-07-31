"""P3-04: adaptive conformal vol intervals and the uncertainty risk gate.

Covers the analytics layer (HAR forecast, ACI interval, end-to-end gate
inputs — including the ~nominal empirical coverage acceptance criterion),
the pure gate function, and the pipeline wiring (risk_gate node blocking /
fail-open, sizing-node scale application).
"""

from datetime import timedelta

import numpy as np
import pytest

from tests.pro_fakes import BASE_TS, make_bars
from tradingagents.contracts import (
    AssetClass,
    MetricReading,
    OHLCVBar,
    ProConfig,
    RiskLimits,
    Timeframe,
)
from tradingagents.pro.analytics.conformal import (
    _har_design,
    _ols,
    adaptive_conformal_interval,
    conformal_vol_gate_inputs,
    har_forecast,
)
from tradingagents.pro.pipeline import conformal_vol_gate
from tradingagents.pro.pipeline.nodes import PipelineNodes, _apply_vol_interval_scale


def ar_vol_series(n: int, seed: int = 7) -> np.ndarray:
    """Synthetic AR(1) log-vol process — persistent, regime-y, like real RV.

    phi=0.8 keeps enough persistence for HAR to have an edge while the
    train/calibration/holdout segments stay near-exchangeable (the split-
    conformal assumption); at phi≈0.95 whole segments live in different
    vol regimes and any static calibration set honestly under-covers.
    """
    rng = np.random.default_rng(seed)
    x = np.empty(n)
    x[0] = 0.0
    for t in range(1, n):
        x[t] = 0.8 * x[t - 1] + 0.2 * rng.standard_normal()
    return 0.02 * np.exp(x)


def make_vol_bars(n: int = 200, seed: int = 3) -> list[OHLCVBar]:
    """Bars whose high-low range carries a stochastic per-bar volatility."""
    rng = np.random.default_rng(seed)
    vols = ar_vol_series(n, seed=seed)
    bars, price = [], 100.0
    for i in range(n):
        close = price * float(np.exp(vols[i] * rng.standard_normal()))
        high = max(price, close) * float(np.exp(vols[i] / 2))
        low = min(price, close) * float(np.exp(-vols[i] / 2))
        bars.append(OHLCVBar(
            timeframe=Timeframe.D1, start=BASE_TS + timedelta(days=i),
            open=price, high=high, low=low, close=close, volume=1_000.0,
        ))
        price = close
    return bars


class TestHarForecast:
    def test_too_short_history_returns_none(self):
        assert har_forecast([0.02] * 29) is None

    def test_nan_input_returns_none(self):
        rv = [0.02] * 60
        rv[30] = float("nan")
        assert har_forecast(rv) is None

    def test_empty_returns_none(self):
        assert har_forecast([]) is None

    def test_forecast_tracks_a_persistent_level(self):
        # constant vol: the one-step forecast must reproduce the level
        assert har_forecast([0.02] * 60) == pytest.approx(0.02, rel=1e-6)

    def test_forecast_is_non_negative(self):
        rv = ar_vol_series(300)
        forecast = har_forecast(rv)
        assert forecast is not None
        assert forecast >= 0.0

    def test_forecast_beats_naive_mean_on_ar_vol(self):
        # HAR must exploit persistence: lower one-step MAE than the
        # unconditional mean over 100 rolling forecasts (seeded)
        rv = ar_vol_series(500)
        har_err, mean_err = [], []
        for t in range(400, 500):
            har_err.append(abs(har_forecast(rv[:t]) - rv[t]))
            mean_err.append(abs(float(rv[:t].mean()) - rv[t]))
        assert float(np.mean(har_err)) < float(np.mean(mean_err))


class TestAdaptiveConformalInterval:
    def test_too_few_residuals_returns_none(self):
        assert adaptive_conformal_interval([0.1] * 7) is None

    def test_bad_alpha_or_gamma_returns_none(self):
        assert adaptive_conformal_interval([0.1] * 50, alpha=0.0) is None
        assert adaptive_conformal_interval([0.1] * 50, alpha=1.0) is None
        assert adaptive_conformal_interval([0.1] * 50, gamma=-0.1) is None

    def test_interval_shape_and_center(self):
        rng = np.random.default_rng(0)
        out = adaptive_conformal_interval(rng.standard_normal(500), center=5.0)
        assert out["upper"] > 5.0 > out["lower"]
        assert out["width"] == pytest.approx(out["upper"] - out["lower"])
        assert out["upper"] - 5.0 == pytest.approx(5.0 - out["lower"])
        assert 0.0 < out["effective_alpha"] < 1.0

    def test_empirical_coverage_is_near_nominal(self):
        # AC (P3-04): 90% nominal coverage within +/-5pp on a seeded holdout
        rng = np.random.default_rng(42)
        calibration = rng.standard_normal(600)
        holdout = np.abs(rng.standard_normal(4000))
        out = adaptive_conformal_interval(calibration, alpha=0.1)
        half_width = out["width"] / 2.0
        coverage = float(np.mean(holdout <= half_width))
        assert 0.85 <= coverage <= 0.95

    def test_har_conformal_coverage_on_ar_vol(self):
        # end-to-end AC on the actual pipeline math: HAR fit on train,
        # ACI calibrated on the next fold, coverage measured on a later
        # untouched holdout of the same AR-vol process
        rv = ar_vol_series(2500, seed=0)
        X, y = _har_design(rv)
        n = y.size
        train = slice(0, int(n * 0.5))
        cal = slice(int(n * 0.5), int(n * 0.7))
        hold = slice(int(n * 0.7), n)
        beta = _ols(X[train], y[train])
        out = adaptive_conformal_interval(y[cal] - X[cal] @ beta, alpha=0.1)
        half_width = out["width"] / 2.0
        coverage = float(np.mean(np.abs(y[hold] - X[hold] @ beta) <= half_width))
        assert 0.85 <= coverage <= 0.95


class TestConformalVolGateInputs:
    def test_full_history_yields_all_fields(self):
        out = conformal_vol_gate_inputs(make_vol_bars(200))
        assert out["forecast"] is not None and out["forecast"] > 0
        assert out["lower"] is not None and out["lower"] >= 0.0
        assert out["upper"] > out["lower"]
        assert out["width"] == pytest.approx(out["upper"] - out["lower"], rel=0.5)
        assert 0.0 < out["effective_alpha"] < 1.0
        assert out["n_calibration"] >= 8

    def test_short_history_returns_none_fields_without_raising(self):
        for n in (0, 5, 40):
            out = conformal_vol_gate_inputs(make_vol_bars(max(n, 1))[:n])
            assert out["width"] is None
            assert out["forecast"] is None
            assert out["n_calibration"] == 0

    def test_garbage_input_never_raises(self):
        assert conformal_vol_gate_inputs([object()] * 100)["width"] is None
        assert conformal_vol_gate_inputs(None)["width"] is None


class TestConformalVolGate:
    def limits(self, **kw) -> RiskLimits:
        return RiskLimits(**kw)

    def test_disabled_by_default_is_a_no_op(self):
        result, scale = conformal_vol_gate(
            {"width": 10.0}, self.limits())  # absurd width, no cap set
        assert result.passed
        assert result.checks == {}
        assert scale == 1.0

    def test_blocks_when_width_exceeds_cap(self):
        # width 0.042 of price = 4.2% vs a 3.0% cap
        result, scale = conformal_vol_gate(
            {"width": 0.042}, self.limits(max_vol_interval_width_pct=3.0))
        assert not result.passed
        assert result.checks["vol_interval_within_cap"] is False
        assert result.reasons == (
            "vol interval 4.2% > 3.0% cap — uncertainty too wide",)
        assert scale == 1.0

    def test_passes_when_width_under_cap(self):
        result, scale = conformal_vol_gate(
            {"width": 0.02}, self.limits(max_vol_interval_width_pct=3.0))
        assert result.passed
        assert result.checks["vol_interval_within_cap"] is True
        assert scale == 1.0

    def test_fails_open_on_missing_inputs_with_disclosure(self):
        for inputs in (None, {"width": None}):
            result, scale = conformal_vol_gate(
                inputs, self.limits(max_vol_interval_width_pct=3.0))
            assert result.passed
            assert result.checks == {"vol_interval_available": False}
            assert "passes open" in result.reasons[0]
            assert scale == 1.0

    def test_size_scale_replaces_the_block(self):
        limits = self.limits(max_vol_interval_width_pct=3.0,
                             vol_interval_size_scale=True)
        result, scale = conformal_vol_gate({"width": 0.042}, limits)
        assert result.passed
        assert result.checks["vol_interval_size_scaled"] is True
        assert scale == pytest.approx(3.0 / 4.2)
        assert "position scaled to 71%" in result.reasons[0]

    def test_size_scale_floors_at_a_quarter(self):
        limits = self.limits(max_vol_interval_width_pct=1.0,
                             vol_interval_size_scale=True)
        _, scale = conformal_vol_gate({"width": 0.50}, limits)  # 50x the cap
        assert scale == 0.25

    def test_size_scale_flag_alone_never_scales_under_cap(self):
        limits = self.limits(max_vol_interval_width_pct=3.0,
                             vol_interval_size_scale=True)
        result, scale = conformal_vol_gate({"width": 0.01}, limits)
        assert result.passed
        assert scale == 1.0


class TestSizingScaleApplication:
    def reading(self, name, value):
        return MetricReading(name=name, value=value, source="risk_engine")

    def sided(self):
        return {
            "POSITION_SIZE_UNITS": self.reading("POSITION_SIZE_UNITS", 10.0),
            "POSITION_NOTIONAL": self.reading("POSITION_NOTIONAL", 1000.0),
            "POSITION_PCT_EQUITY": self.reading("POSITION_PCT_EQUITY", 1.0),
            "ENTRY_REF_PRICE": self.reading("ENTRY_REF_PRICE", 100.0),
        }

    def test_scales_only_the_sizing_metrics(self):
        out = _apply_vol_interval_scale(self.sided(), 0.5)
        assert out["POSITION_SIZE_UNITS"].value == pytest.approx(5.0)
        assert out["POSITION_NOTIONAL"].value == pytest.approx(500.0)
        assert out["POSITION_PCT_EQUITY"].value == pytest.approx(0.5)
        assert out["ENTRY_REF_PRICE"].value == 100.0  # levels untouched

    def test_scale_of_one_is_identity(self):
        sided = self.sided()
        assert _apply_vol_interval_scale(sided, 1.0) is sided


class TestRiskGateNodeWiring:
    def make_nodes(self, **risk_kw) -> PipelineNodes:
        config = ProConfig(asset=AssetClass.GOLD, risk=RiskLimits(**risk_kw))
        return PipelineNodes(llm=None, config=config, equity=100_000.0)

    def state(self, bars):
        from tests.test_pro_agents_base import make_snapshot
        return {
            "snapshot": make_snapshot(bars=bars),
            "risk_metrics": {"VAR_95": MetricReading(
                name="VAR_95", value=0.01, source="risk_engine")},
            "run_timeframe": Timeframe.D1,
            "gate_results": {},
        }

    def test_disabled_by_default_records_nothing(self):
        update = self.make_nodes().risk_gate(self.state(make_vol_bars(200)))
        assert "conformal_vol" not in update["gate_results"]
        assert "vol_interval_scale" not in update
        assert "rejection" not in update

    def test_blocks_entries_when_interval_too_wide(self):
        nodes = self.make_nodes(max_vol_interval_width_pct=0.05)  # 0.05% cap
        update = nodes.risk_gate(self.state(make_vol_bars(200)))
        gate = update["gate_results"]["conformal_vol"]
        assert gate["passed"] is False
        assert update["rejection"]["stage"] == "risk_gate"
        assert "uncertainty too wide" in update["rejection"]["reasons"][0]

    def test_passes_under_a_generous_cap(self):
        nodes = self.make_nodes(max_vol_interval_width_pct=90.0)
        update = nodes.risk_gate(self.state(make_vol_bars(200)))
        assert update["gate_results"]["conformal_vol"]["passed"] is True
        assert update["vol_interval_scale"] == 1.0
        assert "rejection" not in update

    def test_size_scale_attaches_factor_instead_of_blocking(self):
        nodes = self.make_nodes(max_vol_interval_width_pct=0.05,
                                vol_interval_size_scale=True)
        update = nodes.risk_gate(self.state(make_vol_bars(200)))
        assert update["gate_results"]["conformal_vol"]["passed"] is True
        assert 0.25 <= update["vol_interval_scale"] < 1.0
        assert "rejection" not in update

    def test_short_history_fails_open_with_disclosure(self):
        nodes = self.make_nodes(max_vol_interval_width_pct=0.05)
        update = nodes.risk_gate(self.state(make_bars(n=40)))
        gate = update["gate_results"]["conformal_vol"]
        assert gate["passed"] is True
        assert gate["checks"] == {"vol_interval_available": False}
        assert "rejection" not in update
