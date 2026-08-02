"""Crowding / correlation primitives (roadmap P5-04, research backlog #9).

The question this module answers: **are our decisions correlated with what
a generic LLM trader would do?** If a retail trader who pastes the same
evidence into a chat window lands on the same calls we do, our edge is the
plumbing (gates, sizing, audit trail), not the judgment — and in a market
increasingly populated by LLM-prompted traders, that correlation is itself
a risk (the 2007 quant quake was a crowding episode, not an alpha failure).

Everything here is deterministic, pure numpy/stdlib, and works on
*decision series*: a timestamped, signed signal per source.

    +1 = BUY, -1 = SELL, 0 = HOLD **or** a rejected run (no position taken)

Rejections collapse to 0 deliberately: from a crowding standpoint "the gate
refused" and "the judge said hold" are the same market footprint — no order.

Three metrics, in increasing strictness:

1. **Raw agreement** — share of aligned timestamps where two sources emit
   the same signal. Easy to read, easy to fool.
2. **Cohen's kappa** — the same agreement, corrected for the agreement two
   sources would reach by chance alone given their own marginals::

       kappa = (p_o - p_e) / (1 - p_e),  p_e = SUM_k p_a(k) * p_b(k)

   This matters here more than almost anywhere else: decision series are
   *mostly flat* (most bars are HOLD), so two sources that both sit on
   their hands 90% of the time show ~80-90% raw agreement while sharing no
   directional view at all. Kappa reports that honestly as ~0. Kappa is
   0 for chance-level agreement, 1 for identity, negative for systematic
   disagreement, exactly 0 when one source never varies (a constant caller
   carries no information beyond chance), and **undefined** when both
   sources are constant on the same signal (p_e == 1) — reported as None,
   never as a fabricated 1.0.
3. **Crowding score** — the share of OUR directional (non-flat) decisions
   that match the baseline consensus, with
   ``distinctiveness = 1 - crowding``. This is the operator-facing number:
   "when we actually take a side, how often is it the same side the crowd
   of generic LLM traders takes?"

Degenerate inputs (too few overlapping points, no directional decisions,
no consensus) return None fields. Nothing here ever invents a number to
fill a hole, and nothing here raises on bad data.
"""

from __future__ import annotations

from collections import Counter

import numpy as np

#: Signed signal per pre-registered action. Anything else (a rejected run,
#: an unparseable verdict) is flat.
SIGNAL_BY_ACTION = {"BUY": 1, "SELL": -1, "HOLD": 0}

#: Below this many aligned observations, every agreement statistic is noise
#: — the primitives return None rather than a two-point "correlation".
MIN_OVERLAP = 5


def signal_for_action(action) -> int:
    """Signed signal for a ``TradeAction`` (or its string value). Unknown /
    missing actions — including rejected runs — are flat (0)."""
    value = getattr(action, "value", action)
    return SIGNAL_BY_ACTION.get(str(value).upper(), 0) if value is not None else 0


def decision_series(runs, symbol: str | None = None) -> list[dict]:
    """Our pre-registered decisions as a signed decision series.

    ``runs`` are recorder ``RunRecord``s (anything exposing ``run_id``,
    ``symbol``, ``started_at``, ``recommendation``, ``rejection``).
    ``symbol`` filters to one instrument; None keeps every run.

    Returns one entry per run, sorted by decision time::

        {"ts", "signal", "action", "run_id", "symbol", "confidence",
         "rejected"}

    A run that produced no recommendation (a gate or reviewer refused it)
    is kept with ``signal=0``, ``action="REJECTED"`` and the rejection
    stage — dropping refusals would silently inflate our directional rate
    and understate crowding on the decisions we did take.
    """
    entries: list[dict] = []
    for run in runs or []:
        run_symbol = getattr(run, "symbol", None)
        if symbol is not None and run_symbol != symbol:
            continue
        rec = getattr(run, "recommendation", None)
        rejection = getattr(run, "rejection", None)
        if rec is None:
            action, signal, confidence = "REJECTED", 0, None
        else:
            action = str(getattr(rec.action, "value", rec.action))
            signal = signal_for_action(action)
            confidence = getattr(rec, "confidence", None)
        entries.append({
            "ts": getattr(run, "started_at", None),
            "signal": signal,
            "action": action,
            "run_id": getattr(run, "run_id", None),
            "symbol": run_symbol,
            "confidence": confidence,
            "rejected": (rejection or {}).get("stage") if rejection else None,
        })
    return sorted(entries, key=lambda e: _ts_key(e["ts"]))


