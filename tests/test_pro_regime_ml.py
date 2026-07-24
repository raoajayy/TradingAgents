"""ML regime detection (track T3): seeded KMeans over quant features, mapped
to MarketRegime via the rule thresholds. Type-compatible with the rule-based
classifier and deterministic under a seed; rule-based stays the default."""

import math
from datetime import timedelta

import pytest

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import MarketRegime, OHLCVBar, Timeframe
from tradingagents.pro.analytics import MLRegimeModel
from tradingagents.pro.analytics.features import classify_regime


def _window(kind: str, n: int = 60, base: float = 100.0) -> list[OHLCVBar]:
    """Build a bar window with a characteristic shape: strong uptrend, flat
    range, or high-volatility chop."""
    bars = []
    price = base
    for i in range(n):
        if kind == "trend":
            close = base + i * 1.5           # smooth linear rise (high r², low vol)
        elif kind == "range":
            close = base + 2.0 * math.sin(i / 3.0)  # oscillate around base
        else:  # "vol"
            close = base * (1.0 + (0.08 if i % 2 else -0.08))  # ±8% chop each bar
        o = price
        hi = max(o, close) + 0.5
        lo = min(o, close) - 0.5
        bars.append(OHLCVBar(timeframe=Timeframe.D1,
                             start=BASE_TS + timedelta(days=i),
                             open=o, high=hi, low=lo, close=close, volume=1000.0))
        price = close
    return bars


def _training_windows():
    return ([_window("trend", base=100 + k) for k in range(4)]
            + [_window("range", base=100 + k) for k in range(4)]
            + [_window("vol", base=100 + k) for k in range(4)])


def test_classify_returns_a_market_regime():
    model = MLRegimeModel(n_clusters=3, seed=0).fit(_training_windows())
    label = model.classify_regime(_window("trend"))
    assert isinstance(label, MarketRegime)


def test_fit_and_classify_are_deterministic():
    windows = _training_windows()
    a = MLRegimeModel(n_clusters=3, seed=7).fit(windows)
    b = MLRegimeModel(n_clusters=3, seed=7).fit(windows)
    assert a._centroids == b._centroids
    assert a._labels == b._labels
    probe = _window("vol")
    assert a.classify_regime(probe) == b.classify_regime(probe)


def test_high_vol_window_is_not_labelled_calm():
    model = MLRegimeModel(n_clusters=3, seed=1).fit(_training_windows())
    label = model.classify_regime(_window("vol"))
    # ±8%/bar chop annualizes to a very high vol → crisis / high-volatility,
    # never a calm label
    assert label in (MarketRegime.CRISIS, MarketRegime.HIGH_VOLATILITY)


def test_signature_matches_rule_based_classifier():
    # both take a bar window and return a MarketRegime — drop-in compatible
    model = MLRegimeModel(n_clusters=2, seed=0).fit(_training_windows())
    w = _window("trend")
    assert isinstance(classify_regime(w), MarketRegime)
    assert isinstance(model.classify_regime(w), MarketRegime)


def test_fit_needs_at_least_two_windows():
    with pytest.raises(ValueError, match="need >= 2 windows"):
        MLRegimeModel().fit([_window("trend")])


# --- optional [ml] accelerators (scikit-learn / hmmlearn) --------------------


def test_gmm_engine_returns_market_regimes():
    pytest.importorskip("sklearn")
    model = MLRegimeModel(n_clusters=3, seed=0, engine="gmm").fit(_training_windows())
    assert isinstance(model.classify_regime(_window("trend")), MarketRegime)


def test_hmm_engine_returns_market_regimes():
    pytest.importorskip("hmmlearn")
    model = MLRegimeModel(n_clusters=3, seed=0, engine="hmm").fit(_training_windows())
    assert isinstance(model.classify_regime(_window("vol")), MarketRegime)


def test_unknown_engine_rejected():
    with pytest.raises(ValueError, match="unknown engine"):
        MLRegimeModel(engine="wat")
