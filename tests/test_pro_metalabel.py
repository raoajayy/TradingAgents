"""Meta-labeling (track T3): triple-barrier labels + the logistic sizing head.
No look-ahead beyond each event's barrier window; deterministic."""

from datetime import timedelta

import pytest

from tests.pro_fakes import BASE_TS
from tradingagents.contracts import OHLCVBar, Timeframe
from tradingagents.pro.backtest import Event, MetaLabeler, triple_barrier_labels


def _bars(closes: list[float]) -> list[OHLCVBar]:
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(OHLCVBar(timeframe=Timeframe.D1,
                            start=BASE_TS + timedelta(days=i),
                            open=prev, high=max(prev, c), low=min(prev, c),
                            close=c, volume=1000.0))
        prev = c
    return out


# --- triple-barrier labels ---------------------------------------------------


def test_profit_barrier_hit_first_labels_1():
    # entry 100, rises to 106 within the window → +5% pt reached before -5% sl
    bars = _bars([100, 102, 104, 106, 108])
    labels = triple_barrier_labels(bars, [Event(0, "BUY")],
                                   pt=0.05, sl=0.05, max_holding=4)
    assert labels == [1]


def test_stop_barrier_hit_first_labels_0():
    # entry 100, falls to 94 → -5% sl reached first
    bars = _bars([100, 98, 96, 94, 92])
    labels = triple_barrier_labels(bars, [Event(0, "BUY")],
                                   pt=0.05, sl=0.05, max_holding=4)
    assert labels == [0]


def test_short_side_is_mirrored():
    # a SELL profits when price FALLS → hitting the lower barrier labels 1
    bars = _bars([100, 98, 96, 94, 92])
    labels = triple_barrier_labels(bars, [Event(0, "SELL")],
                                   pt=0.05, sl=0.05, max_holding=4)
    assert labels == [1]


def test_no_lookahead_beyond_max_holding():
    # pt would be hit at bar 4, but max_holding=2 closes the window at bar 2
    # (flat) → vertical barrier, non-positive → 0
    bars = _bars([100, 100, 100, 100, 106])
    labels = triple_barrier_labels(bars, [Event(0, "BUY")],
                                   pt=0.05, sl=0.05, max_holding=2)
    assert labels == [0]


def test_same_bar_double_touch_resolves_to_stop():
    # a single wide bar touches both barriers → conservative: stop (0) for long
    bars = [
        OHLCVBar(timeframe=Timeframe.D1, start=BASE_TS, open=100, high=100,
                 low=100, close=100, volume=1000.0),
        OHLCVBar(timeframe=Timeframe.D1, start=BASE_TS + timedelta(days=1),
                 open=100, high=110, low=90, close=100, volume=1000.0),
    ]
    labels = triple_barrier_labels(bars, [Event(0, "BUY")],
                                   pt=0.05, sl=0.05, max_holding=1)
    assert labels == [0]


# --- logistic sizing head ----------------------------------------------------


def _separable():
    # feature 0 cleanly predicts the label
    X = [[2.0, 0.1], [1.8, -0.2], [2.2, 0.0],   # positives
         [-2.0, 0.1], [-1.7, 0.2], [-2.1, -0.1]]  # negatives
    y = [1, 1, 1, 0, 0, 0]
    return X, y


def test_metalabeler_separates_and_sizes_in_unit_interval():
    X, y = _separable()
    ml = MetaLabeler(epochs=400).fit(X, y)
    hi = ml.predict_size([2.0, 0.0])
    lo = ml.predict_size([-2.0, 0.0])
    assert 0.0 <= lo < 0.5 < hi <= 1.0


def test_metalabeler_is_deterministic():
    X, y = _separable()
    a = MetaLabeler(epochs=200).fit(X, y).predict_size([1.5, 0.0])
    b = MetaLabeler(epochs=200).fit(X, y).predict_size([1.5, 0.0])
    assert a == b


def test_metalabeler_needs_fit_first():
    with pytest.raises(RuntimeError, match="not fitted"):
        MetaLabeler().predict_size([1.0, 2.0])
