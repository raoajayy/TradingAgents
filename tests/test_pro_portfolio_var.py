"""P2-05: portfolio-level VaR + correlation-aware pre-trade caps.

Roadmap AC: adding a correlated ETH position to an existing BTC book trips
the correlated-gross cap where an uncorrelated gold position does not; the
VaR math matches a hand computation; and with no limits set (the default)
every check passes untouched.
"""

import math
from datetime import timedelta
from statistics import NormalDist

import numpy as np
import pytest

from tests.pro_fakes import BASE_TS
from tests.test_pro_pipeline_units import make_evidence
from tradingagents.contracts import (
    AgentVote,
    AssetClass,
    Direction,
    MarketRegime,
    OHLCVBar,
    PositionSize,
    RiskLimits,
    TakeProfitLevel,
    Timeframe,
    TradeAction,
    TradeRecommendation,
    VoteBreakdown,
)
from tradingagents.pro.analytics.risk import portfolio_var, returns_covariance
from tradingagents.pro.execution.validation import (
    PortfolioRiskContext,
    validate_recommendation,
)

EQUITY = 100_000.0

# 3-symbol daily log-return covariance: BTC and ETH move together
# (rho = 0.9), gold is uncorrelated with both. Daily vol 5% each.
COV_SYMBOLS = ("BTC-USD", "ETH-USD", "XAUUSD")
_VAR = 0.0025  # (5% daily vol)^2
_COV_BTC_ETH = 0.9 * _VAR
COVARIANCE = (
    (_VAR, _COV_BTC_ETH, 0.0),
    (_COV_BTC_ETH, _VAR, 0.0),
    (0.0, 0.0, _VAR),
)


def make_rec(symbol: str, entry: float, quantity: float,
             asset: AssetClass = AssetClass.BITCOIN) -> TradeRecommendation:
    return TradeRecommendation(
        symbol=symbol,
        asset=asset,
        action=TradeAction.BUY,
        confidence=70,
        entry_price=entry,
        stop_loss=entry * 0.98,
        take_profits=[TakeProfitLevel(price=entry * 1.05, size_fraction=1.0)],
        position_size=PositionSize(quantity=quantity, notional=quantity * entry),
        market_regime=MarketRegime.TRENDING_UP,
        evidence=[make_evidence("trend", Direction.BULLISH, 70)],
        vote_breakdown=VoteBreakdown(votes=[
            AgentVote(agent_id="trend", vote=TradeAction.BUY, confidence=70),
        ]),
    )


def context(book: dict[str, float]) -> PortfolioRiskContext:
    return PortfolioRiskContext(
        open_notional_by_symbol=book,
        cov_symbols=COV_SYMBOLS,
        covariance=COVARIANCE,
    )


def bars_from_closes(closes) -> list[OHLCVBar]:
    return [
        OHLCVBar(timeframe=Timeframe.D1, start=BASE_TS + timedelta(days=i),
                 open=c, high=c * 1.01, low=c * 0.99, close=c, volume=1_000.0)
        for i, c in enumerate(closes)
    ]


# --- portfolio_var math ------------------------------------------------------


class TestPortfolioVar:
    def test_matches_hand_computed_two_asset_var(self):
        # w' C w = 0.25*0.04 + 0.25*0.09 + 2*0.25*0.02 = 0.0425
        cov = [[0.04, 0.02], [0.02, 0.09]]
        var = portfolio_var([0.5, 0.5], cov, confidence=0.99)
        expected = NormalDist().inv_cdf(0.99) * math.sqrt(0.0425)
        assert var == pytest.approx(expected)
        assert var == pytest.approx(0.479589, abs=1e-5)

    def test_horizon_scales_by_square_root_of_time(self):
        cov = [[0.04, 0.02], [0.02, 0.09]]
        one = portfolio_var([0.5, 0.5], cov, horizon_days=1)
        four = portfolio_var([0.5, 0.5], cov, horizon_days=4)
        assert four == pytest.approx(2 * one)

    def test_degenerate_inputs_return_none_never_raise(self):
        good_cov = [[0.04, 0.02], [0.02, 0.09]]
        assert portfolio_var(None, good_cov) is None
        assert portfolio_var([0.5, 0.5], None) is None
        assert portfolio_var([], []) is None
        assert portfolio_var([0.5], good_cov) is None  # shape mismatch
        assert portfolio_var([0.5, 0.5], [[0.04, float("nan")],
                                          [0.02, 0.09]]) is None
        assert portfolio_var([0.5, float("inf")], good_cov) is None
        assert portfolio_var([0.5, 0.5], good_cov, confidence=1.0) is None
        assert portfolio_var([0.5, 0.5], good_cov, horizon_days=0) is None
        assert portfolio_var(["a", "b"], good_cov) is None


