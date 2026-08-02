"""View models: JSON-serializable projections for the dashboard.

Pure functions over RunRecords, ProMemory, and BacktestResults — fully
testable without FastAPI. Every recommendation view renders the complete
TradeRecommendation schema (Phase 0 requirement): action, confidence,
levels, ladder, size, regime, evidence, counterarguments, vote breakdown,
historical analogs, and the derived risk/reward.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from tradingagents.contracts import TradeAction, TradeRecommendation
from tradingagents.pro.backtest import BacktestResult
from tradingagents.pro.dashboard.recorder import RunRecord
from tradingagents.pro.memory import MemoryKind, ProMemory


def market_overview(run: RunRecord | None) -> dict:
    if run is None:
        return {"status": "no runs yet"}
    summary = run.snapshot_summary()
    summary.update({
        "run_id": run.run_id,
        "started_at": run.started_at.isoformat(),
        "execution_status": run.state.get("execution_status"),
        "rejected_at": run.rejection and run.rejection.get("stage"),
    })
    return summary


def _mark_for(symbol: str, ticks, marketdata) -> tuple[float | None, str]:
    """Best available mark price with an honest provenance label:
    live tick > latest daily close (eod) > none (falls back to entry)."""
    if ticks is not None:
        cached = ticks.get(symbol)
        if cached is not None:
            return cached[0], "live"
    if marketdata is not None:
        try:
            bars = marketdata.get_bars(symbol, "1d", limit=1)
            if bars:
                return bars[-1].close, "eod"
        except Exception:  # degraded vendor must not break /api/status
            pass
    return None, "entry"


def _open_stops(memory) -> dict[str, float]:
    """Symbol -> stop_loss of the trade that OPENED the current position.

    A position is opened by exactly one TRADE until it closes (its id then
    appears as an OUTCOME.ref_id). The stop the position was opened with is
    the latest still-open TRADE's payload stop — the honest stop line, not
    a fabricated trailing level (the system doesn't trail)."""
    if memory is None:
        return {}
    try:
        from tradingagents.pro.memory import MemoryKind

        closed = {o.ref_id for o in memory.records(MemoryKind.OUTCOME)}
        stops: dict[str, float] = {}
        for trade in memory.records(MemoryKind.TRADE):  # oldest→newest
            if trade.id in closed or trade.symbol is None:
                continue
            stop = trade.payload.get("stop_loss")
            if stop is not None:
                stops[trade.symbol] = stop  # latest open trade wins
        return stops
    except Exception:
        return {}


def open_positions_view(router, equity: float | None,
                        ticks=None, marketdata=None,
                        memory=None) -> tuple[list[dict], float | None]:
    """Positions with entry/mark/unrealized P&L (trader review G2) + the
    opening stop level (PC.1).

    Entry comes from the venue adapter's book (avg_price); mark from the
    tick cache or daily close, labeled. Anything unknowable is null —
    never a fabricated number.
    """
    entries: dict[str, float] = {}
    adapter = getattr(router, "adapter", None)
    if adapter is not None:
        try:
            for pos in adapter.positions():
                entries[pos.symbol] = pos.avg_price
        except Exception:
            pass
    stops = _open_stops(memory)
    positions: list[dict] = []
    unrealized_total: float | None = None
    for symbol, quantity in sorted(router.local_book.items()):
        entry = entries.get(symbol)
        mark, mark_source = _mark_for(symbol, ticks, marketdata)
        if mark is None and entry is not None:
            mark = entry
        unrealized = (
            (mark - entry) * quantity
            if mark is not None and entry is not None and mark_source != "entry"
            else None
        )
        if unrealized is not None:
            unrealized_total = (unrealized_total or 0.0) + unrealized
        exposure_pct = (
            abs(quantity * mark) / equity * 100.0
            if mark is not None and equity else None
        )
        positions.append({
            "symbol": symbol,
            "quantity": quantity,
            "entry_price": entry,
            "mark_price": mark,
            "mark_source": mark_source,
            "unrealized_pnl": unrealized,
            "exposure_pct": exposure_pct,
            "stop_price": stops.get(symbol),
        })
    return positions, unrealized_total


def system_status(router, equity: float | None = None, arming=None,
                  ticks=None, marketdata=None, memory=None) -> dict:
    """Kill switch, circuit breaker, open book, and per-pair arming
    (UX review RISK-01; go-live Phase 4).

    ``router`` is an ExecutionRouter or None (dashboard attached to a
    replay/monitor-only state). Read-only: reset stays an operator action.
    ``arming`` is an ArmingStore or None; absent = every pair is PAPER.
    """
    arming_view = arming.status() if arming is not None else {}
    live_armed = any(v["tier"] in ("canary", "live")
                     for v in arming_view.values())
    if router is None:
        return {"attached": False, "trading_halted": None,
                "arming": arming_view, "live_armed": live_armed}
    breaker = router.breaker.check()
    engaged = router.kill_switch.engaged
    positions, unrealized_total = open_positions_view(
        router, equity, ticks=ticks, marketdata=marketdata, memory=memory)
    return {
        "attached": True,
        "kill_switch": {"engaged": engaged, "reason": router.kill_switch.reason},
        "circuit_breaker": {"tripped": breaker.tripped, "reason": breaker.reason},
        "open_positions": positions,
        "unrealized_total": unrealized_total,
        "equity": equity,
        "trading_halted": engaged or breaker.tripped,
        "arming": arming_view,
        "live_armed": live_armed,
    }


def alert_feed(runs: Sequence[RunRecord], limit: int = 50) -> dict:
    """Operational events derived from run records, newest first (ALERT-02).

    severity: critical = security (quarantined injection), warning = a
    stage refused the trade, info = degraded inputs the agents disclosed.
    """
    alerts: list[dict] = []
    for run in runs:
        def add(severity: str, text: str, key: str | None = None,
                run=run) -> None:
            alerts.append({
                "time": run.started_at.isoformat(),
                "run_id": run.run_id,
                "severity": severity,
                "text": text,
                # coalescing identity: rejections keyed by stage so five
                # hourly event-gate refusals (whose countdown text varies)
                # merge into one entry
                "_key": key or text,
            })

        snapshot = run.state.get("snapshot")
        for feed in (snapshot.missing_feeds if snapshot else []):
            if feed.startswith("news:quarantined"):
                add("critical", f"suspected prompt injection quarantined ({feed})")
            else:
                add("info", f"feed unavailable: {feed}")
        if run.rejection:
            stage = run.rejection.get("stage")
            reasons = "; ".join(str(r) for r in run.rejection.get("reasons", []))
            add("warning",
                f"trade rejected at {stage}" + (f": {reasons}" if reasons else ""),
                key=f"rejected@{stage}")
        execution_status = run.state.get("execution_status") or ""
        if execution_status.startswith("blocked:"):
            add("warning", f"execution {execution_status}")
    alerts.reverse()
    # coalesce consecutive same-key events (trader review: five hourly
    # event-gate rejections stacked as near-duplicate warnings) — keep the
    # newest text/time, count the occurrences
    deduped: list[dict] = []
    for alert in alerts:
        prev = deduped[-1] if deduped else None
        if (prev is not None and prev["severity"] == alert["severity"]
                and prev["_key"] == alert["_key"]):
            prev["count"] += 1
            continue
        deduped.append({**alert, "count": 1})
    for alert in deduped:
        del alert["_key"]
    return {"alerts": deduped[:limit]}


def recommendation_view(rec: TradeRecommendation | None,
                        invalidation: str | None = None,
                        rejection: dict | None = None) -> dict:
    if rec is None:
        if rejection:  # EXPL-01: a rejected run explains itself
            return {"status": "rejected", "rejection": rejection}
        return {"status": "no recommendation"}
    view = rec.model_dump(mode="json")  # the full Phase 0 schema, verbatim
    view["vote_tally"] = {
        action.value: count for action, count in rec.vote_breakdown.tally().items()
    }
    view["n_evidence"] = len(rec.evidence)
    view["n_counterarguments"] = len(rec.counterarguments)
    # the reflection stage's falsifiability condition (UX review EXPL-02)
    view["invalidation"] = invalidation
    view["rejection"] = rejection
    return view


def debate_timeline(run: RunRecord) -> dict:
    return {
        "run_id": run.run_id,
        "node_sequence": list(run.node_sequence),
        # per-node latency (parallel to node_sequence); [] for runs
        # persisted before timing existed — the UI then omits latency
        "node_times": list(run.node_times),
        # honest execution outcome, e.g. "accepted:paper" / "rejected:risk_gate"
        "execution_status": run.state.get("execution_status"),
        "entries": [
            {
                "speaker": e["speaker"],
                "stance": e.get("stance"),
                "confidence": e.get("confidence"),
                "argument": e["argument"],
                "cited": e.get("cited", []),
            }
            for e in run.debate
        ],
        "rejection": run.rejection,
    }


def evidence_panels(run: RunRecord) -> dict:
    panels = {}
    for team, evidence in run.state.get("evidence_by_team", {}).items():
        panels[team] = [
            {
                "agent_id": e.agent_id,
                "direction": e.direction.value,
                "confidence": e.confidence,
                "claim": e.claim,
                "data_refs": [
                    {"name": r.name, "value": r.value} for r in e.data_refs
                ],
                "sources": [s.id for s in e.sources],
            }
            for e in evidence
        ]
    return panels


def _linked_trade_and_outcome(run: RunRecord, memory: ProMemory):
    """The (TRADE, OUTCOME) memory records for a run's recommendation, or
    (None, None). The journal's linkage: the pipeline writes a TRADE record
    carrying ``payload.recommendation_id``; closing it writes an OUTCOME
    whose ``ref_id`` is that TRADE record's id."""
    rec = run.recommendation
    if rec is None or memory is None:
        return None, None
    trade = memory.find_trade_by_recommendation(rec.id)
    if trade is None:
        return None, None
    outcome = next((o for o in memory.records(MemoryKind.OUTCOME)
                    if o.ref_id == trade.id), None)
    return trade, outcome


def decision_export_pack(run: RunRecord, memory: ProMemory) -> dict:
    """P3-06 decision-audit export pack: the complete reviewable record of
    ONE run, assembled from the existing tested view helpers — snapshot
    inputs, full debate transcript, gate results, the recommendation (or
    rejection), execution outcome, the graded result from memory,
    calibration context, and the P3-07 version stamp. Everything comes
    from the event store; nothing is recomputed or invented."""
    from tradingagents.contracts import utc_now

    snapshot = run.state.get("snapshot")
    bars = snapshot.bars if snapshot is not None else []
    inputs = run.snapshot_summary() if snapshot is not None else {}
    inputs["bar_range"] = {
        "first": bars[0].start.isoformat() if bars else None,
        "last": bars[-1].start.isoformat() if bars else None,
    }
    reflection = run.state.get("reflection") or {}
    rec = run.recommendation
    trade, outcome = _linked_trade_and_outcome(run, memory)
    return {
        "pack_format": 1,
        "generated_at": utc_now().isoformat(),
        "run_id": run.run_id,
        "symbol": run.symbol,
        "asset": run.asset,
        "started_at": run.started_at.isoformat(),
        "trigger": run.trigger,
        "timeframe": run.timeframe,
        # P3-07 provenance stamp; None on pre-stamp runs
        "versions": run.versions,
        "snapshot": inputs,
        "transcript": debate_timeline(run),
        "evidence": evidence_panels(run),
        "gates": run.state.get("gate_results") or {},
        "recommendation": recommendation_view(
            rec, invalidation=reflection.get("invalidation"),
            rejection=run.rejection),
        "execution": {
            # honest venue verdict, e.g. "accepted:paper" /
            # "rejected:order (kill_switch: …)"; None on pre-execution runs
            "execution_status": run.state.get("execution_status"),
            # the order geometry the pipeline committed to (entry/stop/TPs)
            "order": ({"trade_record_id": trade.id, **trade.payload}
                      if trade is not None else None),
        },
        # graded result once the trade closed: pnl/won plus the venue truth
        # (mode, commission, venue_order_id, fill_price, TCA) when present
        "outcome": ({"closed_at": outcome.created_at.isoformat(),
                     **outcome.payload}
                    if outcome is not None else None),
        # empirical p(win) at this ticket's confidence; None below the
        # sample floor — never an invented number
        "calibration": (estimate_p_win(memory, rec.confidence)
                        if rec is not None else None),
    }


def trade_journal(memory: ProMemory) -> dict:
    trades = {r.id: r for r in memory.records(MemoryKind.TRADE)}
    entries = []
    total_pnl = 0.0
    by_mode: dict[str, dict] = {}
    for outcome in memory.records(MemoryKind.OUTCOME):
        trade = trades.get(outcome.ref_id)
        # venue truth + arming mode ride in the outcome payload (Phase 5);
        # paper trades default to mode "paper" with zero venue fields
        mode = outcome.payload.get("mode", "paper")
        if mode == "retro":
            # retro-scored predictions feed calibration, never the blotter
            continue
        pnl = outcome.payload.get("pnl", 0.0)
        total_pnl += pnl
        won = outcome.payload.get("won")
        entries.append({
            "symbol": outcome.symbol,
            "action": trade.payload.get("action") if trade else None,
            "regime": trade.payload.get("regime") if trade else None,
            "pnl": pnl,
            "won": won,
            "closed_at": outcome.created_at.isoformat(),
            "mode": mode,
            "commission": outcome.payload.get("commission", 0.0),
            "venue_order_id": outcome.payload.get("venue_order_id", ""),
            "fill_price": outcome.payload.get("fill_price"),
            "entry_price": outcome.payload.get("entry_price"),
        })
        bucket = by_mode.setdefault(mode, {"n_trades": 0, "wins": 0,
                                           "total_pnl": 0.0})
        bucket["n_trades"] += 1
        bucket["wins"] += 1 if won else 0
        bucket["total_pnl"] += pnl
    for bucket in by_mode.values():
        bucket["win_rate"] = (bucket["wins"] / bucket["n_trades"]
                              if bucket["n_trades"] else None)
    wins = sum(1 for e in entries if e["won"])
    return {
        "entries": entries,
        "total_pnl": total_pnl,
        "n_trades": len(entries),
        "win_rate": wins / len(entries) if entries else None,
        "by_mode": by_mode,
    }


def estimate_p_win(memory: ProMemory, confidence: int | None,
                   band: int = 10, min_n: int = 5) -> dict | None:
    """Empirical win probability for a ticket, from the system's own
    scored record (lived + retro outcomes). Prefers outcomes whose stated
    confidence sat within ``band`` of this ticket's; falls back to the
    overall record. Returns None below ``min_n`` — no invented numbers."""
    trades = {r.id: r for r in memory.records(MemoryKind.TRADE)}
    banded: list[bool] = []
    overall: list[bool] = []
    holds: list[float] = []
    for outcome in memory.records(MemoryKind.OUTCOME):
        trade = trades.get(outcome.ref_id)
        if trade is None:
            continue
        won = bool(outcome.payload.get("won"))
        overall.append(won)
        held = (outcome.created_at - trade.created_at).total_seconds()
        if held > 0:
            holds.append(held)
        conf = trade.payload.get("confidence")
        if (confidence is not None and conf is not None
                and abs(conf - confidence) <= band):
            banded.append(won)
    import statistics

    median_hold_s = statistics.median(holds) if holds else None
    if len(banded) >= min_n:
        return {"p_win": sum(banded) / len(banded), "n": len(banded),
                "basis": f"confidence ±{band}",
                "median_hold_s": median_hold_s}
    if len(overall) >= min_n:
        return {"p_win": sum(overall) / len(overall), "n": len(overall),
                "basis": "all scored decisions",
                "median_hold_s": median_hold_s}
    return None


def journal_performance(memory: ProMemory,
                        starting_equity: float = 100_000.0) -> dict:
    """Live-book performance over CLOSED trades (trader review): equity
    curve reconstructed from realized PnLs plus the shared deterministic
    metrics. Sharpe/Sortino are deliberately omitted — per-trade curves
    have no time basis, and an annualized ratio over a handful of trades
    would be the kind of number this product refuses to fake."""
    from types import SimpleNamespace

    from tradingagents.pro.backtest.metrics import (
        max_drawdown,
        performance_report,
    )

    journal = trade_journal(memory)
    entries = sorted(journal["entries"], key=lambda e: e["closed_at"])
    curve = [starting_equity]
    for entry in entries:
        curve.append(curve[-1] + entry["pnl"])
    trades = [SimpleNamespace(pnl=e["pnl"]) for e in entries]
    report = performance_report(curve, trades)
    # P1-04: realized entry slippage vs the modeled assumption
    slips = [o.payload["tca"]["entry_slippage_bps"]
             for o in memory.records(MemoryKind.OUTCOME)
             if o.payload.get("mode") != "retro"
             and isinstance(o.payload.get("tca"), dict)
             and o.payload["tca"].get("entry_slippage_bps") is not None]
    return {
        "avg_entry_slippage_bps": (sum(slips) / len(slips)) if slips else None,
        "n_slippage_samples": len(slips),
        "equity_curve": curve,
        "n_trades": report.n_trades,
        "win_rate": journal["win_rate"],
        "total_pnl": journal["total_pnl"],
        "expectancy": report.expectancy,
        "profit_factor": (None if report.profit_factor == float("inf")
                          else report.profit_factor),
        "max_drawdown": max_drawdown(curve),
        "total_return": report.total_return,
        "starting_equity": starting_equity,
    }


def portfolio_exposure(positions: list[dict], equity: float | None,
                       max_open_positions: int, marketdata=None) -> dict:
    """Aggregate book risk (trader review): net/gross exposure, direction
    split, concentration. Pure arithmetic over open_positions_view rows —
    unknown marks contribute nothing rather than a fabricated number.
    P2-05: with ``marketdata`` attached, also the parametric 1-day 99%
    book VaR (``portfolio_var_pct``, null whenever any priced position
    lacks return history — a partial VaR would be a lie)."""
    long_notional = 0.0
    short_notional = 0.0
    largest = 0.0
    priced = 0
    signed_notionals: dict[str, float] = {}
    for pos in positions:
        mark = pos.get("mark_price")
        qty = pos.get("quantity") or 0.0
        if mark is None or pos.get("mark_source") == "entry":
            continue
        notional = qty * mark
        priced += 1
        symbol = pos.get("symbol")
        if symbol:
            signed_notionals[symbol] = signed_notionals.get(symbol, 0.0) + notional
        if notional >= 0:
            long_notional += notional
        else:
            short_notional += -notional
        largest = max(largest, abs(notional))
    gross = long_notional + short_notional
    net = long_notional - short_notional
    def pct(x: float) -> float | None:
        return x / equity * 100.0 if equity else None
    return {
        "n_positions": len(positions),
        "n_priced": priced,
        "max_open_positions": max_open_positions,
        "gross_exposure_pct": pct(gross),
        "net_exposure_pct": pct(net),
        "long_exposure_pct": pct(long_notional),
        "short_exposure_pct": pct(short_notional),
        "largest_position_pct": pct(largest),
        "portfolio_var_pct": _book_var_pct(signed_notionals, equity, marketdata),
    }


def _book_var_pct(signed_notionals: dict[str, float], equity: float | None,
                  marketdata) -> float | None:
    """Parametric 1-day 99% VaR of the priced book as percent of equity
    (P2-05). Null — never a fabricated number — when marketdata is not
    attached, the book is empty, or any priced position lacks enough
    return history for the covariance."""
    from tradingagents.contracts import Timeframe
    from tradingagents.pro.analytics.risk import portfolio_var, returns_covariance

    if marketdata is None or not equity or not signed_notionals:
        return None
    bars_by_symbol: dict[str, list] = {}
    for symbol in signed_notionals:
        try:
            bars_by_symbol[symbol] = marketdata.get_bars(
                symbol, Timeframe.D1, limit=45)
        except Exception:
            return None  # one unmeasurable leg makes the whole VaR dishonest
    result = returns_covariance(bars_by_symbol)
    if result is None:
        return None
    symbols, cov = result
    if set(symbols) != set(signed_notionals):
        return None
    weights = [signed_notionals[s] / equity for s in symbols]
    var = portfolio_var(weights, cov)
    return var * 100.0 if var is not None else None


def risk_budget(router) -> dict:
    """Today's realized loss vs the enforced daily limit (trader review:
    'the single most important prop-desk risk control' must be VISIBLE,
    not just enforced). Read-only view over the CircuitBreaker state."""
    if router is None or getattr(router, "breaker", None) is None:
        return {"attached": False}
    breaker = router.breaker
    state = breaker.check()
    limit_usd = breaker.equity_base * breaker.limits.max_daily_loss_pct / 100
    used_usd = max(0.0, -breaker.daily_pnl)
    return {
        "attached": True,
        "daily_pnl": breaker.daily_pnl,
        "daily_loss_limit_pct": breaker.limits.max_daily_loss_pct,
        "daily_loss_limit_usd": limit_usd,
        "daily_loss_used_usd": used_usd,
        "daily_loss_used_pct_of_budget": (
            used_usd / limit_usd * 100.0 if limit_usd else None),
        "consecutive_losses": breaker.consecutive_losses,
        "consecutive_loss_limit": breaker.limits.circuit_breaker_consecutive_losses,
        "max_orders_per_day": getattr(breaker.limits, "max_orders_per_day", None),
        "tripped": state.tripped,
        "reason": state.reason,
    }


def backtest_view(result: BacktestResult | None, monte_carlo=None) -> dict:
    if result is None:
        return {"status": "no backtest yet"}
    view = {
        "report": result.report.as_dict(),
        "final_equity": result.final_equity,
        "decisions": result.decisions,
        "executed": result.executed,
        "rejections": dict(result.rejections),
        "equity_curve": list(result.equity_curve),
        "n_trades": len(result.trades),
    }
    if monte_carlo is not None:
        view["monte_carlo"] = {
            "final_equity_p5": monte_carlo.final_equity_p5,
            "final_equity_p50": monte_carlo.final_equity_p50,
            "final_equity_p95": monte_carlo.final_equity_p95,
            "max_drawdown_p95": monte_carlo.max_drawdown_p95,
            "prob_loss": monte_carlo.prob_loss,
        }
    return view


def memory_insights(memory: ProMemory) -> dict:
    counts = defaultdict(int)
    for record in memory.records():
        counts[record.kind.value] += 1
    lessons = memory.records(MemoryKind.MISTAKE) + memory.records(
        MemoryKind.WINNING_PATTERN
    )
    lessons.sort(key=lambda r: r.created_at, reverse=True)
    return {
        "counts": dict(counts),
        "recent_lessons": [
            {"kind": r.kind.value, "text": r.text} for r in lessons[:10]
        ],
    }


def brier_summary(memory: ProMemory) -> dict:
    """Brier score of stated ticket confidence vs realized outcomes
    (P1-08). The literature is blunt: verbalized confidence is RLHF tone
    (arXiv:2410.09724) — this number says whether OURS means anything."""
    trades = {r.id: r for r in memory.records(MemoryKind.TRADE)}
    sq_err = []
    for outcome in memory.records(MemoryKind.OUTCOME):
        trade = trades.get(outcome.ref_id)
        conf = trade.payload.get("confidence") if trade else None
        won = outcome.payload.get("won")
        if conf is None or won is None:
            continue
        sq_err.append((conf / 100.0 - (1.0 if won else 0.0)) ** 2)
    return {
        "brier": sum(sq_err) / len(sq_err) if sq_err else None,
        "n": len(sq_err),
        # coin-flip-at-stated-confidence reference for the UI caption
        "reference_always_half": 0.25,
    }


def calibration_report(memory: ProMemory) -> dict:
    """P3-11 public calibration view: the brier summary plus empirical
    p(win) per stated-confidence bucket (fixed 20-point buckets). Same
    source of truth as brier_summary — trade records paired with scored
    outcomes; nothing invented (empty buckets report p_win None)."""
    edges = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    tallies = [[0, 0] for _ in edges]  # [n, wins] per bucket
    trades = {r.id: r for r in memory.records(MemoryKind.TRADE)}
    for outcome in memory.records(MemoryKind.OUTCOME):
        trade = trades.get(outcome.ref_id)
        conf = trade.payload.get("confidence") if trade else None
        won = outcome.payload.get("won")
        if conf is None or won is None:
            continue
        index = min(int(conf) // 20, len(edges) - 1)
        tallies[index][0] += 1
        tallies[index][1] += 1 if won else 0
    buckets = [
        {"confidence_lo": lo, "confidence_hi": hi, "n": n,
         "p_win": wins / n if n else None}
        for (lo, hi), (n, wins) in zip(edges, tallies, strict=True)
    ]
    return {**brier_summary(memory), "buckets": buckets}


def public_track_record(runs: Sequence[RunRecord], memory: ProMemory,
                        limit: int = 100) -> dict:
    """P4-02 public live track record: the pre-registered decisions ledger
    plus honest aggregates. Contamination-proof by construction — each row
    carries ``started_at`` (the pre-registration timestamp) and the P3-07
    ``versions`` stamp (git_sha/prompt_hash existed BEFORE the outcome), so
    a reader can verify the decision could not have been fitted to what
    happened next.

    Honesty rules (non-negotiable, tested):
    - outcomes come ONLY from the trade journal (mode != "retro"; never a
      retro-adjusted grade, never a recomputed number);
    - open positions appear as ``open: true`` rows — no survivorship
      trimming while a trade is still in flight;
    - rejected runs stay in the ledger (``rejected_at`` names the gate) —
      a rejection IS a decision;
    - every aggregate ships its own sample size, and reports null rather
      than an invented value below n=1.
    """
    trades_by_rec: dict[str, object] = {}
    for record in memory.records(MemoryKind.TRADE):
        rec_id = record.payload.get("recommendation_id")
        if rec_id:
            trades_by_rec[rec_id] = record
    # journal outcomes only: retro-scored grades feed calibration, never
    # this ledger (same rule as trade_journal)
    outcomes_by_trade = {
        o.ref_id: o for o in memory.records(MemoryKind.OUTCOME)
        if o.payload.get("mode", "paper") != "retro"
    }

    ledger = []
    graded: list[dict] = []       # {won, r} rows for the aggregates
    n_rejected = n_open = 0
    for run in reversed(runs[-max(1, int(limit)):]):
        rec = run.recommendation
        trade = trades_by_rec.get(rec.id) if rec else None
        outcome = outcomes_by_trade.get(trade.id) if trade else None
        rejected_at = run.rejection and run.rejection.get("stage")
        is_open = trade is not None and outcome is None
        row = {
            "run_id": run.run_id,
            "symbol": run.symbol,
            "started_at": run.started_at.isoformat(),
            "action": rec.action.value if rec else None,
            "confidence": rec.confidence if rec else None,
            # the contamination proof: stamped at run time, pre-outcome
            "versions": run.versions,
            "rejected_at": rejected_at,
            "open": is_open,
            "outcome": None,
        }
        if rejected_at:
            n_rejected += 1
        if is_open:
            n_open += 1
        if outcome is not None:
            row["outcome"] = {
                "pnl": outcome.payload.get("pnl"),
                "won": outcome.payload.get("won"),
                "closed_at": outcome.payload.get(
                    "closed_at", outcome.created_at.isoformat()),
            }
            graded.append({
                "won": bool(outcome.payload.get("won")),
                "r": _realized_r(trade, outcome),
            })
        ledger.append(row)

    wins = sum(1 for g in graded if g["won"])
    r_values = [g["r"] for g in graded if g["r"] is not None]
    return {
        "ledger": ledger,
        "aggregates": {
            "n_decisions": len(ledger),
            "n_rejected": n_rejected,
            "n_open": n_open,
            "n_graded": len(graded),
            "win_rate": (wins / len(graded)) if graded else None,
            "win_rate_n": len(graded),
            "avg_r": (sum(r_values) / len(r_values)) if r_values else None,
            "avg_r_n": len(r_values),
            # reuses the P3-11 calibration view verbatim (brier + buckets,
            # each bucket with its own n; empty buckets report p_win null)
            "calibration": calibration_report(memory),
        },
        "methodology": {
            "pre_registered": "every decision is timestamped and version-"
                              "stamped (git_sha, prompt_hash) at run time, "
                              "before its outcome exists",
            "graded_post_hoc": "outcomes come only from the trade journal "
                               "as positions close; never retro-adjusted",
            "rejections_included": "runs rejected by a gate stay in the "
                                   "ledger and the decision count",
            "open_positions_shown": "still-open trades are listed as open; "
                                    "no survivorship trimming",
        },
    }


def _realized_r(trade, outcome) -> float | None:
    """Price-space realized R multiple: (exit - entry) / (entry - stop),
    signed by direction. Null — never invented — when any input (actual
    fill prices from the outcome, the pre-registered stop from the trade)
    is missing or the risk denominator is zero."""
    entry = outcome.payload.get("entry_price")
    exit_price = outcome.payload.get("fill_price")
    stop = trade.payload.get("stop_loss")
    action = trade.payload.get("action")
    if entry is None or exit_price is None or stop is None or action is None:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    sign = 1.0 if action == "BUY" else -1.0
    return sign * (exit_price - entry) / risk


LISTING_MIN_GRADED_ENV = "PRO_LISTING_MIN_GRADED"
LISTING_MIN_GRADED_DEFAULT = 30


def listing_min_graded() -> int:
    """Publish-gate sample-size floor: $PRO_LISTING_MIN_GRADED, default
    30 (roughly where a win rate stops being pure noise). Malformed or
    non-positive values fall back to the default — the gate never opens
    by configuration accident."""
    import os

    raw = os.environ.get(LISTING_MIN_GRADED_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return LISTING_MIN_GRADED_DEFAULT
    return value if value > 0 else LISTING_MIN_GRADED_DEFAULT


def listing_gate(calibration: dict | None,
                 min_graded: int | None = None) -> list[str]:
    """P4-03 publish gate: the reasons a listing's graded record may NOT
    be published (empty list = pass). Same honesty ethos as
    public_track_record — a marketplace listing IS a performance claim,
    so the claim must carry a real graded sample:

    - ``n_graded`` present and >= the minimum (default 30,
      $PRO_LISTING_MIN_GRADED);
    - ``brier`` present and numeric — null is "never measured", and a
      null must not be dressed as a zero (a perfect score);
    - ``win_rate`` present, numeric, in [0, 1], WITH its own sample size
      ``win_rate_n`` >= 1 — a rate without an n is marketing, not a
      record.

    Every failure is a specific human-readable sentence; the publish
    endpoint returns them verbatim in its 422.
    """
    floor = min_graded if min_graded is not None else listing_min_graded()
    if not isinstance(calibration, dict) or not calibration:
        return ["no calibration record attached: publishing requires a "
                "graded record (n_graded, win_rate, brier)"]
    reasons: list[str] = []

    n_graded = calibration.get("n_graded")
    if not isinstance(n_graded, int) or isinstance(n_graded, bool):
        reasons.append("n_graded is missing or not an integer — the gate "
                       "cannot verify the sample size")
    elif n_graded < floor:
        reasons.append(f"n_graded={n_graded} is below the minimum of "
                       f"{floor} graded outcomes")

    brier = calibration.get("brier")
    if brier is None:
        reasons.append("brier is missing or null — an unmeasured brier "
                       "must not be dressed as a (perfect) zero")
    elif not isinstance(brier, (int, float)) or isinstance(brier, bool):
        reasons.append(f"brier={brier!r} is not a number")

    win_rate = calibration.get("win_rate")
    if win_rate is None:
        reasons.append("win_rate is missing or null")
    elif (not isinstance(win_rate, (int, float)) or isinstance(win_rate, bool)
          or not 0.0 <= float(win_rate) <= 1.0):
        reasons.append(f"win_rate={win_rate!r} is not a fraction in [0, 1]")
    else:
        win_rate_n = calibration.get("win_rate_n")
        if (not isinstance(win_rate_n, int) or isinstance(win_rate_n, bool)
                or win_rate_n < 1):
            reasons.append("win_rate carries no sample size — win_rate_n "
                           "must be a positive integer")
    return reasons


def agent_performance(runs: Sequence[RunRecord], memory: ProMemory) -> dict:
    """Per-agent activity plus outcome-scored accuracy.

    An agent scores on a closed trade when its vote was directional: correct
    if it voted with the executed action and the trade won, or against it
    and the trade lost. HOLD votes are neutral and not scored.
    """
    stats: dict[str, dict] = defaultdict(
        lambda: {"votes": 0, "confidence_sum": 0, "scored": 0, "correct": 0}
    )
    outcomes = {
        r.ref_id: r.payload for r in memory.records(MemoryKind.OUTCOME)
    }
    trade_records = {r.id: r for r in memory.records(MemoryKind.TRADE)}

    for run in runs:
        rec = run.recommendation
        if rec is None:
            continue
        outcome = None
        for trade_id, trade in trade_records.items():
            if trade.payload.get("recommendation_id") == rec.id:
                outcome = outcomes.get(trade_id)
                break
        for vote in rec.vote_breakdown.votes:
            row = stats[vote.agent_id]
            row["votes"] += 1
            row["confidence_sum"] += vote.confidence
            if outcome is None or vote.vote is TradeAction.HOLD:
                continue
            row["scored"] += 1
            agreed = vote.vote is rec.action
            won = bool(outcome.get("won"))
            if (agreed and won) or (not agreed and not won):
                row["correct"] += 1

    return {
        agent_id: {
            "votes": row["votes"],
            "avg_confidence": row["confidence_sum"] / row["votes"] if row["votes"] else 0,
            "scored": row["scored"],
            "hit_rate": row["correct"] / row["scored"] if row["scored"] else None,
        }
        for agent_id, row in sorted(stats.items())
    }
