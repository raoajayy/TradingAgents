"""GoldVolContextAgent: gold IV context as deterministic evidence (P3-09).

Mirrors ``computed_factor.ComputedFactorAgent`` mechanics: same
``analyze(snapshot, extra_metrics)`` signature as EvidenceAgent so it can
sit in the same team list, but the claim is assembled in code from the
snapshot's GVZ readings (level, rank, percentile, IV-RV spread) — no LLM
call is involved in producing the values or the interpretation.

The interpretation rule is a single deterministic line: IV rank above 80
means gold options are expensive against their own trailing year (below 20
means cheap); the IV-RV spread is quoted as the vol-risk-premium *proxy*
it is. Direction is always NEUTRAL — vol richness is a pricing statement,
not a price-direction call — and confidence is a fixed schedule, higher at
rank extremes where the statement carries information.

Gold only: the agent abstains on any non-GOLD snapshot, and
``attach_gold_vol_context`` only appends it for GOLD, so the crypto roster
never carries it (DVOL context stays with the ``implied_volatility`` spec).
"""

from __future__ import annotations

from tradingagents.contracts import (
    AgentEvidence,
    AgentTeam,
    AssetClass,
    DataRef,
    Direction,
    MarketSnapshot,
    MetricReading,
    SourceAttribution,
    SourceType,
    Timeframe,
)

_SOURCE_ID = "gold_vol_context"
RANK_EXPENSIVE = 80.0
RANK_CHEAP = 20.0

_METRIC_NAMES = ("GOLD_VOL_INDEX", "GOLD_VOL_INDEX_CHANGE_1D",
                 "GOLD_IV_RANK", "GOLD_IV_PERCENTILE", "GOLD_IV_RV_SPREAD")


class GoldVolContextAgent:
    """Deterministic gold implied-vol context evidence.

    Abstains (returns None) when the snapshot is not gold or carries no
    GVZ level — an honest gap, never a fabricated read.
    """

    agent_id = "gold_vol_context"
    team = AgentTeam.MACRO

    def __init__(self, timeframe: Timeframe = Timeframe.D1):
        # GVZ and the realized-vol proxy are daily series; D1 is the
        # honest frame regardless of the run timeframe.
        self.timeframe = timeframe

    def analyze(
        self,
        snapshot: MarketSnapshot,
        extra_metrics: dict[str, MetricReading] | None = None,
    ) -> AgentEvidence | None:
        if snapshot.asset is not AssetClass.GOLD:
            return None
        readings: dict[str, MetricReading] = {
            r.name: r for r in [*snapshot.macro, *snapshot.onchain]
        }
        if extra_metrics:
            readings.update(extra_metrics)
        level = readings.get("GOLD_VOL_INDEX")
        if level is None:
            return None
        rank = readings.get("GOLD_IV_RANK")
        percentile = readings.get("GOLD_IV_PERCENTILE")
        spread = readings.get("GOLD_IV_RV_SPREAD")

        refs = [
            DataRef(name=name, value=readings[name].value,
                    source=_SOURCE_ID, as_of=readings[name].as_of)
            for name in _METRIC_NAMES if name in readings
        ]

        parts = [f"GVZ at {float(level.value):.1f}"]
        if rank is not None:
            parts.append(f"IV rank {float(rank.value):.0f}/100 over the "
                         f"trailing year")
        if percentile is not None:
            parts.append(f"IV percentile {float(percentile.value):.0f}")
        if spread is not None:
            parts.append(f"IV-RV spread {float(spread.value):+.1f} vol pts "
                         f"(GVZ vs 20d Parkinson realized — a vol-risk-"
                         f"premium proxy, not an options surface)")
        claim = "; ".join(parts) + "."

        # one-line deterministic interpretation rule
        confidence = 35
        if rank is not None:
            rank_value = float(rank.value)
            if rank_value > RANK_EXPENSIVE:
                claim += (" Rank above 80: gold options are expensive "
                          "versus their own 1y history.")
                confidence = 60
            elif rank_value < RANK_CHEAP:
                claim += (" Rank below 20: gold options are cheap versus "
                          "their own 1y history.")
                confidence = 60
            else:
                claim += (" Rank mid-range: implied vol is unremarkable "
                          "versus its own 1y history.")
                confidence = 45

        return AgentEvidence(
            agent_id=self.agent_id,
            team=self.team,
            claim=claim,
            direction=Direction.NEUTRAL,  # pricing statement, not a call
            confidence=confidence,
            timeframe=self.timeframe,
            data_refs=refs,
            sources=[SourceAttribution(
                id=_SOURCE_ID, type=SourceType.MARKET_DATA,
                name="CBOE GVZ (Yahoo) + computed IV rank/percentile and "
                     "Parkinson realized-vol spread")],
        )


def attach_gold_vol_context(agents: list, snapshot: MarketSnapshot) -> list:
    """Return ``agents`` plus the vol-context agent — for GOLD snapshots
    only. Any other asset returns the input list unchanged (same object),
    so wiring it into the macro team node is a strict no-op for crypto."""
    if snapshot.asset is not AssetClass.GOLD:
        return agents
    return [*agents, GoldVolContextAgent()]


__all__ = ["GoldVolContextAgent", "attach_gold_vol_context",
           "RANK_CHEAP", "RANK_EXPENSIVE"]
