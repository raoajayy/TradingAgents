"""Tests for the additive tuned-preset catalog (backtest/presets.py)."""

from __future__ import annotations

import pytest

from tradingagents.pro.backtest import presets
from tradingagents.pro.backtest.presets import (
    CATALOG,
    Preset,
    build_preset_strategy,
    list_presets,
    preset_params,
)
from tradingagents.pro.backtest.registry import strategy_param_space


class TestLoader:
    def test_missing_cell_returns_none(self):
        assert preset_params("trend_following_v1", "NOPE-USD", "1h") is None
        assert preset_params("does_not_exist", "BTC-USD", "1h") is None

    def test_build_falls_back_to_defaults_when_no_preset(self):
        # no preset for this fabricated cell → pure a-priori defaults
        strat = build_preset_strategy("trend_following_v1", "NOPE-USD", "1h")
        assert strat.params["donchian_period"] == 20  # shipped default
        assert strat.id == "trend_following_v1"

    def test_extra_overrides_win_over_defaults(self):
        strat = build_preset_strategy("trend_following_v1", "NOPE-USD", "1h",
                                      extra_overrides={"donchian_period": 40})
        assert strat.params["donchian_period"] == 40

    def test_list_presets_is_serializable(self):
        rows = list_presets()
        assert isinstance(rows, list)
        for r in rows:
            assert {"strategy_id", "symbol", "timeframe", "params",
                    "oos_sharpe", "deflated_sharpe", "pbo"} <= set(r)


class TestCatalogIntegrity:
    def test_every_registered_preset_is_in_domain_and_applies(self):
        """Whatever is in the catalog (possibly empty) must be valid: params in
        the declared domain, and build_preset_strategy must apply them."""
        for sid, cells in CATALOG.items():
            space = strategy_param_space(sid)
            for (symbol, tf), preset in cells.items():
                assert isinstance(preset, Preset)
                assert preset.deflated_sharpe >= 0.6 and preset.pbo <= 0.5, (
                    f"{sid}@{symbol}/{tf} does not clear the guard bar")
                assert preset.oos_sharpe > 0
                for name, value in preset.params.items():
                    assert name in space
                    assert space._by_name[name].contains(value)
                strat = build_preset_strategy(sid, symbol, tf)
                for name, value in preset.params.items():
                    assert strat.params[name] == value

    def test_validate_rejects_out_of_domain_preset(self, monkeypatch):
        """The import-time guard must reject a value outside the declared domain."""
        bad = {"trend_following_v1": {
            ("BTC-USD", "1h"): Preset(
                params={"donchian_period": 999},  # domain is 10–100
                oos_sharpe=1.0, deflated_sharpe=1.0, pbo=0.0, n_trials=10)}}
        monkeypatch.setattr(presets, "CATALOG", bad)
        with pytest.raises(ValueError, match="outside declared domain"):
            presets._validate_catalog()
