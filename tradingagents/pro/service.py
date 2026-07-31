"""PaperTradingService: the deployable end-to-end loop (Phase 11).

One iteration = build snapshot -> run the full debate pipeline (recorded
for the dashboard) -> route an accepted recommendation through the
execution router -> manage open positions on bar closes -> report realized
P&L back to the router (circuit breaker) and memory (analogs, Kelly).

Position management here is bar-close based — the service reacts to stop/
target breaches observed at snapshot time. Intrabar fills are the backtest
broker's domain; live intrabar management belongs to venue-native
stop/take-profit orders once a real transport is signed off (ADR-0029).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tradingagents.contracts import (
    MarketSnapshot,
    ProConfig,
    TradeAction,
    TradeRecommendation,
    utc_now,
)
from tradingagents.pro.alerting import AlertManager
from tradingagents.pro.dashboard.app import DashboardState
from tradingagents.pro.execution import ExecutionRouter
from tradingagents.pro.memory import ProMemory
from tradingagents.pro.observability import MetricsRegistry

logger = logging.getLogger(__name__)

SnapshotSource = Callable[[], MarketSnapshot]


def _final_tp_price(rec) -> float:
    prices = getattr(rec, "take_profit_prices", None)
    if prices is not None:
        return prices[-1]
    return rec.take_profits[-1].price


@dataclass
class _RehydratedPlan:
    """Minimal exit plan reconstructed from a memory trade record; only the
    fields _manage_positions touches."""

    id: str
    action: TradeAction
    stop_loss: float
    take_profit_prices: list[float]


@dataclass
class OpenPosition:
    recommendation: TradeRecommendation
    fill_price: float
    quantity: float
    entry_commission: float
    # P1-03: realized perp funding accrued while open (negative = paid)
    funding_paid: float = 0.0
    last_funding_at: object = None  # datetime of last accrual
    # P1-04: TCA — arrival mid, entry slippage, post-fill markouts
    tca: dict = field(default_factory=dict)


class PaperTradingService:
    def __init__(
        self,
        llm,
        config: ProConfig,
        snapshot_source: SnapshotSource,
        router: ExecutionRouter,
        memory: ProMemory,
        dashboard_state: DashboardState | None = None,
        metrics: MetricsRegistry | None = None,
        alerts: AlertManager | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        run_lock: threading.Lock | None = None,
        **pipeline_kwargs,
    ):
        self.llm = llm
        self.config = config
        self.snapshot_source = snapshot_source
        self.router = router
        # P2-05: give the router the book context so pre-trade caps can
        # see portfolio VaR / correlated gross. Fail-open by design.
        router.portfolio_risk_provider = self._portfolio_risk_context
        # P3-07: every order audit line carries the same provenance stamp
        # as the runs that produce orders. Fail-open: a stamping failure
        # must never block trading infrastructure.
        try:
            from tradingagents.pro.versioning import build_version_stamp

            router.versions = build_version_stamp(config)
        except Exception:
            logger.exception("order audit will not carry a version stamp")
        self.memory = memory
        self.dashboard = dashboard_state or DashboardState(memory=memory)
        self.metrics = metrics or MetricsRegistry()
        self.alerts = alerts or AlertManager(metrics=self.metrics)
        self.on_event = on_event
        # serializes pipeline executions (hourly loop vs on-demand trigger)
        self.run_lock = run_lock or threading.Lock()
        self.pipeline_kwargs = pipeline_kwargs
        self.open_positions: dict[str, OpenPosition] = {}
        # paper-mode daily order cap (trader review): live arming has
        # max_orders_per_day; paper had none — a runaway loop could churn
        self._orders_today = 0
        self._orders_day: object = None
        # P2-06 event-driven triggers: symbols the 60s check scans, an
        # injectable bar source (tests / alternate feeds), and lazily loaded
        # debounce state (persisted via prefs so restarts never re-fire)
        self.event_symbols: tuple[str, ...] = (config.symbol,)
        self.event_bars_fn: Callable[[str], list] | None = None
        self._event_state: dict | None = None
        # loop-cadence hygiene: per-symbol last driving-bar start, lazily
        # loaded from prefs (persisted so restarts keep skipping correctly)
        self._last_bar_state: dict | None = None
        # P3-11: run_complete webhook registry, lazily wired from the
        # prefs' event store (None until first use / in file-backed mode)
        self.webhooks = None
        self.rehydrate()


    def _portfolio_risk_context(self):
        """P2-05 provider: signed open notionals + daily-return covariance
        for the open book. None on any failure — a data gap must fail open
        at the gate, never block trading on infrastructure."""
        try:
            from tradingagents.contracts import Timeframe
            from tradingagents.pro.analytics.risk import returns_covariance
            from tradingagents.pro.execution import PortfolioRiskContext

            notionals: dict[str, float] = {}
            for position in self.router.adapter.positions():
                # BrokerPosition carries avg_price + side (quantity is
                # positive); richer adapters may expose a live mark
                mark = (getattr(position, "mark_price", None)
                        or getattr(position, "avg_price", None)
                        or getattr(position, "entry_price", None))
                if not mark:
                    continue
                sign = -1 if getattr(position, "side", "BUY") == "SELL" else 1
                notionals[position.symbol] = sign * position.quantity * mark
            if not notionals:
                return None
            marketdata = getattr(self.dashboard, "marketdata", None)
            if marketdata is None:
                return None
            bars = {sym: marketdata.get_bars(sym, Timeframe.D1, limit=45)
                    for sym in notionals}
            result = returns_covariance(bars)
            if result is None:
                return PortfolioRiskContext(
                    open_notional_by_symbol=notionals,
                    cov_symbols=(), covariance=None)
            symbols, cov = result
            return PortfolioRiskContext(
                open_notional_by_symbol=notionals,
                cov_symbols=tuple(symbols), covariance=cov)
        except Exception:
            logger.warning("portfolio risk context unavailable", exc_info=True)
            return None

    def _order_budget_left(self) -> bool:
        today = utc_now().date()
        if self._orders_day != today:
            self._orders_day = today
            self._orders_today = 0
        return self._orders_today < self.config.risk.max_orders_per_day

    # --- durability (REL-01) ---------------------------------------------------

    def rehydrate(self) -> None:
        """Rebuild open-position tracking after a restart from the adapter's
        book plus memory's open trade records. Positions we cannot match to
        a remembered recommendation are left to reconcile() to flag."""
        for position in self.router.adapter.positions():
            if position.symbol in self.open_positions:
                continue
            rec = self._find_open_recommendation(position.symbol)
            if rec is None:
                logger.warning("unmatched venue position in %s; reconcile will flag it",
                               position.symbol)
                continue
            self.open_positions[position.symbol] = OpenPosition(
                recommendation=rec,
                fill_price=position.avg_price,
                quantity=position.quantity,
                entry_commission=0.0,  # already charged pre-restart
            )
            sign = 1 if position.side == "BUY" else -1
            self.router.local_book.setdefault(
                position.symbol, sign * position.quantity
            )

    def _find_open_recommendation(self, symbol: str):
        from tradingagents.pro.memory import MemoryKind

        closed_ids = {
            r.ref_id for r in self.memory.records(MemoryKind.OUTCOME)
        }
        for record in reversed(self.memory.records(MemoryKind.TRADE)):
            if record.symbol != symbol or record.id in closed_ids:
                continue
            payload = record.payload
            if not payload.get("stop_loss") or not payload.get("take_profits"):
                return None
            from tradingagents.contracts import TradeAction as TA

            return _RehydratedPlan(
                id=payload.get("recommendation_id", record.id),
                action=TA(payload["action"]),
                stop_loss=payload["stop_loss"],
                take_profit_prices=list(payload["take_profits"]),
            )
        return None

    # --- one iteration -----------------------------------------------------------

    def run_once(self, snapshot: MarketSnapshot | None = None,
                 config: ProConfig | None = None,
                 trigger: str = "loop") -> dict:
        with self.run_lock:
            summary = self._run_once(snapshot=snapshot, config=config,
                                     trigger=trigger)
        if summary.get("skipped"):
            # unchanged-bar loop skip: no pipeline ran, so no run/position/
            # status events either — the dashboard state didn't change
            return summary
        self._emit("run", summary)
        for closed in summary.get("closed_positions", []):
            self._emit("position", {"state": "closed", **closed})
        if summary.get("order_status") == "filled":
            self._emit("position", {"state": "opened",
                                    "symbol": summary.get("symbol"),
                                    "action": summary.get("action")})
        self._emit("status", self._status_event())
        return summary

    def _emit(self, type_: str, data: dict) -> None:
        """UI push hook (SSE); a broken consumer never breaks the loop."""
        if self.on_event is None:
            return
        try:
            self.on_event(type_, data)
        except Exception:
            logger.exception("on_event consumer failed for %s event", type_)

    def _status_event(self) -> dict:
        from tradingagents.pro.dashboard import service as views

        try:
            equity = self.router.adapter.account().equity
        except Exception:
            equity = None
        return views.system_status(self.router, equity)

    def _run_once(self, snapshot: MarketSnapshot | None = None,
                  config: ProConfig | None = None,
                  trigger: str = "loop") -> dict:
        if snapshot is None:
            produced = self.snapshot_source()
            # multi-symbol rotation: the source may pair each snapshot with
            # its per-asset config (crypto vs gold rosters)
            if isinstance(produced, tuple):
                snapshot, source_config = produced
                config = config or source_config
            else:
                snapshot = produced
        config = config or self.config
        # P3-07: keep the order audit stamp consistent with the config that
        # drives THIS run (multi-symbol rotation swaps per-asset configs).
        # run_lock serializes runs, so no order can race the update.
        try:
            from tradingagents.pro.versioning import build_version_stamp

            self.router.versions = build_version_stamp(config)
        except Exception:
            logger.exception("per-run version stamp update failed")
        if trigger == "loop" and self._skip_unchanged_bar(snapshot):
            # D1-cadence hygiene: the rotation revisits a symbol several
            # times per driving bar; identical inputs would spend an LLM
            # run to reach the same verdict. Loop path only — operator and
            # event triggers always run.
            self.metrics.inc("runs_skipped_unchanged_total")
            # the skip IS a healthy loop iteration: keep the /health/live +
            # dead-man heartbeat fresh, or an all-skipped stretch would
            # masquerade as a stalled loop
            self.metrics.set_gauge("last_run_ts", utc_now().timestamp())
            logger.info(
                "loop run for %s skipped: driving bar unchanged (%s) — no "
                "LLM spend (PRO_RERUN_UNCHANGED_BARS=1 restores re-runs)",
                snapshot.symbol, snapshot.bars[-1].start.isoformat())
            return {"run_id": None, "symbol": snapshot.symbol,
                    "action": None, "rejected_at": None,
                    "closed_positions": [],
                    "order_status": "skipped:unchanged_bar",
                    "in_sync": True, "skipped": True}
        self.metrics.inc("runs_total")
        # heartbeat for /health/live + the dead-man switch (go-live Phase 5)
        self.metrics.set_gauge("last_run_ts", utc_now().timestamp())

        reconciliation = self.router.reconcile()
        if not reconciliation.in_sync:
            self.metrics.inc("reconciliation_failures_total")
            self.alerts.emit(
                "critical", "reconciliation_drift",
                "local book and venue disagree; new entries blocked",
                missing=",".join(reconciliation.missing_on_venue),
                unknown=",".join(reconciliation.unknown_on_venue),
            )

        closed = self._manage_positions(snapshot)

        quarantined = [f for f in snapshot.missing_feeds
                       if f.startswith("news:quarantined")]
        if quarantined:
            self.alerts.emit(
                "critical", "injection_quarantined",
                f"{len(quarantined)} news item(s) quarantined as suspected "
                "prompt injection before reaching any prompt",
                symbol=snapshot.symbol,
            )

        # degraded data while exposed is worth waking someone (Phase 5):
        # a feed the open position depends on going dark is push-worthy,
        # not just a dashboard badge
        non_quarantine_missing = [f for f in snapshot.missing_feeds
                                  if not f.startswith("news:quarantined")]
        if non_quarantine_missing and self.open_positions:
            self.alerts.emit(
                "warning", "degraded_with_open_position",
                f"feeds degraded while {len(self.open_positions)} position(s) "
                f"open: {non_quarantine_missing}", symbol=snapshot.symbol,
            )

        # sizing must see the SAME equity the venue validator checks later
        # (submit path reads adapter.account().equity): sizing on a static
        # default while validating against live equity produced the phantom
        # SELL — every at-cap order bounced after the first losing trade
        run = self.dashboard.recorder.record_run(
            self.llm, config, snapshot, memory=self.memory,
            on_node=lambda name: self._emit("stage", {"stage": name,
                                                      "symbol": snapshot.symbol}),
            trigger=trigger,
            **{**self.pipeline_kwargs,
               "equity": self.router.adapter.account().equity},
        )
        rec = run.recommendation
        # P3-11: every completed run (accepted OR rejected) notifies the
        # registered run_complete webhooks — signed, threaded, best-effort
        self._notify_run_complete(run)
        self._emit_regime_change(snapshot.symbol)
        self._evaluate_intel_alerts()
        summary: dict = {
            "run_id": run.run_id,
            "symbol": snapshot.symbol,
            "action": rec.action.value if rec else None,
            "rejected_at": run.rejection and run.rejection.get("stage"),
            "closed_positions": closed,
            "order_status": None,
            "in_sync": reconciliation.in_sync,
        }
        if run.rejection:
            self.metrics.inc("rejections_total", stage=run.rejection["stage"])
            return summary
        if rec is not None:
            self.metrics.inc("recommendations_total", action=rec.action.value)
        if closed:
            # cooldown: never re-enter on the same bar that closed a position
            # (exit-bar churn); the next iteration may enter fresh
            summary["order_status"] = "cooldown"
            return summary
        if not reconciliation.in_sync:
            # book drift is an incident, not a trading opportunity
            summary["order_status"] = "blocked:reconciliation"
            return summary
        if (
            rec is not None
            and rec.action is not TradeAction.HOLD
            and (run.state.get("execution_status") or "").startswith("accepted")
            and rec.symbol not in self.open_positions
        ):
            # Phase 3 data-health gate: with live gates armed, degraded
            # ingestion is a hard pre-trade stop — never trade on data
            # you'd flag as degraded in the UI
            if (getattr(self.router, "live_gates", None) is not None
                    and snapshot.missing_feeds):
                self.router.audit.append("live_data_health", {
                    "recommendation_id": rec.id,
                    "missing_feeds": list(snapshot.missing_feeds),
                })
                self.alerts.emit(
                    "warning", "order_rejected",
                    f"entry for {rec.symbol} blocked: degraded feeds "
                    f"{list(snapshot.missing_feeds)}", symbol=rec.symbol,
                )
                summary["order_status"] = "blocked:data_health"
                return summary
            # P1-06 stale-data gate: never open a position on bars older
            # than 3x the driving timeframe — a vendor incident must fail
            # closed, not trade on yesterday's close
            if snapshot.bars:
                from tradingagents.pro.dashboard.marketdata import (
                    TIMEFRAME_SECONDS,
                )

                last_bar = snapshot.bars[-1]
                tf_s = TIMEFRAME_SECONDS.get(last_bar.timeframe, 3600)
                # age at snapshot-build time: "were the bars fresh when we
                # decided" — a stalled vendor fails closed
                age_s = (snapshot.as_of - last_bar.start).total_seconds()
                if age_s > 3 * tf_s:  # bar START lags one interval + slack
                    self.alerts.emit(
                        "warning", "order_rejected",
                        f"entry for {rec.symbol} blocked: stale data — last "
                        f"bar {age_s / 3600:.1f}h old ({last_bar.timeframe.value})",
                        symbol=rec.symbol,
                    )
                    summary["order_status"] = "blocked:stale_data"
                    return summary
            spread_bps = None
            if snapshot.quote and snapshot.quote.bid and snapshot.quote.ask:
                mid = (snapshot.quote.bid + snapshot.quote.ask) / 2
                if mid > 0:
                    spread_bps = 10_000.0 * (
                        snapshot.quote.ask - snapshot.quote.bid) / mid
            if not self._order_budget_left():
                self.alerts.emit(
                    "warning", "order_rejected",
                    f"entry for {rec.symbol} blocked: daily order cap "
                    f"({self.config.risk.max_orders_per_day}) reached",
                    symbol=rec.symbol,
                )
                summary["order_status"] = "blocked:daily_order_cap"
                return summary
            equity = self.router.adapter.account().equity
            result = self.router.submit_recommendation(
                rec, equity, spread_bps=spread_bps)
            self._orders_today += 1
            summary["order_status"] = result.status
            if result.status == "rejected":
                reason = result.reason or ""
                safety_stop = reason.startswith(("kill_switch", "circuit_breaker"))
                self.alerts.emit(
                    "critical" if safety_stop else "warning",
                    "order_rejected",
                    f"order for {rec.symbol} rejected: {reason}",
                    symbol=rec.symbol,
                )
                # write the venue verdict back onto the run: every dashboard
                # surface reads run.state["execution_status"], and leaving it
                # "accepted:paper" painted an executed SELL over a flat book
                run.state["execution_status"] = f"rejected:order ({reason})"
                self.dashboard.recorder.repersist(run)
            if result.status == "filled":
                self.metrics.inc("orders_filled_total")
                position = OpenPosition(
                    recommendation=rec,
                    fill_price=result.fill_price,
                    quantity=result.filled_quantity,
                    entry_commission=result.commission,
                )
                self._capture_tca(position, rec.symbol, snapshot)
                self.open_positions[rec.symbol] = position
        self._maybe_daily_pnl_summary(snapshot)
        return summary

    # intel condition thresholds (Phase 4 v1 — operator defaults; a
    # prefs-backed builder can layer on later). Fires on CROSSINGS only:
    # each key remembers its last state so steady conditions stay quiet.
    FUNDING_EXTREME = 0.03      # %/8h ≈ 33% annualized — crowded carry
    VOL_SPIKE_1D = 2.0          # GVZ points day-over-day
    EVENT_TMINUS_S = 3900.0     # warn inside ~65 minutes of a major release

    def _evaluate_intel_alerts(self) -> None:
        """Condition alerts over the intel snapshot (trader review: 'the
        platform HAS this data' — funding, positioning, vol, calendar —
        'but never calls me'). Reuses the 60s-TTL intel cache; no extra
        vendor spend."""
        intel = getattr(self.dashboard, "intel", None)
        if intel is None:
            return
        if not hasattr(self, "_intel_state"):
            # P1-06: crossing state survives restarts (no re-fires)
            try:
                self._intel_state: dict = self.dashboard.prefs.intel_alert_state()
            except Exception:
                self._intel_state = {}
        try:
            snapshot = intel.snapshot()
            metrics = {m["name"]: m["value"]
                       for m in snapshot.get("metrics", [])}
        except Exception:
            return

        def crossed(key: str, active: bool, severity: str, text: str) -> None:
            was = self._intel_state.get(key, False)
            if active and not was:
                self.alerts.emit(severity, "intel_alert", text)
            self._intel_state[key] = active

        funding = metrics.get("FUNDING_RATE")
        if funding is not None:
            crossed(
                "funding_extreme", abs(funding) >= self.FUNDING_EXTREME,
                "warning",
                f"funding rate {funding:+.4f}%/8h — crowded "
                f"{'longs' if funding > 0 else 'shorts'} paying carry",
            )
        vol_chg = metrics.get("GOLD_VOL_INDEX_CHANGE_1D")
        if vol_chg is not None:
            crossed(
                "gold_vol_spike", vol_chg >= self.VOL_SPIKE_1D, "warning",
                f"gold vol index jumped {vol_chg:+.2f} in a day — "
                "regime shift risk",
            )
        cot_chg = metrics.get("GOLD_COT_NET_CHANGE_1W")
        prev_cot = self._intel_state.get("cot_sign")
        if cot_chg is not None:
            sign = 1 if cot_chg > 0 else -1 if cot_chg < 0 else 0
            if prev_cot is not None and sign != 0 and sign != prev_cot:
                self.alerts.emit(
                    "info", "intel_alert",
                    f"gold COT weekly positioning flipped "
                    f"{'bullish' if sign > 0 else 'bearish'} "
                    f"({cot_chg:+,.0f} contracts w/w)",
                )
            if sign != 0:
                self._intel_state["cot_sign"] = sign
        try:
            calendar_fn = self.pipeline_kwargs.get("calendar_fn")
            nxt = calendar_fn() if calendar_fn else None
            if nxt and nxt.get("seconds_until") is not None:
                key = f"tminus:{nxt.get('release')}:{nxt.get('date')}"
                if (0 < nxt["seconds_until"] <= self.EVENT_TMINUS_S
                        and not self._intel_state.get(key)):
                    minutes = int(nxt["seconds_until"] // 60)
                    self.alerts.emit(
                        "warning", "intel_alert",
                        f"major release in {minutes}m: {nxt.get('release')} — "
                        "the event gate will block new entries",
                    )
                    self._intel_state[key] = True
        except Exception:
            pass
        self._evaluate_condition_alerts(metrics)
        try:
            self.dashboard.prefs.save_intel_alert_state(self._intel_state)
        except Exception:
            logger.warning("intel alert state not persisted; continuing",
                           exc_info=True)

    def _evaluate_condition_alerts(self, metrics: dict) -> None:
        """P2-07: user-built condition alerts (prefs ``condition_alerts``)
        over the same intel metric readings as the operator defaults.
        Crossing state lives in the SAME persisted ``_intel_state`` dict
        (keys ``cond:<alert_id>``) so restarts never re-fire. gt/lt fire
        when the condition becomes true (quiet while it stays true);
        crosses_above/below additionally require a prior reading on the
        other side — a fresh alert never fires on its first observation."""
        try:
            alerts = self.dashboard.prefs.condition_alerts()
        except Exception:
            return
        live_keys = {f"cond:{a['id']}" for a in alerts}
        for key in [k for k in self._intel_state
                    if k.startswith("cond:") and k not in live_keys]:
            del self._intel_state[key]  # deleted alerts leave no state
        for alert in alerts:
            if not alert.get("active"):
                continue
            value = metrics.get(alert["metric"])
            if value is None:
                continue
            key = f"cond:{alert['id']}"
            was = self._intel_state.get(key)
            operator, threshold = alert["operator"], alert["threshold"]
            if operator in ("gt", "crosses_above"):
                now_true = value > threshold
            else:  # lt / crosses_below
                now_true = value < threshold
            fire = (now_true and was is not None and not was
                    if operator.startswith("crosses")
                    else now_true and not was)
            if fire:
                from tradingagents.pro.dashboard.intel import METRIC_INFO

                label = (METRIC_INFO.get(alert["metric"], {}).get("label")
                         or alert["metric"])
                arrow = ("above" if operator in ("gt", "crosses_above")
                         else "below")
                note = alert.get("note") or ""
                self.alerts.emit(
                    "warning", "condition_alert",
                    f"{label} {arrow} {threshold:g}: now {value:g}"
                    + (f" — {note}" if note else ""),
                )
            self._intel_state[key] = now_true

    # --- run_complete webhooks (P3-11) -----------------------------------------

    def _webhook_registry(self):
        """Lazily wired WebhookRegistry over the event store the prefs
        ride on; None in file-backed dev/test mode (no kv table). Tests
        may pre-set ``self.webhooks`` (injectable-fakes pattern)."""
        if self.webhooks is None:
            store = getattr(self.dashboard.prefs, "store", None)
            if store is None:
                return None
            from tradingagents.pro.webhooks import WebhookRegistry

            self.webhooks = WebhookRegistry(store, alerts=self.alerts)
        return self.webhooks

    def _notify_run_complete(self, run) -> None:
        """Fire run_complete webhooks for one recorded run. Non-blocking:
        dispatch happens on a daemon thread (each delivery already has a
        5s timeout); any failure is the registry's problem, never the
        loop's."""
        try:
            registry = self._webhook_registry()
            if registry is None or not registry.has_active("run_complete"):
                return
            rec = run.recommendation
            payload = {
                "event": "run_complete",
                "run_id": run.run_id,
                "symbol": run.symbol,
                "action": rec.action.value if rec else None,
                "started_at": run.started_at.isoformat(),
                # P3-07 provenance stamp; None on pre-stamp runs
                "versions": run.versions,
            }
            threading.Thread(
                target=registry.dispatch, args=("run_complete", payload),
                name="run-webhooks", daemon=True,
            ).start()
        except Exception:
            logger.exception("run_complete webhook dispatch not started")

    def _emit_regime_change(self, symbol: str) -> None:
        """Alert on regime TRANSITIONS (trader review Phase 4): the regime
        records already accrue per run; a flip between the last two is the
        event a trader wants pushed, not the steady state."""
        from tradingagents.pro.memory import MemoryKind

        try:
            regimes = [r for r in self.memory.records(MemoryKind.REGIME)
                       if r.symbol == symbol]
            if len(regimes) < 2:
                return
            prev = regimes[-2].payload.get("regime")
            curr = regimes[-1].payload.get("regime")
            if prev and curr and prev != curr:
                self.alerts.emit(
                    "info", "regime_change",
                    f"{symbol} regime changed: {prev} → {curr}",
                    symbol=symbol,
                )
        except Exception:
            logger.warning("regime-change detection failed; continuing",
                           exc_info=True)

    def _maybe_daily_pnl_summary(self, snapshot) -> None:
        """Emit one info/daily_pnl alert per UTC day (go-live Phase 5)."""
        from tradingagents.pro.dashboard.service import trade_journal

        today = utc_now().date().isoformat()
        if getattr(self, "_last_pnl_day", None) == today:
            return
        # restart-proof (review R3.1): the in-memory marker dies with the
        # process, so every deploy re-emitted a fresh "daily" summary — ten
        # in twelve hours during a deploy burst. The durable notification
        # log is the tiebreaker; a "daily" event must mean what it says.
        prefs = getattr(self.dashboard, "prefs", None)
        if prefs is not None:
            try:
                if any(n.get("event") == "daily_pnl"
                       and str(n.get("time", "")).startswith(today)
                       for n in prefs.notifications()):
                    self._last_pnl_day = today
                    return
            except Exception:
                logger.warning("daily_pnl dedupe probe failed; may re-emit",
                               exc_info=True)
        self._last_pnl_day = today
        journal = trade_journal(self.memory)
        try:
            equity = self.router.adapter.account().equity
        except Exception:
            equity = None
        self.alerts.emit(
            "info", "daily_pnl",
            f"daily summary: {journal['n_trades']} closed trades, realized "
            f"P&L {journal['total_pnl']:+.2f}"
            + (f", equity {equity:,.2f}" if equity is not None else ""),
        )

    def run_forever(
        self,
        interval_seconds: float = 3600.0,
        max_iterations: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            try:
                summary = self.run_once()
                logger.info("service iteration complete",
                            extra={"extra_fields": summary})
            except Exception as exc:
                logger.exception("service iteration failed; continuing")
                self.metrics.inc("iteration_errors_total")
                self.alerts.emit(
                    "warning", "iteration_error",
                    f"service iteration raised {type(exc).__name__}: {exc}",
                )
            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                sleep(interval_seconds)

    # --- event-driven triggers (P2-06) ---------------------------------------------

    def check_event_triggers(self, now: datetime | None = None) -> list[dict]:
        """Besides the hourly rotation, fire a pipeline run when (a) a major
        calendar event just released (T+delay), (b) the last bar's realized
        range spikes past n×ATR, or (c) price gaps n×ATR between consecutive
        bars. Every hit routes through run_once — the SAME run_lock-serialized
        path as the loop — with trigger="event:<reason>" provenance, and the
        daily order cap still applies inside the run. Debounced per symbol via
        a persisted cooldown (prefs event_trigger_state) so container restarts
        never re-fire. Returns the fires: [{symbol, reason, run_id}]."""
        cfg = getattr(self.config, "event_triggers", None)
        if cfg is None or not cfg.enabled:
            return []
        now = now or utc_now()
        state = self._event_trigger_state()
        fired: list[dict] = []
        for symbol, reason in self._detect_event_triggers(now, cfg, state):
            cooldowns = state.setdefault("cooldown_until", {})
            until = cooldowns.get(symbol)
            if until:
                try:
                    if now < datetime.fromisoformat(until):
                        continue
                except ValueError:
                    pass  # corrupt timestamp: treat as expired
            if not self._order_budget_left():
                # daily order cap reached: an event run could only be
                # blocked at submit — don't spend the LLM budget either
                logger.info("event trigger %s suppressed: daily order cap "
                            "reached", reason)
                continue
            cooldowns[symbol] = (
                now + timedelta(minutes=cfg.cooldown_minutes)).isoformat()
            # persist BEFORE the (slow) run: a crash mid-run must not re-fire
            self._save_event_trigger_state(state)
            self.alerts.emit("warning", "event_trigger",
                             f"event-triggered run: {reason}", symbol=symbol)
            try:
                summary = self.run_once(trigger=f"event:{reason}")
            except Exception:
                logger.exception("event-triggered run failed (%s); cooldown "
                                 "stands", reason)
                continue
            fired.append({"symbol": symbol, "reason": reason,
                          "run_id": summary.get("run_id")})
        self._save_event_trigger_state(state)
        return fired

    def _detect_event_triggers(self, now: datetime, cfg,
                               state: dict) -> list[tuple[str, str]]:
        """(symbol, reason) candidates; debounce/cap filtering is the
        caller's job. At most one bar-based reason per symbol per check."""
        hits: list[tuple[str, str]] = list(
            self._detect_calendar_triggers(now, cfg, state))
        for symbol in self.event_symbols:
            bars = self._event_bars(symbol)
            if len(bars) < 2:
                continue
            atr = self._atr(bars[:-1])  # spike bar can't inflate its baseline
            if not atr or atr <= 0:
                continue
            last, prev = bars[-1], bars[-2]
            if (last.high - last.low) > cfg.vol_spike_atr_mult * atr:
                hits.append((symbol, f"vol_spike:{symbol}"))
                continue
            if abs(last.open - prev.close) > cfg.gap_atr_mult * atr:
                hits.append((symbol, f"gap:{symbol}"))
        return hits

    def _detect_calendar_triggers(self, now: datetime, cfg,
                                  state: dict) -> list[tuple[str, str]]:
        """Remember upcoming majors from the calendar source (the same
        next_major the event gate uses); once one's scheduled instant is
        delay minutes past, fire exactly once (fired keys are persisted —
        next_major itself skips past events, so memory is required)."""
        pending: dict = state.setdefault("pending_events", {})
        fired: dict = state.setdefault("fired_events", {})
        calendar_fn = self.pipeline_kwargs.get("calendar_fn")
        if calendar_fn is not None:
            try:
                nxt = calendar_fn()
            except Exception:
                nxt = None
            if nxt and nxt.get("at"):
                key = f"{nxt.get('release')}@{nxt.get('at')}"
                pending.setdefault(key, nxt["at"])
        hits: list[tuple[str, str]] = []
        delay = timedelta(minutes=cfg.calendar_delay_minutes)
        for key, at_iso in list(pending.items()):
            try:
                at = datetime.fromisoformat(str(at_iso))
            except ValueError:
                pending.pop(key, None)
                continue
            if now - at > timedelta(hours=24):  # prune stale entries
                pending.pop(key, None)
                fired.pop(key, None)
                continue
            if now >= at + delay and key not in fired:
                fired[key] = now.isoformat()
                release = key.split("@", 1)[0]
                hits.append((self.config.symbol, f"calendar:{release}"))
        return hits

    def _event_bars(self, symbol: str) -> list:
        """Latest bars for trigger evaluation: the injected source when set,
        else the dashboard's market-data service (cached; the same bars the
        charts render). Empty list on any failure — a data gap must never
        crash the check loop."""
        if self.event_bars_fn is not None:
            try:
                return list(self.event_bars_fn(symbol) or [])
            except Exception:
                logger.warning("event bars source failed for %s", symbol,
                               exc_info=True)
                return []
        marketdata = getattr(self.dashboard, "marketdata", None)
        if marketdata is None:
            return []
        from tradingagents.contracts import Timeframe

        try:
            return list(marketdata.get_bars(symbol, Timeframe.H1, limit=16)
                        or [])
        except Exception:
            return []

    @staticmethod
    def _atr(bars, period: int = 14) -> float | None:
        """Plain Wilder-free ATR (mean true range over the last `period`
        bars) — deliberately dependency-light for a 60s check loop."""
        if len(bars) < period + 1:
            return None
        window = bars[-(period + 1):]
        ranges = [
            max(bar.high - bar.low,
                abs(bar.high - prev.close),
                abs(bar.low - prev.close))
            for prev, bar in zip(window[:-1], window[1:], strict=True)
        ]
        return sum(ranges) / len(ranges) if ranges else None

    # --- unchanged-bar loop skip ----------------------------------------------------

    def _skip_unchanged_bar(self, snapshot: MarketSnapshot) -> bool:
        """True when the loop already ran this symbol on this driving bar.

        Remembers each symbol's last bar start (persisted via prefs, the
        same pattern as event_trigger_state, so restarts don't re-spend);
        a new bar updates the memory and runs. PRO_RERUN_UNCHANGED_BARS=1
        opts out (old behavior: every rotation tick runs the pipeline)."""
        if os.environ.get("PRO_RERUN_UNCHANGED_BARS") == "1":
            return False
        if not snapshot.bars:
            return False
        last = snapshot.bars[-1].start.isoformat()
        state = self._last_bar_seen()
        if state.get(snapshot.symbol) == last:
            return True
        state[snapshot.symbol] = last
        self._save_last_bar_seen(state)
        return False

    def _last_bar_seen(self) -> dict:
        if self._last_bar_state is None:
            try:
                self._last_bar_state = self.dashboard.prefs.last_bar_state()
            except Exception:
                self._last_bar_state = {}
        return self._last_bar_state

    def _save_last_bar_seen(self, state: dict) -> None:
        try:
            self.dashboard.prefs.save_last_bar_state(state)
        except Exception:
            logger.warning("last-bar state not persisted; continuing",
                           exc_info=True)

    def _event_trigger_state(self) -> dict:
        if self._event_state is None:
            try:
                self._event_state = self.dashboard.prefs.event_trigger_state()
            except Exception:
                self._event_state = {}
        return self._event_state

    def _save_event_trigger_state(self, state: dict) -> None:
        try:
            self.dashboard.prefs.save_event_trigger_state(state)
        except Exception:
            logger.warning("event trigger state not persisted; continuing",
                           exc_info=True)

    def run_event_triggers_forever(
        self,
        interval_seconds: float = 60.0,
        max_iterations: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Light 60s evaluation loop beside the hourly rotation (roadmap
        P2-06). A failing check never kills the thread."""
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            try:
                self.check_event_triggers()
            except Exception:
                logger.exception("event-trigger check failed; continuing")
                self.metrics.inc("event_trigger_errors_total")
            iterations += 1
            if max_iterations is None or iterations < max_iterations:
                sleep(interval_seconds)

    def start_event_trigger_daemon(
            self, interval_seconds: float = 60.0) -> threading.Thread | None:
        """Start the 60s check loop when enabled; None (no thread) when the
        feature is off — existing deployments change nothing."""
        cfg = getattr(self.config, "event_triggers", None)
        if cfg is None or not cfg.enabled:
            return None
        thread = threading.Thread(
            target=self.run_event_triggers_forever,
            kwargs={"interval_seconds": interval_seconds},
            name="event-triggers", daemon=True,
        )
        thread.start()
        return thread

    # --- internals ----------------------------------------------------------------

    def _capture_tca(self, position: OpenPosition, symbol: str,
                     snapshot) -> None:
        """P1-04: per-fill TCA — arrival mid at decision, signed entry
        slippage, and best-effort markouts at +30s/+1m/+5m from the tick
        cache. ponytail: markout timers die with the process — best-effort
        by design; the entry slippage (the number that matters) is durable."""
        quote = snapshot.quote
        arrival = None
        if quote and quote.bid and quote.ask:
            arrival = (quote.bid + quote.ask) / 2
        elif snapshot.bars:
            arrival = snapshot.bars[-1].close
        if not arrival:
            return
        long = position.recommendation.action is TradeAction.BUY
        side = 1.0 if long else -1.0
        position.tca = {
            "arrival_mid": arrival,
            # positive = paid worse than arrival
            "entry_slippage_bps": side * (position.fill_price - arrival)
            / arrival * 10_000.0,
            "markouts_bps": {},
        }
        ticks = getattr(self.dashboard, "ticks", None)
        if ticks is None:
            return

        def markout(label: str) -> None:
            try:
                cached = ticks.get(symbol)
                if cached:
                    last = cached[0]
                    position.tca["markouts_bps"][label] = (
                        side * (last - position.fill_price)
                        / position.fill_price * 10_000.0)
            except Exception:
                pass

        for delay, label in ((30, "30s"), (60, "1m"), (300, "5m")):
            timer = threading.Timer(delay, markout, args=(label,))
            timer.daemon = True
            timer.start()

    @staticmethod
    def _accrue_funding(position: OpenPosition, snapshot, bar) -> None:
        """P1-03: charge realized perp funding on open crypto positions.
        Rate comes from the symbol's own snapshot FUNDING_RATE (%/8h,
        Delta/Binance); accrual is continuous over elapsed hours. Spot/gold
        snapshots carry no FUNDING_RATE and accrue nothing."""
        rate_8h = None
        for reading in [*snapshot.onchain, *snapshot.macro]:
            if reading.name == "FUNDING_RATE":
                rate_8h = reading.value
                break
        if rate_8h is None:
            return
        since = position.last_funding_at or snapshot.as_of
        hours = max(0.0, (bar.start - since).total_seconds() / 3600.0)
        position.last_funding_at = bar.start
        if hours == 0:
            return
        notional = abs(position.quantity) * bar.close
        pay = notional * (rate_8h / 100.0) * (hours / 8.0)
        # positive funding: longs pay, shorts receive
        long = position.recommendation.action is TradeAction.BUY
        position.funding_paid += -pay if long else pay

    def _manage_positions(self, snapshot: MarketSnapshot) -> list[dict]:
        """Close positions whose stop or final target was breached at the
        latest bar close; report realized P&L to router + memory."""
        if not snapshot.bars:
            return []
        bar = snapshot.bars[-1]
        closed = self._consume_oms_exits(bar)
        for symbol, position in list(self.open_positions.items()):
            if symbol != snapshot.symbol:
                continue
            self._accrue_funding(position, snapshot, bar)
            omses = (self.router.omses() if hasattr(self.router, "omses")
                     else [o for o in (getattr(self.router, "oms", None),) if o])
            if any(o.has_venue_protection(symbol) for o in omses):
                # venue-side bracket owns the exit; bar-close management
                # would double-close (go-live Phase 2 coexistence rule)
                continue
            rec = position.recommendation
            long = rec.action is TradeAction.BUY
            # intrabar high/low with stop-first priority: identical
            # pessimistic semantics to the backtest broker (QUANT-03)
            stop_hit = bar.low <= rec.stop_loss if long else bar.high >= rec.stop_loss
            final_tp = _final_tp_price(rec)
            target_hit = bar.high >= final_tp if long else bar.low <= final_tp
            if not (stop_hit or target_hit):
                continue
            exit_reference = rec.stop_loss if stop_hit else final_tp
            result = self.router.adapter.close_position(symbol, exit_reference)
            if result.status != "filled":
                logger.warning("close failed for %s: %s", symbol, result.reason)
                continue
            sign = 1 if long else -1
            pnl = (
                sign * (result.fill_price - position.fill_price) * position.quantity
                - position.entry_commission
                - result.commission
                + position.funding_paid  # P1-03: realized perp funding
            )
            reason = "stop" if stop_hit else "take_profit"
            self.router.record_close(symbol, pnl)
            record = self.memory.find_trade_by_recommendation(rec.id)
            if record is not None:
                self.memory.close_trade(
                    record.id, pnl=pnl,
                    lesson=f"{rec.action.value} exited via {reason}",
                    event_time=bar.start,
                    details={
                        "mode": self._trade_mode(symbol),
                        "commission": position.entry_commission + result.commission,
                        "funding_paid": position.funding_paid,
                        "tca": position.tca,
                        "venue_order_id": result.venue_symbol,
                        "fill_price": result.fill_price,
                        "entry_price": position.fill_price,
                    })
            self.metrics.inc("positions_closed_total", reason=reason)
            self.metrics.set_gauge("last_realized_pnl", pnl)
            del self.open_positions[symbol]
            closed.append({"symbol": symbol, "pnl": pnl, "reason": reason})
        return closed

    def _trade_mode(self, symbol: str) -> str:
        """Effective arming tier for a symbol at close time (paper default)
        — tags the journal for per-mode calibration (go-live Phase 5)."""
        arming = getattr(self.dashboard, "arming", None)
        if arming is None:
            return "paper"
        try:
            return arming.effective_tier(symbol)
        except Exception:
            return "paper"

    def _consume_oms_exits(self, bar) -> list[dict]:
        """Fold venue-detected exits (brackets/watchdog flattens) into the
        SAME downstream path as bar-close exits: breaker, memory, metrics.
        Phase 6: both the paper and (when routed) live OMS are drained."""
        omses = (self.router.omses() if hasattr(self.router, "omses")
                 else [o for o in (getattr(self.router, "oms", None),) if o])
        closed = []
        for oms in omses:
            oms.poll()
            closed.extend(self._drain_one_oms(oms, bar))
        return closed

    def _drain_one_oms(self, oms, bar) -> list[dict]:
        closed = []
        for trade in oms.drain_closed():
            position = self.open_positions.pop(trade.symbol, None)
            self.router.record_close(trade.symbol, trade.pnl)
            if position is not None:
                record = self.memory.find_trade_by_recommendation(
                    position.recommendation.id)
                if record is not None:
                    self.memory.close_trade(
                        record.id, pnl=trade.pnl,
                        lesson=f"exited via venue {trade.reason}",
                        event_time=bar.start,
                        details={
                            "mode": self._trade_mode(trade.symbol),
                            "commission": trade.commission,
                            "venue_order_id": trade.client_order_id,
                            "fill_price": trade.exit_price,
                            "entry_price": trade.entry_price,
                        })
            self.metrics.inc("positions_closed_total", reason=trade.reason)
            self.metrics.set_gauge("last_realized_pnl", trade.pnl)
            closed.append({"symbol": trade.symbol, "pnl": trade.pnl,
                           "reason": trade.reason})
        return closed
