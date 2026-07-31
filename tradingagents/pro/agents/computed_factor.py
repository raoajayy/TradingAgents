"""ComputedFactorAgent: mined factors as deterministic evidence (P3-03).

A factor that survived OOS evaluation in the mining loop becomes an
evidence agent whose claim is a *computed value*, not an opinion: the
factor's current reading over the snapshot's bars plus its recorded OOS
IC and a recent in-window IC, all attached as DataRefs the way the rules
engines do — no LLM call is involved in producing the value or the claim.

Survivors live in the event-store kv under ``MINED_FACTORS_KEY`` as a
JSON list of records; ``load_mined_factor_agents`` turns that into agents
and ``attach_mined_factors`` appends them to an existing roster team.
Both are guarded: an absent, empty, or malformed key changes nothing.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from tradingagents.contracts import (
    AgentEvidence,
    AgentTeam,
    DataRef,
    Direction,
    MarketSnapshot,
    MetricReading,
    SourceAttribution,
    SourceType,
    Timeframe,
)
from tradingagents.pro.analytics.factors import FactorExpr, factor_ic, forward_returns

logger = logging.getLogger(__name__)

MINED_FACTORS_KEY = "mined_factors"
_SOURCE_ID = "quant_engine"  # maps to SourceType.MODEL in rendering's table
_RECENT_IC_HORIZON = 5


class ComputedFactorAgent:
    """Deterministic evidence agent for one registered mined factor.

    Mirrors the ``EvidenceAgent.analyze`` signature so it can sit in the
    same team list, but the "analysis" is arithmetic: evaluate the factor
    over the snapshot bars, sign the reading by the factor's known IC, and
    cite every number. Abstains (returns None) when the bars are too few
    or the current value is NaN — an honest gap, never a fabricated read.
    """

    def __init__(self, name: str, expression: str,
                 ic_mean: float | None = None,
                 ic_ir: float | None = None,
                 timeframe: Timeframe = Timeframe.D1):
        self.name = name
        self.expression = expression
        self.expr = FactorExpr.parse(expression)  # ValueError on bad input
        self.ic_mean = ic_mean
        self.ic_ir = ic_ir
        self.timeframe = timeframe
        self.agent_id = f"factor_{name}".lower()

    def analyze(
        self,
        snapshot: MarketSnapshot,
        extra_metrics: dict[str, MetricReading] | None = None,
    ) -> AgentEvidence | None:
        bars = [b for b in snapshot.bars if b.timeframe == self.timeframe]
        if len(bars) < 2:
            return None
        df = pd.DataFrame(
            {
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
            }
        )
        try:
            series = self.expr.evaluate(df)
        except ValueError:
            logger.warning("%s: factor evaluation failed; abstaining",
                           self.agent_id, exc_info=True)
            return None
        value = series.iloc[-1]
        if pd.isna(value):
            return None  # warm-up window longer than the shown history
        value = float(value)
        recent_ic = factor_ic(
            series, forward_returns(df["close"], _RECENT_IC_HORIZON))

        # sign the raw reading by the direction the factor predicted OOS
        ic_used = self.ic_mean if self.ic_mean is not None else recent_ic
        if ic_used is None or ic_used == 0 or value == 0:
            direction = Direction.NEUTRAL
        elif value * ic_used > 0:
            direction = Direction.BULLISH
        else:
            direction = Direction.BEARISH
        # deterministic confidence: floor 35, plus up to 30 from |IC|
        confidence = 35 + min(30, int(round(abs(ic_used or 0.0) * 300)))

        refs = [
            DataRef(name=f"FACTOR_{self.name.upper()}", value=value,
                    timeframe=self.timeframe, source=_SOURCE_ID),
            DataRef(name=f"FACTOR_{self.name.upper()}_BARS_USED",
                    value=len(bars), source=_SOURCE_ID),
        ]
        claim = (f"Mined factor '{self.name}' ({self.expression}) reads "
                 f"{value:+.4f} on the latest {bars[-1].timeframe.value} bar.")
        if self.ic_mean is not None:
            refs.append(DataRef(name=f"FACTOR_{self.name.upper()}_IC_OOS",
                                value=self.ic_mean, source=_SOURCE_ID))
            claim += f" Registered OOS IC {self.ic_mean:+.3f}."
        if recent_ic is not None:
            refs.append(DataRef(name=f"FACTOR_{self.name.upper()}_IC_RECENT",
                                value=recent_ic, source=_SOURCE_ID))
            claim += (f" In-window IC over the shown bars "
                      f"{recent_ic:+.3f} (h={_RECENT_IC_HORIZON}).")
        return AgentEvidence(
            agent_id=self.agent_id,
            team=AgentTeam.QUANT,
            claim=claim,
            direction=direction,
            confidence=confidence,
            timeframe=self.timeframe,
            data_refs=refs,
            sources=[SourceAttribution(
                id=_SOURCE_ID, type=SourceType.MODEL,
                name=f"Mined factor evaluator ({self.name})")],
        )


# --- survivor registration via the event-store kv ---------------------------

def store_survivors(store, survivors: list[dict]) -> None:
    """Persist mining survivors (overwrites the key). Each record needs at
    least ``name`` and ``expression``; ``ic_mean``/``ic_ir`` ride along."""
    store.put_kv(MINED_FACTORS_KEY, json.dumps(survivors))


def load_mined_factor_agents(
    store, timeframe: Timeframe = Timeframe.D1
) -> list[ComputedFactorAgent]:
    """Build ComputedFactorAgents from the kv registry. Absent, empty, or
    malformed entries yield [] (or skip the bad record) — the roster is
    unchanged unless a valid survivor exists."""
    raw = store.get_kv(MINED_FACTORS_KEY)
    if not raw:
        return []
    try:
        records = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("kv %r holds invalid JSON; ignoring", MINED_FACTORS_KEY)
        return []
    if not isinstance(records, list):
        return []
    agents: list[ComputedFactorAgent] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        name, expression = rec.get("name"), rec.get("expression")
        if not name or not expression:
            continue
        try:
            agents.append(ComputedFactorAgent(
                name=str(name),
                expression=str(expression),
                ic_mean=rec.get("ic_mean"),
                ic_ir=rec.get("ic_ir"),
                timeframe=timeframe,
            ))
        except ValueError:
            # a record that no longer parses (whitelist tightened, corrupt
            # row) must not take the roster down
            logger.warning("mined factor %r has invalid expression; skipped", name)
    return agents


def attach_mined_factors(
    agents: list, store, timeframe: Timeframe = Timeframe.D1
) -> list:
    """Return ``agents`` plus any registered ComputedFactorAgents. With an
    empty/absent kv key this returns the input list unchanged (same
    object), so wiring it in is a strict no-op until survivors exist."""
    mined = load_mined_factor_agents(store, timeframe=timeframe)
    if not mined:
        return agents
    return [*agents, *mined]


__all__ = [
    "MINED_FACTORS_KEY",
    "ComputedFactorAgent",
    "attach_mined_factors",
    "load_mined_factor_agents",
    "store_survivors",
]
