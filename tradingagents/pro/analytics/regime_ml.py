"""ML market-regime detection (track T3): an unsupervised alternative to the
rule-based ``features.classify_regime``.

Default engine is an in-repo, seeded KMeans over the standardized quant-feature
vector (annualized vol, %/bar trend slope, trend R², close z-score). Each fitted
centroid is mapped to a ``MarketRegime`` by running the SAME rule thresholds on
the centroid's (destandardized) features — so clusters carry interpretable,
stable labels instead of arbitrary integers, and a new window is classified by
nearest centroid. ``classify_regime`` has the identical signature and return
type as the rule-based function, so it drops into any consumer that accepts a
regime callable. Pure stdlib + deterministic; the rule-based classifier stays
the default everywhere.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence

from tradingagents.contracts import MarketRegime, OHLCVBar
from tradingagents.pro.analytics.features import (
    _HIGH_VOL,
    _LOW_VOL,
    _TREND_R2,
    _TREND_SLOPE_PCT,
    close_zscore,
    realized_volatility,
    trend_slope,
)


def _feature_vector(bars: Sequence[OHLCVBar]) -> tuple[float, float, float, float]:
    """(annualized vol, %/bar slope, trend R², close z-score). The z-score
    needs a 50-bar window; when short, it degrades to 0.0 (neutral)."""
    vol = realized_volatility(bars)
    slope, r2 = trend_slope(bars)
    try:
        z = close_zscore(bars)
    except ValueError:
        z = 0.0
    return (vol, slope, r2, z)


def _label_for(vol: float, slope_pct: float, r_squared: float) -> MarketRegime:
    """The rule-based regime label applied to a centroid's features — reuses
    features.py thresholds so ML labels line up with the rule-based ones."""
    if vol >= _HIGH_VOL * 2:
        return MarketRegime.CRISIS
    if vol >= _HIGH_VOL:
        return MarketRegime.HIGH_VOLATILITY
    if abs(slope_pct) >= _TREND_SLOPE_PCT and r_squared >= _TREND_R2:
        return (MarketRegime.TRENDING_UP if slope_pct > 0
                else MarketRegime.TRENDING_DOWN)
    if vol <= _LOW_VOL:
        return MarketRegime.LOW_VOLATILITY
    return MarketRegime.RANGING


def _kmeans(points: list[list[float]], k: int, seed: int,
            iters: int = 50) -> list[list[float]]:
    """Lloyd's algorithm, seeded. Returns k centroids. Deterministic: initial
    centroids are drawn with one ``random.Random(seed)`` and assignment ties
    break to the lowest index."""
    rng = random.Random(seed)
    uniq = [list(p) for p in {tuple(p) for p in points}]
    k = max(1, min(k, len(uniq)))
    centroids = rng.sample(uniq, k)
    dim = len(points[0])
    for _ in range(iters):
        buckets: list[list[list[float]]] = [[] for _ in range(k)]
        for p in points:
            best, bd = 0, math.inf
            for i, c in enumerate(centroids):
                d = sum((a - b) ** 2 for a, b in zip(p, c, strict=False))
                if d < bd:
                    best, bd = i, d
            buckets[best].append(p)
        moved = False
        for i in range(k):
            if not buckets[i]:
                continue
            new_c = [math.fsum(p[j] for p in buckets[i]) / len(buckets[i])
                     for j in range(dim)]
            if new_c != centroids[i]:
                centroids[i], moved = new_c, True
        if not moved:
            break
    return centroids


class MLRegimeModel:
    """Fit on a set of historical bar-windows, then classify a new window to
    the nearest cluster's regime label. Drop-in for ``classify_regime``."""

    def __init__(self, n_clusters: int = 5, seed: int = 0,
                 engine: str = "kmeans"):
        if engine not in ("kmeans", "gmm", "hmm"):
            raise ValueError(f"unknown engine {engine!r} (kmeans | gmm | hmm)")
        self.n_clusters = max(1, n_clusters)
        self.seed = seed
        # "kmeans" is the pure-Python default; "gmm"/"hmm" are opt-in
        # accelerators behind the [ml] extra (scikit-learn / hmmlearn)
        self.engine = engine
        self._mean: list[float] | None = None
        self._std: list[float] | None = None
        self._centroids: list[list[float]] = []
        self._labels: list[MarketRegime] = []

    def fit(self, windows: Sequence[Sequence[OHLCVBar]]) -> MLRegimeModel:
        raw = [list(_feature_vector(w)) for w in windows]
        if len(raw) < 2:
            raise ValueError("need >= 2 windows to fit a regime model")
        dim = len(raw[0])
        self._mean = [math.fsum(r[j] for r in raw) / len(raw) for j in range(dim)]
        self._std = []
        for j in range(dim):
            var = math.fsum((r[j] - self._mean[j]) ** 2 for r in raw) / len(raw)
            self._std.append(math.sqrt(var) or 1.0)
        standardized = [self._standardize(r) for r in raw]
        if self.engine == "kmeans":
            self._centroids = _kmeans(standardized, self.n_clusters, self.seed)
        else:
            self._centroids = self._fit_external(standardized)
        # label each centroid by destandardizing back to feature space and
        # applying the rule thresholds (vol, slope, r² are dims 0,1,2)
        self._labels = []
        for c in self._centroids:
            vol, slope, r2, _ = self._destandardize(c)
            self._labels.append(_label_for(vol, slope, r2))
        return self

    def classify_regime(self, bars: Sequence[OHLCVBar]) -> MarketRegime:
        """Nearest-centroid regime for one window (same signature/return as
        ``features.classify_regime``)."""
        if not self._centroids or self._mean is None:
            raise RuntimeError("model is not fitted")
        p = self._standardize(list(_feature_vector(bars)))
        best, bd = 0, math.inf
        for i, c in enumerate(self._centroids):
            d = sum((a - b) ** 2 for a, b in zip(p, c, strict=False))
            if d < bd:
                best, bd = i, d
        return self._labels[best]

    def _fit_external(self, points: list[list[float]]) -> list[list[float]]:
        """GMM/HMM cluster means (opt-in [ml] extra). Returns component means in
        standardized space, so classification stays nearest-mean (as KMeans)."""
        k = max(1, min(self.n_clusters, len(points)))
        if self.engine == "gmm":
            try:
                from sklearn.mixture import GaussianMixture
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise ImportError(
                    "engine='gmm' needs scikit-learn — install tradingagents[ml]"
                ) from exc
            model = GaussianMixture(n_components=k, random_state=self.seed)
            model.fit(points)
            return [list(m) for m in model.means_]
        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise ImportError(
                "engine='hmm' needs hmmlearn — install tradingagents[ml]"
            ) from exc
        model = GaussianHMM(n_components=k, covariance_type="diag",
                            random_state=self.seed, n_iter=50)
        model.fit(points)
        return [list(m) for m in model.means_]

    def _standardize(self, v: list[float]) -> list[float]:
        return [(v[j] - self._mean[j]) / self._std[j] for j in range(len(v))]

    def _destandardize(self, v: list[float]) -> list[float]:
        return [v[j] * self._std[j] + self._mean[j] for j in range(len(v))]


__all__ = ["MLRegimeModel"]
