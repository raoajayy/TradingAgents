"""P2-03 memorization-audit anonymizer.

A model that has memorized "gold rallied into 2026-07" can ace a named
replay without reading the data. The masker strips *identity* (tickers,
asset-class vocabulary) and *calendar* (absolute dates) from rendered
prompts while leaving every number — prices, indicator values, macro
readings — verbatim: the market data IS the test, the labels are the leak.

Two hooks, both pure and deterministic:

- ``render_context(..., anonymize=masker)`` / ``anonymization_scope(masker)``
  in ``agents/rendering.py`` masks the data block at the snapshot->prompt
  boundary, so agent code is untouched.
- ``anonymize_llm(bundle, masker)`` wraps every model in the bundle so the
  *final* prompt of every stage (agents, debate, judge, critic, reflection
  — which interpolate ``snapshot.symbol``/``asset`` outside the data
  block) is masked before it leaves the process.

Real-model entry: ``python -m tradingagents.pro.evals --memorization-audit``.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field
from datetime import date, datetime

# Asset-class vocabulary per normalized symbol root. Longest-first within
# each tuple so "btcusd" wins over "btc". These are the identity words a
# memorizing model could key on; macro series names (DXY, CPI) are data,
# not identity, and stay.
_KNOWN_ALIASES: dict[str, tuple[str, ...]] = {
    "XAU": ("xau/usd", "xau-usd", "xauusd", "xau", "gold", "bullion"),
    "BTC": ("btc-usd", "btc/usd", "btcusd", "xbt", "btc", "bitcoin"),
    "ETH": ("eth-usd", "eth/usd", "ethusd", "eth", "ethereum", "ether"),
    "SOL": ("sol-usd", "sol/usd", "solusd", "sol", "solana"),
    "XAG": ("xag/usd", "xag-usd", "xagusd", "xag", "silver"),
}

_LABELS = tuple(f"ASSET_{c}" for c in string.ascii_uppercase)

# ISO dates / datetimes as the renderer emits them: bars "%Y-%m-%d %H:%M",
# news "(%Y-%m-%d)", plus full ISO stamps in recorded payloads. The time
# part is *kept* (bar ordering within a day is data, not calendar leakage);
# only the absolute date becomes relative.
_ISO_DATE = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})"
    r"(?:[ T](\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
_MONTHS = {m.lower(): i + 1 for i, m in enumerate(
    ("January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"))}
_TEXT_DATE = re.compile(
    r"\b(" + "|".join(m[:3] for m in _MONTHS) + r")[a-z]*\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b",
    re.IGNORECASE,
)


def _symbol_root(symbol: str) -> str:
    """'BTC-USD' -> 'BTCUSD' -> matched against known roots."""
    return re.sub(r"[^A-Z0-9]", "", symbol.upper())


def _alias_words(symbol: str) -> tuple[str, ...]:
    root = _symbol_root(symbol)
    for key, aliases in _KNOWN_ALIASES.items():
        if root.startswith(key):
            extra = tuple(
                v for v in (symbol, root) if v.lower() not in aliases
            )
            return tuple(a.lower() for a in (*extra, *aliases))
    # unknown symbol: mask its literal spellings only
    variants = {symbol, root, symbol.replace("-", "/"), symbol.replace("-", "")}
    return tuple(sorted((v.lower() for v in variants if v), key=len, reverse=True))


@dataclass
class Anonymizer:
    """Deterministic identity+calendar masker for rendered prompt text.

    - tickers/asset words -> stable ``ASSET_A``/``ASSET_B``... labels in
      registration order (``seed`` rotates the label alphabet so mapping
      can be varied without losing determinism);
    - absolute dates -> relative to ``reference`` ("T-3d", "T-0", "T+2d");
    - numbers are never touched: honest masking hides *labels*, not data.

    ``mask`` is pure and idempotent: masking masked text is a no-op.
    """

    reference: datetime | date | None = None
    seed: int = 0
    _order: list[str] = field(default_factory=list, init=False, repr=False)
    _tokens: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _patterns: list[tuple[re.Pattern, str]] = field(
        default_factory=list, init=False, repr=False)

    # --- identity ------------------------------------------------------------

    def register(self, symbol: str) -> str:
        """Assign (or return) the neutral token for a symbol. Stable per
        instance: first registered symbol is ASSET_<seed-rotated A>, etc."""
        root = _symbol_root(symbol)
        if root in self._tokens:
            return self._tokens[root]
        token = _LABELS[(len(self._order) + self.seed) % len(_LABELS)]
        self._order.append(root)
        self._tokens[root] = token
        words = sorted(set(_alias_words(symbol)), key=len, reverse=True)
        # boundary allows underscores so identifier segments are caught too:
        # GOLD_VOL_INDEX -> ASSET_A_VOL_INDEX (metric names leak identity),
        # while letter-adjacent lookalikes (Marigold, GOLDEN) stay intact
        pattern = re.compile(
            r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(w) for w in words)
            + r")(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        self._patterns.append((pattern, token))
        return token

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._tokens)

    # --- calendar -------------------------------------------------------------

    def _relative(self, d: date, clock: str | None = None) -> str:
        ref = self.reference
        if ref is None:
            tag = "T-?d"
        else:
            ref_d = ref.date() if isinstance(ref, datetime) else ref
            days = (ref_d - d).days
            tag = "T-0" if days == 0 else (f"T-{days}d" if days > 0 else f"T+{-days}d")
        return f"{tag} {clock}" if clock else tag

    def _mask_dates(self, text: str) -> str:
        def iso(m: re.Match) -> str:
            try:
                d = date(int(m[1]), int(m[2]), int(m[3]))
            except ValueError:  # e.g. version string 2026-99-99: leave it
                return m[0]
            return self._relative(d, m[4])

        def textual(m: re.Match) -> str:
            ref = self.reference
            year = int(m[3]) if m[3] else (ref.year if ref is not None else 0)
            month = next(
                (v for k, v in _MONTHS.items() if k.startswith(m[1].lower())), None)
            try:
                d = date(year, month, int(m[2])) if month else None
            except ValueError:
                return m[0]
            return self._relative(d) if d else m[0]

        return _TEXT_DATE.sub(textual, _ISO_DATE.sub(iso, text))

    # --- entry point ------------------------------------------------------------

    def mask(self, text: str) -> str:
        if not text:
            return text
        text = self._mask_dates(text)
        for pattern, token in self._patterns:
            text = pattern.sub(token, text)
        return text


# --- LLM-boundary wrapper -------------------------------------------------------


class _MaskedRunnable:
    def __init__(self, inner, masker: Anonymizer):
        self._inner = inner
        self._masker = masker

    def invoke(self, prompt):
        return self._inner.invoke(self._masker.mask(prompt))


class _MaskedRefsRunnable(_MaskedRunnable):
    """Only built when the inner runnable exposes ``invoke_with_refs``
    (deterministic rules engines) — the base class must NOT grow the method
    unconditionally, or ``hasattr`` dispatch in EvidenceAgent misroutes."""

    def invoke_with_refs(self, prompt, spec, refs):
        # rules engines vote on data_refs, not prose; masking the prompt
        # keeps captured transcripts consistent either way
        return self._inner.invoke_with_refs(self._masker.mask(prompt), spec, refs)


class AnonymizingLLM:
    """Masks every outgoing prompt; mirrors CostTrackingLLM's transparent
    with_structured_output contract, so it stacks with the other wrappers."""

    def __init__(self, inner, masker: Anonymizer):
        self.inner = inner
        self.masker = masker

    def with_structured_output(self, schema):
        runnable = self.inner.with_structured_output(schema)
        cls = (_MaskedRefsRunnable if hasattr(runnable, "invoke_with_refs")
               else _MaskedRunnable)
        return cls(runnable, self.masker)


def anonymize_llm(llm_or_bundle, masker: Anonymizer):
    """Wrap a model (or a whole ModelBundle) so all prompts are masked."""
    from tradingagents.pro.models import ModelBundle

    bundle = ModelBundle.coerce(llm_or_bundle)
    quick = AnonymizingLLM(bundle.quick, masker)
    deep = quick if bundle.deep is bundle.quick else AnonymizingLLM(bundle.deep, masker)
    overrides = {
        team: (quick if model is bundle.quick else AnonymizingLLM(model, masker))
        for team, model in bundle.team_overrides.items()
    }
    return ModelBundle(quick=quick, deep=deep, team_overrides=overrides)


# --- memorization audit -----------------------------------------------------------

# protocol gate (docs/EVAL_PROTOCOL.md): named-vs-anonymized action
# agreement below this flags possible memorization/contamination
AGREEMENT_FLAG_THRESHOLD = 0.85


def _run_actions(llm, config, snapshot, samples: int, **kwargs) -> tuple[list[str], list[int]]:
    from tradingagents.pro.pipeline import run_pipeline

    actions: list[str] = []
    confidences: list[int] = []
    for _ in range(samples):
        state = run_pipeline(llm, config, snapshot, **kwargs)
        rec = state.get("recommendation")
        actions.append(rec.action.value if rec else "rejected")
        if rec is not None:
            confidences.append(rec.confidence)
    return actions, confidences


def run_memorization_audit(llm, config, samples: int = 1,
                           case_names: tuple[str, ...] | None = None,
                           snapshots: dict[str, object] | None = None,
                           seed: int = 0, **kwargs) -> list[dict]:
    """Run each frozen snapshot ``samples`` times named and ``samples``
    times anonymized (same model, same data, masked labels); report
    per-symbol action agreement. ``snapshots`` lets a caller add
    recorder-loaded runs (name -> MarketSnapshot) beside the stability
    fixtures."""
    from statistics import mean

    from tradingagents.pro.evals.golden import golden_cases
    from tradingagents.pro.evals.stability import DEFAULT_CASE_NAMES

    names = case_names if case_names is not None else DEFAULT_CASE_NAMES
    by_name = {c.name: c.snapshot for c in golden_cases()}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise ValueError(f"unknown golden cases: {missing}")
    work = [(n, by_name[n]) for n in names]
    work += sorted((snapshots or {}).items())

    from tradingagents.pro.agents.rendering import anonymization_scope

    rows: list[dict] = []
    for name, snapshot in work:
        named_actions, named_conf = _run_actions(llm, config, snapshot,
                                                 samples, **kwargs)
        masker = Anonymizer(reference=snapshot.as_of, seed=seed)
        masker.register(snapshot.symbol)
        masked_llm = anonymize_llm(llm, masker)
        with anonymization_scope(masker):
            anon_actions, anon_conf = _run_actions(masked_llm, config,
                                                   snapshot, samples, **kwargs)
        agreement = mean(
            1.0 if a == b else 0.0
            for a, b in zip(named_actions, anon_actions, strict=True)
        )
        rows.append({
            "case": name,
            "symbol": snapshot.symbol,
            "token": masker.mapping[_symbol_root(snapshot.symbol)],
            "samples": samples,
            "named_actions": named_actions,
            "anon_actions": anon_actions,
            "named_mean_confidence": round(mean(named_conf), 1) if named_conf else None,
            "anon_mean_confidence": round(mean(anon_conf), 1) if anon_conf else None,
            "action_agreement": round(agreement, 3),
            "flagged": agreement < AGREEMENT_FLAG_THRESHOLD,
        })
    return rows
