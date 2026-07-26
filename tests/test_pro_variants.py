"""SO-D — the four named variants (variants.py) resolve to guard-passing presets
only, and form a coherent risk ladder. Offline + deterministic (no engine)."""

from __future__ import annotations

from tradingagents.pro.backtest import presets, variants


class TestVariantsComposeFromShippedPresets:
    def test_validate_variants_passes(self):
        variants.validate_variants()  # raises if any component isn't a preset

    def test_every_component_is_a_guard_passing_preset(self):
        for v in variants.list_variants():
            for (sid, sym, tf) in v.components:
                assert presets.CATALOG.get(sid, {}).get((sym, tf)) is not None

    def test_all_four_variants_present(self):
        assert set(variants.VARIANTS) == {"A", "B", "C", "D"}


class TestRiskLadder:
    def test_vol_target_increases_A_to_C(self):
        vt = variants.VARIANTS
        assert vt["A"].vol_target < vt["B"].vol_target < vt["C"].vol_target

    def test_conservative_is_daily_only(self):
        assert all(tf == "1d" for (_, _, tf) in variants.VARIANTS["A"].components)

    def test_aggressive_allows_higher_leverage(self):
        assert variants.VARIANTS["C"].max_leverage >= variants.VARIANTS["A"].max_leverage

    def test_regime_variant_is_built_on_regime_momentum(self):
        sids = {sid for (sid, _, _) in variants.VARIANTS["D"].components}
        assert "regime_momentum_v1" in sids


class TestValidationCatchesBadComponents:
    def test_unknown_preset_component_raises(self, monkeypatch):
        bad = variants.Variant(
            name="X", title="Bad", risk="test", weight_scheme="equal",
            vol_target=0.1, components=(("trend_following_v1", "DOGE-USD", "1d"),))
        monkeypatch.setitem(variants.VARIANTS, "X", bad)
        try:
            import pytest
            with pytest.raises(AssertionError):
                variants.validate_variants()
        finally:
            variants.VARIANTS.pop("X", None)

    def test_bad_weight_scheme_raises(self, monkeypatch):
        import pytest
        bad = variants.Variant(
            name="Y", title="Bad", risk="test", weight_scheme="magic",
            vol_target=0.1, components=(("trend_following_v1", "ETH-USD", "1d"),))
        monkeypatch.setitem(variants.VARIANTS, "Y", bad)
        try:
            with pytest.raises(AssertionError):
                variants.validate_variants()
        finally:
            variants.VARIANTS.pop("Y", None)