def cohens_kappa(a, b) -> float | None:
    """Cohen's kappa between two aligned, equal-length signal sequences.

        kappa = (p_o - p_e) / (1 - p_e)

    with ``p_o`` the observed agreement rate and ``p_e`` the agreement
    expected from the two sources' own marginal frequencies
    (``SUM_k p_a(k)*p_b(k)`` over the categories present).

    Returns None for empty/mismatched input and for the degenerate case
    ``p_e == 1`` — both sources constant on the same signal (e.g. two
    all-HOLD series), where raw agreement is a meaningless 100% and any
    kappa would be fabricated. A source that is constant while the other
    varies scores exactly 0.0, which is the correct reading: a caller who
    always says the same thing agrees with you at exactly chance rate.
    """
    sa = np.asarray(list(a), dtype=int)
    sb = np.asarray(list(b), dtype=int)
    if sa.size == 0 or sa.size != sb.size:
        return None
    n = sa.size
    p_o = float(np.mean(sa == sb))
    counts_a = Counter(sa.tolist())
    counts_b = Counter(sb.tolist())
    p_e = sum((counts_a[k] / n) * (counts_b.get(k, 0) / n)
              for k in counts_a)
    if p_e >= 1.0:
        return None
    return float((p_o - p_e) / (1.0 - p_e))


def agreement_matrix(series_by_source, min_overlap: int = MIN_OVERLAP) -> dict:
    """Pairwise agreement + Cohen's kappa between decision series.

    ``series_by_source`` maps a source name to a decision series (entries
    as produced by :func:`decision_series`, or plain ``(ts, signal)``
    pairs). Every pair is aligned on the timestamps the two sources
    *share* — pairwise, not globally, so one short baseline does not
    truncate the comparison between the others.

    Returns::

        {"sources": [...],
         "overlap":   {a: {b: n_shared_timestamps}},
         "agreement": {a: {b: raw_agreement | None}},
         "kappa":     {a: {b: cohens_kappa | None}},
         "pairs":     [{"a", "b", "n", "agreement", "kappa"}, ...],
         "min_overlap": n}

    Read the two together, never the agreement alone: on mostly-flat
    decision series a high raw agreement with kappa near 0 means the two
    sources agree only about doing nothing (see the module docstring).
    Pairs with fewer than ``min_overlap`` shared timestamps report None for
    both statistics; ``overlap`` still shows how thin the comparison was.
    """
    points = {name: _as_points(series)
              for name, series in (series_by_source or {}).items()}
    names = list(points)
    overlap: dict[str, dict[str, int]] = {n: {} for n in names}
    agreement: dict[str, dict[str, float | None]] = {n: {} for n in names}
    kappa: dict[str, dict[str, float | None]] = {n: {} for n in names}
    pairs: list[dict] = []
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            keys = sorted(points[a].keys() & points[b].keys())
            n_shared = len(keys)
            if n_shared < max(1, min_overlap):
                agr = kap = None
            else:
                sa = [points[a][k][1] for k in keys]
                sb = [points[b][k][1] for k in keys]
                agr = float(np.mean(np.asarray(sa) == np.asarray(sb)))
                kap = cohens_kappa(sa, sb)
            overlap[a][b] = n_shared
            agreement[a][b] = agr
            kappa[a][b] = kap
            if j > i:
                pairs.append({"a": a, "b": b, "n": n_shared,
                              "agreement": agr, "kappa": kap})
    return {
        "sources": names,
        "overlap": overlap,
        "agreement": agreement,
        "kappa": kappa,
        "pairs": pairs,
        "min_overlap": min_overlap,
    }