# --- returns_covariance ------------------------------------------------------


class TestReturnsCovariance:
    def test_matches_numpy_covariance_of_log_returns(self):
        rng = np.random.default_rng(7)
        r_a = rng.normal(0, 0.02, 40)
        r_b = 1.5 * r_a + rng.normal(0, 0.005, 40)  # strongly correlated
        closes_a = 100.0 * np.exp(np.cumsum(r_a))
        closes_b = 2000.0 * np.exp(np.cumsum(r_b))
        result = returns_covariance({
            "A": bars_from_closes(closes_a),
            "B": bars_from_closes(closes_b),
        }, window=30)
        assert result is not None
        symbols, cov = result
        assert sorted(symbols) == ["A", "B"]
        log_a = np.diff(np.log(closes_a))[-30:]
        log_b = np.diff(np.log(closes_b))[-30:]
        expected = np.cov(np.vstack([log_a, log_b]), ddof=1)
        i, j = symbols.index("A"), symbols.index("B")
        assert cov[i][i] == pytest.approx(expected[0][0])
        assert cov[j][j] == pytest.approx(expected[1][1])
        assert cov[i][j] == pytest.approx(expected[0][1])

    def test_missing_history_returns_none(self):
        assert returns_covariance({}) is None
        assert returns_covariance(None) is None
        # 3 bars -> 2 overlapping returns, below the honesty floor of 5
        short = bars_from_closes([100.0, 101.0, 100.5])
        assert returns_covariance({"A": short}) is None


# --- pre-trade caps (roadmap AC) --------------------------------------------


class TestCorrelatedGrossCap:
    LIMITS = RiskLimits(max_correlated_gross_pct=10.0)

    def test_correlated_eth_on_btc_book_trips_the_cap(self):
        # 8% BTC already open; 4% ETH (rho 0.9) -> 12% correlated gross
        rec = make_rec("ETH-USD", entry=2_000.0, quantity=2.0)
        check = validate_recommendation(
            rec, self.LIMITS, EQUITY, {"ETH-USD"},
            portfolio=context({"BTC-USD": 8_000.0}))
        assert not check.ok
        assert any("correlated gross" in r for r in check.reasons)
        assert any("BTC-USD" in r for r in check.reasons)

    def test_uncorrelated_gold_on_btc_book_passes(self):
        rec = make_rec("XAUUSD", entry=2_000.0, quantity=2.0,
                       asset=AssetClass.GOLD)
        check = validate_recommendation(
            rec, self.LIMITS, EQUITY, {"XAUUSD"},
            portfolio=context({"BTC-USD": 8_000.0}))
        assert check.ok, check.reasons


class TestPortfolioVarCap:
    # ETH book VaR: 2.326 * 0.05 * sqrt(.08^2 + .04^2 + 2*.9*.08*.04)
    # = 1.364% > 1.2%; the gold book comes to 1.040% < 1.2%.
    LIMITS = RiskLimits(max_portfolio_var_pct=1.2)

    def test_correlated_eth_pushes_var_over_the_limit(self):
        rec = make_rec("ETH-USD", entry=2_000.0, quantity=2.0)
        check = validate_recommendation(
            rec, self.LIMITS, EQUITY, {"ETH-USD"},
            portfolio=context({"BTC-USD": 8_000.0}))
        assert not check.ok
        assert any("portfolio VaR" in r for r in check.reasons)

    def test_uncorrelated_gold_keeps_var_under_the_limit(self):
        rec = make_rec("XAUUSD", entry=2_000.0, quantity=2.0,
                       asset=AssetClass.GOLD)
        check = validate_recommendation(
            rec, self.LIMITS, EQUITY, {"XAUUSD"},
            portfolio=context({"BTC-USD": 8_000.0}))
        assert check.ok, check.reasons


