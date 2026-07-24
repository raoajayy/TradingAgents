"""Meta-labeling (track T3): a secondary model that SIZES/FILTERS a primary
strategy's signals — it never generates signals of its own.

Two pieces, both pure stdlib + deterministic:

- ``triple_barrier_labels``: López de Prado's triple-barrier method. For each
  primary-signal event, look forward at most ``max_holding`` bars and label 1
  if the profit-taking barrier is reached before the stop, else 0 (a same-bar
  double-touch resolves to the stop — the conservative intrabar policy used
  throughout the engine). No look-ahead beyond the event's own barrier window.
- ``MetaLabeler``: a small in-repo logistic-regression head fit on
  (features → label); ``predict_size`` returns the win probability in [0, 1],
  used as a position-size multiplier or a take/skip filter on the primary
  signal. To avoid the classic meta-label leakage it must be trained only on
  embargoed walk-forward windows (the caller's discipline).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from tradingagents.contracts import OHLCVBar


@dataclass(frozen=True)
class Event:
    """A primary-signal event to meta-label: the bar it fired on and its side."""

    index: int
    side: str = "BUY"  # BUY | SELL


def triple_barrier_labels(
    bars: Sequence[OHLCVBar],
    events: Sequence[Event],
    *,
    pt: float,
    sl: float,
    max_holding: int,
) -> list[int]:
    """Label each event 1 (profit barrier hit first) or 0 (stop first, or the
    vertical barrier closed non-positive). ``pt``/``sl`` are fractions of the
    entry price (e.g. 0.02 = 2%). Look-ahead is bounded to the event's own
    forward window; a bar touching both barriers counts as the stop."""
    if pt <= 0 or sl <= 0:
        raise ValueError("pt and sl must be > 0")
    labels: list[int] = []
    n = len(bars)
    for ev in events:
        i = ev.index
        long = ev.side == "BUY"
        entry = bars[i].close
        up = entry * (1 + pt) if long else entry * (1 + sl)
        down = entry * (1 - sl) if long else entry * (1 - pt)
        label = 0
        resolved = False
        last = min(i + max_holding, n - 1)
        for j in range(i + 1, last + 1):
            bar = bars[j]
            hit_up = bar.high >= up
            hit_down = bar.low <= down
            if hit_up and hit_down:
                # both barriers in one bar → resolve to the adverse side
                label = 0 if long else 1
                resolved = True
                break
            if hit_up:
                label = 1 if long else 0
                resolved = True
                break
            if hit_down:
                label = 0 if long else 1
                resolved = True
                break
        if not resolved:
            # vertical barrier: label by the sign of the held return
            ret = bars[last].close - entry
            label = 1 if (ret > 0) == long and ret != 0 else 0
        labels.append(label)
    return labels


class MetaLabeler:
    """In-repo logistic regression (full-batch gradient descent on standardized
    features). ``fit(X, y)`` then ``predict_size(x) -> [0, 1]`` (the win
    probability) as a sizing multiplier / filter. Deterministic: weights start
    at zero and the update is exact, so no randomness is involved (``seed`` is
    accepted for API symmetry)."""

    def __init__(self, epochs: int = 300, lr: float = 0.1, l2: float = 1e-4,
                 seed: int = 0, model: str = "logistic"):
        if model not in ("logistic", "boosting"):
            raise ValueError(f"unknown model {model!r} (logistic | boosting)")
        self.epochs = epochs
        self.lr = lr
        self.l2 = l2
        self.seed = seed
        # "logistic" is the pure-Python default; "boosting" is an opt-in
        # gradient-boosting head behind the [ml] extra (scikit-learn)
        self.model = model
        self._mean: list[float] = []
        self._std: list[float] = []
        self._w: list[float] = []
        self._b: float = 0.0
        self._sk = None  # fitted scikit-learn estimator (boosting)

    def fit(self, features: Sequence[Sequence[float]],
            labels: Sequence[int]) -> MetaLabeler:
        rows = [list(map(float, r)) for r in features]
        y = [1.0 if v else 0.0 for v in labels]
        if len(rows) < 2 or len(rows) != len(y):
            raise ValueError("need >= 2 aligned (features, label) rows")
        if self.model == "boosting":
            try:
                from sklearn.ensemble import GradientBoostingClassifier
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise ImportError(
                    "model='boosting' needs scikit-learn — install "
                    "tradingagents[ml]") from exc
            self._sk = GradientBoostingClassifier(random_state=self.seed)
            self._sk.fit(rows, [int(v) for v in y])
            return self
        dim = len(rows[0])
        self._mean = [math.fsum(r[j] for r in rows) / len(rows) for j in range(dim)]
        self._std = []
        for j in range(dim):
            var = math.fsum((r[j] - self._mean[j]) ** 2 for r in rows) / len(rows)
            self._std.append(math.sqrt(var) or 1.0)
        xs = [self._standardize(r) for r in rows]
        self._w = [0.0] * dim
        self._b = 0.0
        m = len(xs)
        for _ in range(self.epochs):
            gw = [0.0] * dim
            gb = 0.0
            for x, yi in zip(xs, y, strict=False):
                pred = self._sigmoid(sum(w * xi for w, xi in zip(self._w, x, strict=False)) + self._b)
                err = pred - yi
                for j in range(dim):
                    gw[j] += err * x[j]
                gb += err
            self._w = [w - self.lr * (gw[j] / m + self.l2 * w)
                       for j, w in enumerate(self._w)]
            self._b -= self.lr * (gb / m)
        return self

    def predict_size(self, features: Sequence[float]) -> float:
        """Win probability in [0, 1] for one feature vector (0 → skip / smallest
        size, 1 → full conviction)."""
        if self._sk is not None:
            return float(self._sk.predict_proba([list(map(float, features))])[0][1])
        if not self._w:
            raise RuntimeError("model is not fitted")
        x = self._standardize(list(map(float, features)))
        return self._sigmoid(
            sum(w * xi for w, xi in zip(self._w, x, strict=False)) + self._b)

    def _standardize(self, v: list[float]) -> list[float]:
        return [(v[j] - self._mean[j]) / self._std[j] for j in range(len(v))]

    @staticmethod
    def _sigmoid(z: float) -> float:
        if z < 0:  # numerically stable
            e = math.exp(z)
            return e / (1.0 + e)
        return 1.0 / (1.0 + math.exp(-z))


__all__ = ["Event", "MetaLabeler", "triple_barrier_labels"]
