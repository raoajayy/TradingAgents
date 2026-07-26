"""Offline tests for the parameter-sensitivity producer (scripts/pro_sensitivity.py)
and the charts.param_sensitivity_heatmap renderer (SO-B).

Deterministic + offline: they prove the holdout-Sharpe math, the verdict logic,
the a-priori-range policy on the swept axes, and that the heatmap renders a PNG
without raising. No vendor, no cache."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "pro_sensitivity",
    Path(__file__).resolve().parent.parent / "scripts" / "pro_sensitivity.py")
sens = importlib.util.module_from_spec(_SPEC)
sys.modules["pro_sensitivity"] = sens
_SPEC.loader.exec_module(sens)


class TestAxesRespectDeclaredDomains:
    def test_every_swept_value_is_in_the_registered_domain(self):
        # raises AssertionError if any axis value falls outside the ParamSpace
        sens._assert_axes_in_domain()

    def test_top_cells_reference_real_axes(self):
        for sid, *_ in sens.TOP_CELLS:
            assert sid in sens.AXES


class TestHoldoutSharpe:
    def test_none_when_tail_too_short(self):
        assert sens._holdout_sharpe([0.01] * 5, 252, 0.3) is None

    def test_none_when_no_dispersion(self):
        # a perfectly flat tail has no Sharpe (zero std) → not comparable
        assert sens._holdout_sharpe([0.0] * 100, 252, 0.3) is None

    def test_positive_drift_gives_positive_sharpe(self):
        rets = [0.001 + (0.0005 if i % 2 else -0.0004) for i in range(200)]
        hs = sens._holdout_sharpe(rets, 252, 0.3)
        assert hs is not None and hs > 0

    def test_only_scores_the_tail(self):
        # huge negative head, positive-drift tail (with variance) → holdout > 0
        rets = [-0.05] * 140 + [0.003 if i % 2 else 0.001 for i in range(60)]
        hs = sens._holdout_sharpe(rets, 252, 0.3)
        assert hs is not None and hs > 0


class TestVerdict:
    def test_plateau_when_mostly_positive_and_boxed_positive(self):
        grid = [[0.5, 0.4], [0.3, 0.6]]
        tag, _ = sens._verdict(grid, star=(1, 1))
        assert tag == "plateau"

    def test_fragile_when_boxed_positive_but_field_negative(self):
        grid = [[-0.5, -0.4], [-0.3, 0.6]]
        tag, _ = sens._verdict(grid, star=(1, 1))
        assert tag == "fragile"

    def test_no_data_when_all_blank(self):
        tag, _ = sens._verdict([[None, None], [None, None]], star=None)
        assert tag == "no-data"

    def test_closest_index_picks_nearest(self):
        assert sens._closest_index([10, 20, 35, 50], 22) == 1


class TestHeatmapRenderer:
    def test_writes_a_png(self, tmp_path):
        charts = pytest.importorskip("tradingagents.pro.backtest.charts")
        out = tmp_path / "hm.png"
        charts.param_sensitivity_heatmap(
            out, "x", [1, 2, 3], "y", [10, 20],
            [[0.1, 0.2, 0.3], [0.4, None, 0.6]],
            objective="Sharpe", star=(0, 1))
        assert out.exists() and out.stat().st_size > 0

    def test_empty_axes_write_placeholder(self, tmp_path):
        charts = pytest.importorskip("tradingagents.pro.backtest.charts")
        out = tmp_path / "empty.png"
        charts.param_sensitivity_heatmap(out, "x", [], "y", [], [])
        assert out.exists()