def crowding_score(ours, baselines, min_overlap: int = MIN_OVERLAP) -> dict:
    """How much of our signal is explained by the baseline consensus.

    ``ours`` is our decision series; ``baselines`` maps a baseline name to
    its series. All series are aligned on the timestamps they **all**
    share — a majority needs every voter present at the same moment.

    At each aligned timestamp the baselines vote; the consensus is the
    strict plurality signal. A tie has no consensus: those timestamps are
    counted in ``n_no_consensus`` and excluded from the score rather than
    scored against an arbitrary tiebreak.

    The score itself looks only at OUR directional decisions (signal != 0)
    — crowding is about the side we take, and scoring the flat bars would
    let "we both did nothing" dominate::

        crowding        = matched / n_scored
        distinctiveness = 1 - crowding

    where ``n_scored`` is our directional decisions that had a consensus to
    compare against. ``kappa_vs_consensus`` is the chance-corrected
    companion over ALL aligned points (flats included) — the honest check
    on a crowding number computed from few directional decisions.

    Returns ``{crowding, distinctiveness, kappa_vs_consensus, n_aligned,
    n_directional, n_scored, n_matched, n_no_consensus, per_baseline,
    min_overlap}``. The three statistics are None (never a made-up number)
    when fewer than ``min_overlap`` of our directional decisions could be
    scored — including the no-overlap and no-baseline cases.
    """
    names = list((baselines or {}).keys())
    keys, signals = _align({"__ours__": ours, **(baselines or {})})
    our_sig = signals.get("__ours__", np.empty(0, dtype=int))
    n_aligned = len(keys)

    out: dict = {
        "crowding": None,
        "distinctiveness": None,
        "kappa_vs_consensus": None,
        "n_aligned": n_aligned,
        "n_directional": int(np.count_nonzero(our_sig)) if n_aligned else 0,
        "n_scored": 0,
        "n_matched": 0,
        "n_no_consensus": 0,
        "per_baseline": {},
        "min_overlap": min_overlap,
    }
    if not names or n_aligned == 0:
        return out

    votes = np.vstack([signals[name] for name in names])  # (n_baselines, n)
    consensus = [_plurality(votes[:, i].tolist()) for i in range(n_aligned)]
    out["n_no_consensus"] = sum(1 for c in consensus if c is None)

    directional = [i for i in range(n_aligned) if our_sig[i] != 0]
    scored = [i for i in directional if consensus[i] is not None]
    matched = [i for i in scored if consensus[i] == our_sig[i]]
    out["n_scored"] = len(scored)
    out["n_matched"] = len(matched)

    out["per_baseline"] = {
        name: {
            "n": n_aligned,
            "match_rate": (
                float(np.mean([signals[name][i] == our_sig[i]
                               for i in directional]))
                if len(directional) >= max(1, min_overlap) else None),
            "kappa": (cohens_kappa(our_sig, signals[name])
                      if n_aligned >= max(1, min_overlap) else None),
        }
        for name in names
    }

    if len(scored) < max(1, min_overlap):
        return out  # too thin to score — honest hole, not a number
    crowding = len(matched) / len(scored)
    out["crowding"] = crowding
    out["distinctiveness"] = 1.0 - crowding
    voted = [i for i in range(n_aligned) if consensus[i] is not None]
    if len(voted) >= max(1, min_overlap):
        out["kappa_vs_consensus"] = cohens_kappa(
            [int(our_sig[i]) for i in voted], [int(consensus[i]) for i in voted])
    return out


