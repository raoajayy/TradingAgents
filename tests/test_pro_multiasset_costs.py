"""Multi-asset cost foundation (track T4): equities + FX asset classes, FX
zero-volume liquidity fix; existing crypto/gold profiles byte-identical."""

from tradingagents.contracts import AssetClass
from tradingagents.pro.backtest.costs import LiquidityModel, cost_profile_for


def test_new_asset_classes_exist():
    assert AssetClass.EQUITY.value == "EQ"
    assert AssetClass.FX.value == "FX"


def test_fx_liquidity_is_volume_agnostic():
    _slip, _comm, liq = cost_profile_for(AssetClass.FX)
    assert liq.max_participation is None
    # the load-bearing fix: a zero-volume FX bar no longer rejects the fill
    assert liq.cap_quantity(10.0, 0.0) == 10.0


def test_equity_profile_keeps_a_volume_cap():
    _slip, _comm, liq = cost_profile_for(AssetClass.EQUITY)
    assert liq.max_participation == 0.1
    assert liq.cap_quantity(10.0, 50.0) == 5.0  # 0.1 × 50


def test_existing_assets_unchanged():
    # gold + crypto keep the exact volume-based liquidity model (equivalence)
    for a in (AssetClass.GOLD, AssetClass.BITCOIN, AssetClass.ETHEREUM,
              AssetClass.SOLANA):
        _slip, _comm, liq = cost_profile_for(a)
        assert liq.max_participation == 0.1


def test_liquidity_none_returns_desired():
    assert LiquidityModel(max_participation=None).cap_quantity(7.0, 0.0) == 7.0
    # default still caps by volume, byte-identical to before
    assert LiquidityModel().cap_quantity(7.0, 20.0) == 2.0