class TestDisabledByDefault:
    def test_default_limits_pass_even_on_a_correlated_book(self):
        rec = make_rec("ETH-USD", entry=2_000.0, quantity=2.0)
        check = validate_recommendation(
            rec, RiskLimits(), EQUITY, {"ETH-USD"},
            portfolio=context({"BTC-USD": 8_000.0}))
        assert check.ok, check.reasons

    def test_limits_set_but_no_portfolio_context_passes(self):
        limits = RiskLimits(max_portfolio_var_pct=0.1,
                            max_correlated_gross_pct=1.0)
        rec = make_rec("ETH-USD", entry=2_000.0, quantity=2.0)
        check = validate_recommendation(rec, limits, EQUITY, {"ETH-USD"})
        assert check.ok, check.reasons

    def test_symbol_without_return_history_fails_open(self):
        limits = RiskLimits(max_portfolio_var_pct=0.1,
                            max_correlated_gross_pct=1.0)
        rec = make_rec("DOGE-USD", entry=0.5, quantity=100.0)
        check = validate_recommendation(
            rec, limits, EQUITY, {"DOGE-USD"},
            portfolio=context({"BTC-USD": 8_000.0}))
        assert check.ok, check.reasons


# --- service -> router provider wiring (P2-05) --------------------------------


class TestServiceRouterWiring:
    def test_router_receives_context_when_positions_exist(self):
        # the service attaches its zero-arg provider at construction; with
        # an open book + stub daily bars the router sees signed notionals
        # and a covariance covering the open symbols
        from tests.test_pro_e2e_service import make_service

        service = make_service([130.0])
        assert service.run_once()["order_status"] == "filled"
        rng = np.random.default_rng(5)
        closes = {"XAUUSD": 130.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 40)))}
        service.dashboard.marketdata = _StubMarketData(closes)
        assert service.router.portfolio_risk_provider is not None
        ctx = service.router.portfolio_risk_provider()
        assert ctx is not None
        assert ctx.open_notional_by_symbol["XAUUSD"] > 0  # long book, signed
        assert "XAUUSD" in ctx.cov_symbols
        assert ctx.covariance is not None

    def test_provider_returns_none_on_a_flat_book(self):
        from tests.test_pro_e2e_service import make_service

        service = make_service([130.0])
        assert service.router.portfolio_risk_provider() is None

    def test_provider_fails_open_when_bars_raise(self):
        from tests.test_pro_e2e_service import make_service

        class _Boom:
            def get_bars(self, *a, **k):
                raise RuntimeError("vendor down")

        service = make_service([130.0])
        assert service.run_once()["order_status"] == "filled"
        service.dashboard.marketdata = _Boom()
        assert service.router.portfolio_risk_provider() is None


# --- dashboard exposure view -------------------------------------------------


class _StubMarketData:
    def __init__(self, closes_by_symbol):
        self._closes = closes_by_symbol

    def get_bars(self, symbol, timeframe, limit=250, end=None):
        return bars_from_closes(self._closes[symbol])


class TestDashboardExposureVar:
    def _positions(self):
        return [
            {"symbol": "BTC-USD", "quantity": 0.1, "mark_price": 80_000.0,
             "mark_source": "tick"},
            {"symbol": "ETH-USD", "quantity": 2.0, "mark_price": 2_000.0,
             "mark_source": "tick"},
        ]

    def test_exposure_block_carries_portfolio_var_pct(self):
        from tradingagents.pro.dashboard.service import portfolio_exposure

        rng = np.random.default_rng(11)
        r = rng.normal(0, 0.02, 40)
        closes = {
            "BTC-USD": 80_000.0 * np.exp(np.cumsum(r)),
            "ETH-USD": 2_000.0 * np.exp(np.cumsum(0.8 * r)),
        }
        view = portfolio_exposure(self._positions(), EQUITY, 3,
                                  marketdata=_StubMarketData(closes))
        assert view["portfolio_var_pct"] is not None
        assert view["portfolio_var_pct"] > 0
        assert view["gross_exposure_pct"] == pytest.approx(12.0)

    def test_var_is_null_without_marketdata_or_history(self):
        from tradingagents.pro.dashboard.service import portfolio_exposure

        view = portfolio_exposure(self._positions(), EQUITY, 3)
        assert view["portfolio_var_pct"] is None
        # one leg with no usable history -> the whole number is withheld
        closes = {"BTC-USD": 80_000.0 * np.exp(np.cumsum(
            np.random.default_rng(3).normal(0, 0.02, 40))),
            "ETH-USD": np.array([2_000.0, 2_010.0])}
        view = portfolio_exposure(self._positions(), EQUITY, 3,
                                  marketdata=_StubMarketData(closes))
        assert view["portfolio_var_pct"] is None
