"""Shared order construction for the native path (F7).

The single-symbol ``BacktestEngine`` and the multi-symbol ``PortfolioEngine``
both turn an ``OrderIntent`` into a ``PendingOrder`` with identical sizing and
the same 15-field bracket/algo/reduce-only/OCO/iceberg wiring. These two
helpers are the single source of truth for that translation so the two engines
stay in lockstep (previously they were duplicated and every new PendingOrder
field had to be mirrored by hand). The engines keep their own differences
around these: the portfolio engine layers a per-symbol allocator budget cap and
returns a rejection reason, the single-symbol engine does neither.
"""

from __future__ import annotations

import uuid

from tradingagents.pro.backtest.broker import PendingOrder
from tradingagents.pro.backtest.execution import schedule_for


def size_intent(intent, equity: float, ref_close: float,
                max_position_pct: float) -> float | None:
    """Resolve an intent's quantity: an explicit ``quantity`` wins, else
    equity-aware fixed-risk sizing off the entry reference (limit/stop price,
    else the decision bar's close) and the bracket stop. Returns None when
    risk_pct sizing is requested without a bracket stop (nothing to submit)."""
    if intent.quantity is not None:
        return intent.quantity
    stop_loss = intent.bracket.stop_loss if intent.bracket else None
    if intent.risk_pct is not None and stop_loss is not None:
        from tradingagents.pro.analytics.risk import fixed_risk_position_size

        entry_ref = intent.limit_price or intent.stop_price or ref_close
        return fixed_risk_position_size(
            equity, intent.risk_pct, entry=entry_ref, stop=stop_loss,
            max_position_pct=max_position_pct).quantity
    return None


def build_pending_order(intent, order_id: str, quantity: float, symbol: str,
                        submitted_index: int) -> PendingOrder:
    """Construct the PendingOrder for one intent — the lockstep bracket + algo +
    reduce-only/OCO/iceberg wiring both engines share. Schedule reflects the
    (possibly allocator-trimmed) ``quantity`` passed in."""
    bracket = intent.bracket
    return PendingOrder(
        id=order_id, kind=intent.kind, side=intent.side, quantity=quantity,
        limit_price=intent.limit_price, stop_price=intent.stop_price,
        stop_loss=bracket.stop_loss if bracket else None,
        take_profits=list(bracket.take_profits) if bracket else [],
        trailing_mode=bracket.trailing if bracket else None,
        trailing_mult=bracket.trailing_mult if bracket else None,
        trailing_period=bracket.trailing_period if bracket else None,
        reduce_only=intent.reduce_only,
        oco_group=intent.oco_group,
        display_qty=intent.display_qty,
        schedule=schedule_for(intent.algo, intent.algo_bars,
                              intent.volume_profile, quantity),
        symbol=symbol, submitted_index=submitted_index, tag=intent.tag)


def new_order_id(intent, i: int, symbol: str | None = None) -> str:
    """Deterministic-shape order id: ``{symbol-}{bar}-{tag|uuid}`` (uuid only
    when the intent carries no tag, matching the prior per-engine format)."""
    suffix = intent.tag or uuid.uuid4().hex[:8]
    return f"{symbol}-{i}-{suffix}" if symbol is not None else f"{i}-{suffix}"


__all__ = ["build_pending_order", "new_order_id", "size_intent"]
