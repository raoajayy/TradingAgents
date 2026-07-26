"""Named strategy variants (SO-D) — four ready-to-run risk profiles assembled
**only** from guard-passing Strategy-Lab presets.

Each variant is a declarative bundle: a set of component presets
(strategy × symbol × timeframe, each already in ``presets.CATALOG``), a weight
scheme, and an annualized volatility target. Nothing here fits parameters or
introduces a new engine — a variant is a *portfolio recipe* over strategies that
have each independently cleared walk-forward OOS + DSR/PBO. The runner
(``scripts/pro_variants_lab.py``) resolves a variant into component equity
streams, blends them, applies the vol target, and reports OOS Sharpe / MAR /
max-drawdown / Monte-Carlo.

The four form a deliberate risk ladder:

- **A · Conservative** — only the highest-DSR, param-stable *daily* winners;
  inverse-vol (risk parity); low 8% vol target. Smoothest ride.
- **B · Balanced (recommended)** — the six diversified daily-crypto winners
  (the Sharpe≈3.16 portfolio from 02_portfolio.md); inverse-vol; 15% vol target.
- **C · Aggressive** — pyramiding trend-followers + 4h breakouts (higher
  turnover, deeper drawdowns accepted); inverse-vol; 22% vol target.
- **D · Regime-aware** — built around ``regime_momentum_v1`` (which carries its
  own market-health gate) plus regime-diverse trend/breakout components chosen
  from the 06_regime_breakdown evidence; inverse-vol; 15% vol target.

Honesty note on D: its regime-awareness comes from (1) the regime *gate* inside
``regime_momentum_v1`` and (2) deliberately regime-diverse component selection —
NOT from a dynamic per-bar regime-switching allocator. A fully dynamic
regime-switched allocation is recorded as future work in 19_optimization_report;
claiming it here would overstate what ships.

Every component is asserted to be a real guard-passing preset at import via
``validate_variants`` (called by the test suite and the runner) — a variant can
never silently reference a cell that did not clear the bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tradingagents.pro.backtest import presets

# component key = (strategy_id, symbol, timeframe)
Component = tuple[str, str, str]


@dataclass(frozen=True)
class Variant:
    name: str                     # short id: "A".."D"
    title: str                    # human label
    risk: str                     # conservative | balanced | aggressive | regime-aware
    weight_scheme: str            # "inverse-vol" | "equal"
    vol_target: float             # annualized target (e.g. 0.15 = 15%)
    components: tuple[Component, ...]
    max_leverage: float = 3.0     # cap on vol-target scaling (honesty on leverage)
    description: str = ""
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


VARIANTS: dict[str, Variant] = {
    "A": Variant(
        name="A", title="Conservative", risk="conservative",
        weight_scheme="inverse-vol", vol_target=0.08, max_leverage=2.0,
        components=(
            ("trend_following_v2", "ETH-USD", "1d"),
            ("trend_following_v1", "ETH-USD", "1d"),
            ("volatility_breakout_v1", "ETH-USD", "1d"),
            ("volatility_breakout_v1", "SOL-USD", "1d"),
        ),
        description="Only the highest-DSR, param-stable daily winners, risk-parity "
                    "weighted at a low 8% vol target. Prioritizes a smooth equity "
                    "curve and shallow drawdowns over raw return.",
        notes="All four components share param-stability 1.0 in the walk-forward "
              "run; leverage capped at 2x.",
        tags=("daily", "risk-parity", "low-vol")),
    "B": Variant(
        name="B", title="Balanced (recommended)", risk="balanced",
        weight_scheme="inverse-vol", vol_target=0.15,
        components=(
            ("trend_following_v2", "ETH-USD", "1d"),
            ("volatility_breakout_v1", "ETH-USD", "1d"),
            ("trend_following_v1", "ETH-USD", "1d"),
            ("mean_reversion_v1", "SOL-USD", "1d"),
            ("trend_following_v2", "SOL-USD", "1d"),
            ("volatility_breakout_v1", "SOL-USD", "1d"),
        ),
        description="The six diversified daily-crypto winners across three "
                    "archetypes (trend, breakout, mean-reversion) on ETH+SOL — the "
                    "OOS-validated inverse-vol portfolio from 02_portfolio.md — at "
                    "a 15% vol target. The default recommendation.",
        notes="Reproduces the headline diversified portfolio; inverse-vol chosen "
              "because it beat sharpe-tilt out-of-sample.",
        tags=("daily", "risk-parity", "diversified", "recommended")),
    "C": Variant(
        name="C", title="Aggressive", risk="aggressive",
        weight_scheme="inverse-vol", vol_target=0.22, max_leverage=4.0,
        components=(
            ("trend_following_v2", "ETH-USD", "1d"),
            ("trend_following_v2", "SOL-USD", "1d"),
            ("trend_following_v2", "ETH-USD", "4h"),
            ("volatility_breakout_v1", "ETH-USD", "4h"),
            ("volatility_breakout_v1", "SOL-USD", "4h"),
        ),
        description="Pyramiding trend-followers plus 4h chandelier breakouts — "
                    "higher turnover and a 22% vol target for maximum return, with "
                    "deeper drawdowns explicitly accepted.",
        notes="Leans on trend_following_v2 (pyramids winners) and the 4h Chandelier "
              "breakouts (SO-C1); leverage capped at 4x — this variant is the one "
              "most exposed to the leverage caveat.",
        tags=("mixed-tf", "pyramiding", "high-vol")),
    "D": Variant(
        name="D", title="Regime-aware", risk="regime-aware",
        weight_scheme="inverse-vol", vol_target=0.15,
        components=(
            ("regime_momentum_v1", "ETH-USD", "1d"),
            ("regime_momentum_v1", "ETH-USD", "4h"),
            ("trend_following_v1", "ETH-USD", "1d"),
            ("mean_reversion_v1", "SOL-USD", "1d"),
        ),
        description="Built around regime_momentum_v1 (carries its own market-health "
                    "gate) plus regime-diverse trend and mean-reversion components — "
                    "a trend engine for directional regimes, mean-reversion for "
                    "ranging ones — inverse-vol at 15% vol.",
        notes="Regime-awareness comes from the components' own gates + regime-diverse "
              "selection (06_regime_breakdown), NOT a dynamic per-bar switching "
              "allocator (recorded as future work). No overclaim.",
        tags=("regime-gated", "mixed-tf", "diversified")),
}


def validate_variants() -> None:
    """Assert every variant references only shipped, guard-passing presets and
    uses a known weight scheme with a sane vol target. Raises on any violation
    (the test suite + runner call this)."""
    for v in VARIANTS.values():
        assert v.weight_scheme in ("inverse-vol", "equal"), \
            f"variant {v.name}: unknown weight scheme {v.weight_scheme!r}"
        assert 0.0 < v.vol_target <= 1.0, \
            f"variant {v.name}: implausible vol target {v.vol_target}"
        assert v.components, f"variant {v.name}: no components"
        for (sid, sym, tf) in v.components:
            preset = presets.CATALOG.get(sid, {}).get((sym, tf))
            assert preset is not None, (
                f"variant {v.name}: component {sid} {sym} {tf} is not a shipped "
                "preset (would bypass the guard bar)")


def list_variants() -> list[Variant]:
    return [VARIANTS[k] for k in sorted(VARIANTS)]


__all__ = ["Component", "VARIANTS", "Variant", "list_variants", "validate_variants"]
