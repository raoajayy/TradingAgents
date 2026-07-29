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


# --- P2-10 FX majors: the enum ripple, asserted end to end ------------------


def test_fx_default_symbol_and_symbol_map():
    from tradingagents.contracts import ASSET_BY_SYMBOL, DEFAULT_SYMBOLS, FX_SYMBOLS

    assert DEFAULT_SYMBOLS[AssetClass.FX] == "EURUSD"
    assert FX_SYMBOLS == ("EURUSD", "USDJPY")
    # ASSET_BY_SYMBOL covers the non-default pair the 1:1 inversion misses
    assert ASSET_BY_SYMBOL["EURUSD"] is AssetClass.FX
    assert ASSET_BY_SYMBOL["USDJPY"] is AssetClass.FX
    assert ASSET_BY_SYMBOL["XAUUSD"] is AssetClass.GOLD


def test_fx_config_defaults_and_pair_override():
    from tradingagents.contracts import ProConfig

    assert ProConfig(asset=AssetClass.FX).symbol == "EURUSD"
    assert ProConfig(asset=AssetClass.FX, symbol="USDJPY").symbol == "USDJPY"


def test_fx_is_a_market_closure_asset():
    # FX closes on weekends like gold: "1Y" of daily bars means ~252, and
    # annualization uses trading days, not 365
    import pytest

    pytest.importorskip("fastapi")
    from tradingagents.contracts import Timeframe
    from tradingagents.pro.dashboard import backtest_job as btjob

    assert btjob.periods_per_year(Timeframe.D1, AssetClass.FX) == 252
    assert btjob.bars_for_duration("1Y", Timeframe.D1, AssetClass.FX) \
        < btjob.bars_for_duration("1Y", Timeframe.D1, AssetClass.BITCOIN)


def test_fx_has_no_perp_funding():
    import pytest

    pytest.importorskip("fastapi")
    from tradingagents.pro.dashboard.backtest_job import _funding_for

    assert _funding_for(AssetClass.FX) is None


def test_paper_and_oanda_venues_carry_the_fx_pairs():
    from tradingagents.pro.execution.venues import VENUES

    assert VENUES["paper"].venue_symbol("EURUSD") == "EURUSD"
    assert VENUES["paper"].venue_symbol("USDJPY") == "USDJPY"
    assert VENUES["oanda"].venue_symbol("EURUSD") == "EUR_USD"
    assert VENUES["oanda"].venue_symbol("USDJPY") == "USD_JPY"