def rolling_crowding(ours, baselines, window: int = 10, step: int = 1,
                     min_overlap: int = MIN_OVERLAP) -> list[dict]:
    """:func:`crowding_score` over a sliding window of aligned decisions,
    so drift is visible: converging on the crowd over time is the failure
    mode a single all-history number hides.

    The window counts *aligned observations*, not calendar time. With
    ``step=1`` and ``n`` aligned points the result has ``n - window + 1``
    entries (empty when there are fewer than ``window`` aligned points).
    Each entry is ``{"start", "end", "n", "crowding", "distinctiveness",
    "n_scored", "n_directional"}``; ``start``/``end`` are the first and
    last timestamps in the window, and the statistics are None on windows
    too thin to score — exactly as in the whole-sample call.
    """
    if window < 1 or step < 1:
        return []
    keys, signals = _align({"__ours__": ours, **(baselines or {})})
    n = len(keys)
    if n < window:
        return []
    names = list((baselines or {}).keys())
    rows: list[dict] = []
    for start in range(0, n - window + 1, step):
        stop = start + window
        window_keys = keys[start:stop]
        sliced_ours = [(keys[i], int(signals["__ours__"][i]))
                       for i in range(start, stop)]
        sliced_base = {
            name: [(keys[i], int(signals[name][i]))
                   for i in range(start, stop)]
            for name in names
        }
        score = crowding_score(sliced_ours, sliced_base,
                               min_overlap=min_overlap)
        rows.append({
            "start": window_keys[0],
            "end": window_keys[-1],
            "n": window,
            "crowding": score["crowding"],
            "distinctiveness": score["distinctiveness"],
            "n_scored": score["n_scored"],
            "n_directional": score["n_directional"],
        })
    return rows


# --- internals ---------------------------------------------------------------------

def _ts_key(ts) -> str:
    """Stable, sortable alignment key. Datetimes align by ISO string, so a
    naive and an aware timestamp are NOT silently treated as the same
    moment — an alignment we cannot justify is better left unaligned."""
    if ts is None:
        return ""
    return ts.isoformat() if hasattr(ts, "isoformat") else str(ts)


def _as_points(series) -> dict[str, tuple[object, int]]:
    """``{ts_key: (ts, signal)}`` from decision-series entries or plain
    ``(ts, signal)`` pairs. Signals are sign-normalized to {-1, 0, +1};
    entries without a timestamp or signal are dropped, and the FIRST entry
    at a repeated timestamp wins (matching the corpus builder's dedupe)."""
    out: dict[str, tuple[object, int]] = {}
    for item in series or []:
        if isinstance(item, dict):
            ts, signal = item.get("ts"), item.get("signal")
        else:
            try:
                ts, signal = item[0], item[1]
            except (TypeError, IndexError, KeyError):
                continue
        if ts is None or signal is None:
            continue
        key = _ts_key(ts)
        if key in out:
            continue
        out[key] = (ts, int(np.sign(signal)))
    return out


def _align(series_by_source) -> tuple[list[str], dict[str, np.ndarray]]:
    """Align every series on the timestamps they ALL share. Returns the
    sorted shared keys and one int array of signals per source."""
    points = {name: _as_points(series)
              for name, series in (series_by_source or {}).items()}
    if not points:
        return [], {}
    shared: set[str] | None = None
    for source_points in points.values():
        keys = set(source_points)
        shared = keys if shared is None else (shared & keys)
    keys_sorted = sorted(shared or set())
    signals = {
        name: np.asarray([source_points[k][1] for k in keys_sorted], dtype=int)
        for name, source_points in points.items()
    }
    return keys_sorted, signals


def _plurality(votes: list[int]) -> int | None:
    """The strictly most common vote, or None on a tie (no consensus)."""
    if not votes:
        return None
    ranked = Counter(votes).most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return int(ranked[0][0])


__all__ = [
    "MIN_OVERLAP",
    "SIGNAL_BY_ACTION",
    "agreement_matrix",
    "cohens_kappa",
    "crowding_score",
    "decision_series",
    "rolling_crowding",
    "signal_for_action",
]
