"""ExecutionRouter: the only sanctioned path from recommendation to venue.

Order of gates (each step audited):
validate -> kill switch -> circuit breaker -> idempotent submit with
bounded retries. Reconciliation compares the router's book against what
the adapter reports — drift is surfaced, never silently adopted.

P3-10 TWAP entry slicing lives HERE, in the router, not in the OMS.
Rationale: the OMS's atomicity unit is one ExecutionPlan = one entry plus
its protection, placed as a unit (native bracket on Binance, synthetic
stop + watchdog elsewhere). Slicing above that boundary means every child
slice is a complete plan flowing through the SAME dispatch tail a single
order uses — so the P3-01 "no naked entry" invariant holds per slice by
construction, on every venue, with zero new code inside the OMS or the
adapters.

Per-venue stop rule (investigated, P3-10): no wired venue can rest a
protective stop for quantity that has not filled yet — Binance rejects a
reduce-only STOP_MARKET with no position behind it, and the paper
adapter's synthetic-stop path likewise rejects reduce-only orders without
an opposing position. The "venue stop for the full intended quantity goes
first" variant is therefore impossible everywhere; the safest available
rule is PER-SLICE stops: each child carries the full BracketSpec, so
Binance places entry+STOP_MARKET atomically inside place_order per slice,
the synthetic-bracket OMS path places a per-slice reduce-only stop, and
the paper default (bar_close) manages whatever quantity is open. A process
death mid-window leaves the remaining slices unplaced (timers are
best-effort, like the P1-04 markouts) — every already-filled slice is
already protected, and a detectable mid-window failure logs, audits and
alerts (``twap_slice_failed``).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from tradingagents.contracts import RiskLimits, TradeRecommendation, utc_now
from tradingagents.pro.execution.audit import AuditLog
from tradingagents.pro.execution.interface import (
    AdapterError,
    ExecutionAdapter,
    OrderRequest,
    OrderResult,
)
from tradingagents.pro.execution.safety import CircuitBreaker, KillSwitch
from tradingagents.pro.execution.validation import validate_recommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReconciliationReport:
    in_sync: bool
    missing_on_venue: tuple[str, ...] = ()
    unknown_on_venue: tuple[str, ...] = ()
    quantity_mismatches: tuple[str, ...] = ()


@dataclass
class ExecutionRouter:
    adapter: ExecutionAdapter
    limits: RiskLimits
    kill_switch: KillSwitch
    breaker: CircuitBreaker
    audit: AuditLog
    max_retries: int = 2
    local_book: dict[str, float] = field(default_factory=dict)  # symbol -> signed qty
    # go-live Phase 2: when an OrderManager is injected, submissions run
    # through the journaled OMS path (write-ahead, resolve-by-coid,
    # brackets). None = the original synchronous path, unchanged.
    oms = None
    protection_mode: str = "bar_close"  # "venue_bracket" for live wiring
    # go-live Phase 3: LiveGateChain when real capital is armed; None =
    # paper behavior unchanged.
    live_gates = None
    # go-live Phase 6 staged rollout: per-pair mode routing. ``arming``
    # decides the tier; ``live_oms`` (an OrderManager over the live venue
    # adapter) is where canary/live orders go; ``shadow_tracker`` records
    # would-have-been live fills for shadow-mode paper fills. All None =
    # every order stays on the paper venue, exactly as before.
    arming = None
    live_oms = None
    shadow_tracker = None
    # P2-05: optional zero-arg callable returning a
    # ``validation.PortfolioRiskContext`` (or None) built from the open
    # book and return covariance. None = the portfolio VaR / correlated-
    # gross caps are skipped (they are also disabled by default in
    # RiskLimits), so existing wiring behaves exactly as before.
    portfolio_risk_provider = None
    # P3-07 algo/version tagging: provenance stamp {git_sha, prompt_hash,
    # model_ids, config_hash} attached to every order audit entry. The
    # service sets it from versioning.build_version_stamp(config); None
    # (bare routers, old wiring) simply omits the field.
    versions = None
    # P3-10 TWAP slicing: the service wires ``alerts`` (AlertManager) so a
    # mid-window slice failure pages, and ``twap_price_fn`` (symbol -> last
    # price or None) so each slice records its own arrival price off the
    # shared tick cache. Both optional — bare routers fall back to logging
    # and the recommendation's entry reference. ``twap_executions`` holds
    # per-recommendation slice state (fills, schedule, summary) keyed by
    # recommendation id; the service folds it into position TCA.
    alerts = None
    twap_price_fn = None
    twap_executions: dict[str, dict] = field(default_factory=dict)

    def tier_for(self, symbol: str) -> str:
        if self.arming is None:
            return "paper"
        try:
            return self.arming.effective_tier(symbol)
        except Exception:
            return "paper"

    def omses(self) -> list:
        return [o for o in (self.oms, self.live_oms) if o is not None]

    def _stamped(self, payload: dict) -> dict:
        """Attach the P3-07 version stamp to an order audit payload."""
        if self.versions is not None:
            return {**payload, "versions": self.versions}
        return payload

    def submit_recommendation(
        self, rec: TradeRecommendation | None, equity: float,
        spread_bps: float | None = None,
    ) -> OrderResult:
        tier = self.tier_for(getattr(rec, "symbol", ""))
        route_live = tier in ("canary", "live") and self.live_oms is not None
        self.audit.append("order_received", self._stamped({
            "recommendation_id": getattr(rec, "id", None),
            "symbol": getattr(rec, "symbol", None),
            "action": getattr(rec, "action", None) and rec.action.value,
            "tier": tier,
            "route": "live" if route_live else "paper",
        }))

        # a pair armed at a live tier with no live venue wired is REFUSED,
        # never silently paper-filled — the operator believes real capital
        # is working; pretending would be a lie (Phase 6)
        if tier in ("canary", "live") and self.live_oms is None:
            return self._refuse(
                rec, "live_route_unavailable",
                f"pair armed '{tier}' but no live venue is wired — refusing "
                "rather than silently filling on paper")

        # leading gate (OMS wiring only): a process that has not accounted
        # for its outstanding orders does not trade
        entry_oms = self.live_oms if route_live else self.oms
        if entry_oms is not None and not entry_oms.recovered:
            return self._refuse(rec, "oms_not_recovered",
                                "boot recovery has not completed")

        if route_live:
            # gates measure against the venue that will hold the risk
            try:
                equity = self.live_oms.adapter.account().equity
            except Exception as exc:
                return self._refuse(rec, "live_venue_unreachable", str(exc))
            if tier == "canary":
                # canary sizes to the venue minimum BEFORE validation —
                # the gates judge the order that will actually be placed
                sized = self._canary_sized(rec)
                if sized is None:
                    return self._refuse(
                        rec, "canary_sizing",
                        "venue minimum size unavailable for canary")
                rec = sized

        supported = (
            self.adapter.supported_symbols()
            if hasattr(self.adapter, "supported_symbols")
            else {getattr(rec, "symbol", "")}
        )
        portfolio = None
        if self.portfolio_risk_provider is not None:
            try:
                portfolio = self.portfolio_risk_provider()
            except Exception:
                logger.exception(
                    "portfolio risk context unavailable; VaR/correlation "
                    "caps are skipped for this order")
        check = validate_recommendation(rec, self.limits, equity, supported,
                                        portfolio=portfolio)
        if not check.ok:
            return self._refuse(rec, "validation_failed", "; ".join(check.reasons))

        if self.kill_switch.engaged:
            return self._refuse(rec, "kill_switch",
                                self.kill_switch.reason or "kill switch engaged")

        breaker = self.breaker.check()
        if breaker.tripped:
            return self._refuse(rec, "circuit_breaker", breaker.reason)

        if self.live_gates is not None:
            gate = self._check_live_gates(rec, equity, spread_bps)
            if not gate.ok:
                return self._refuse(rec, gate.gate, gate.reason)

        # P3-10: a gated ENTRY may be split into TWAP child orders; every
        # child flows through the same dispatch tail below. Disabled
        # (twap_slices=1, the default) this branch is never taken and the
        # single-order path is byte-identical to pre-P3-10 behavior.
        if self._twap_applies(rec, tier):
            return self._submit_twap(rec, tier, route_live)

        return self._dispatch_entry(rec, tier, route_live)

    def _dispatch_entry(self, rec: TradeRecommendation, tier: str,
                        route_live: bool) -> OrderResult:
        """The venue dispatch tail every gated entry takes — a single order
        directly, TWAP children one slice at a time."""
        if route_live:
            return self._submit_via_oms(rec, oms=self.live_oms, tier=tier)

        if self.oms is not None:
            result = self._submit_via_oms(rec, oms=self.oms, tier=tier)
            self._maybe_shadow(rec, tier, result)
            return result

        order = OrderRequest(
            idempotency_key=rec.id,
            symbol=rec.symbol,
            side=rec.action.value,
            quantity=rec.position_size.quantity,
            reference_price=rec.entry_price,
            stop_loss=rec.stop_loss,
            take_profits=tuple(tp.price for tp in rec.take_profits),
        )
        result = self._submit_with_retries(order)
        self.audit.append("order_result", self._stamped({
            "recommendation_id": rec.id,
            "status": result.status,
            "fill_price": result.fill_price,
            "filled_quantity": result.filled_quantity,
            "venue": result.venue,
            "reason": result.reason,
        }))
        if result.status == "filled":
            sign = 1 if order.side == "BUY" else -1
            self.local_book[order.symbol] = (
                self.local_book.get(order.symbol, 0.0) + sign * result.filled_quantity
            )
        self._maybe_shadow(rec, tier, result)
        return result

    # --- P3-10 TWAP entry slicing ---------------------------------------------

    def _twap_applies(self, rec, tier: str) -> bool:
        """Entries only, n>1 with a window, never canary (already clamped
        to the venue minimum — children would round below it)."""
        n = getattr(self.limits, "twap_slices", 1) or 1
        window = getattr(self.limits, "twap_window_minutes", None)
        if n <= 1 or not window or tier == "canary":
            return False
        return getattr(getattr(rec, "position_size", None),
                       "quantity", 0.0) > 0

    @staticmethod
    def _twap_slice_quantities(total: float, n: int) -> list[float]:
        """n ~equal children whose sum is exactly ``total`` — the rounding
        remainder lands on the last slice."""
        base = round(total / n, 10)
        return [base] * (n - 1) + [round(total - base * (n - 1), 10)]

    @staticmethod
    def _twap_child(rec, index: int, quantity: float):
        """Slice ``index`` as a full recommendation copy: same symbol, side,
        stop and TP ladder (per-slice protection), sliced quantity. Slice 1
        keeps the recommendation id so idempotent resubmission still
        dedupes; later slices get a deterministic ``#twapK`` suffix."""
        update = {
            "position_size": rec.position_size.model_copy(update={
                "quantity": quantity,
                "notional": (quantity * rec.entry_price
                             if rec.entry_price else None),
            }),
        }
        if index > 0:
            update["id"] = f"{rec.id}#twap{index + 1}"
        return rec.model_copy(update=update)

    def entry_coid(self, rec) -> str:
        """Deterministic client order id of the FIRST entry order this
        recommendation produces — the sliced child when TWAP applies, the
        recommendation itself otherwise (used by the service's pending
        live-TCA registration)."""
        from tradingagents.pro.execution import ids

        target = rec
        if self._twap_applies(rec, self.tier_for(rec.symbol)):
            quantity = self._twap_slice_quantities(
                rec.position_size.quantity, self.limits.twap_slices)[0]
            target = self._twap_child(rec, 0, quantity)
        return ids.client_order_id(target.id, ids.decision_hash(target),
                                   ids.ENTRY)

    def _twap_arrival(self, symbol: str, fallback: float | None):
        """Per-slice arrival price: live tick when the service wired a
        source, else the recommendation's entry reference."""
        if self.twap_price_fn is not None:
            try:
                price = self.twap_price_fn(symbol)
                if price:
                    return float(price)
            except Exception:
                logger.warning("twap arrival price unavailable for %s",
                               symbol, exc_info=True)
        return fallback

    def _submit_twap(self, rec, tier: str, route_live: bool) -> OrderResult:
        n = int(self.limits.twap_slices)
        quantities = self._twap_slice_quantities(
            rec.position_size.quantity, n)
        existing = self.twap_executions.get(rec.id)
        if existing is not None:
            # resubmission of a known decision: re-dispatch slice 1 only —
            # the venue/OMS dedupe answers, the schedule is NOT restarted
            return self._dispatch_entry(
                self._twap_child(rec, 0, quantities[0]), tier, route_live)
        state = {
            "recommendation_id": rec.id,
            "symbol": rec.symbol,
            "side": rec.action.value,
            "slices": n,
            "window_minutes": float(self.limits.twap_window_minutes),
            "total_quantity": rec.position_size.quantity,
            "quantities": quantities,
            "fills": [],
            "next_slice": 1,
            "failed": False,
            "complete": False,
            "timer": None,
        }
        self.twap_executions[rec.id] = state
        self.audit.append("twap_started", self._stamped({
            "recommendation_id": rec.id, "symbol": rec.symbol,
            "slices": n, "window_minutes": state["window_minutes"],
            "quantities": quantities, "tier": tier,
        }))
        result = self._twap_submit_slice(rec, state, 0, tier, route_live)
        if result.status not in ("filled", "submitted"):
            self._twap_abort(rec, state, 0,
                             reason=result.reason or result.status)
            return result
        self._twap_schedule_next(rec, state, tier, route_live)
        return result

    def _twap_submit_slice(self, rec, state: dict, index: int, tier: str,
                           route_live: bool) -> OrderResult:
        child = self._twap_child(rec, index, state["quantities"][index])
        arrival = self._twap_arrival(rec.symbol, rec.entry_price)
        result = self._dispatch_entry(child, tier, route_live)
        if result.status in ("filled", "submitted"):
            state["fills"].append({
                "slice": index + 1,
                "client_id": child.id,
                "arrival_mid": arrival,
                "fill_price": result.fill_price,
                "quantity": result.filled_quantity,
                "commission": result.commission,
                "status": result.status,
                "ts": utc_now().isoformat(),
            })
            self.audit.append("twap_slice", self._stamped({
                "recommendation_id": rec.id, "slice": index + 1,
                "of": state["slices"], "status": result.status,
                "quantity": result.filled_quantity,
                "fill_price": result.fill_price, "arrival_mid": arrival,
            }))
        return result

    def _twap_schedule_next(self, rec, state: dict, tier: str,
                            route_live: bool) -> None:
        index = state["next_slice"]
        if index >= state["slices"]:
            state["complete"] = True
            self.audit.append("twap_complete", self._stamped({
                "recommendation_id": rec.id,
                **(self.twap_summary(rec.id) or {}),
            }))
            return
        # slice k fires at k * window/n — even spread across the window
        delay = state["window_minutes"] * 60.0 / state["slices"]
        timer = threading.Timer(
            delay, self._twap_fire, args=(rec, state, index, tier, route_live))
        timer.daemon = True
        state["timer"] = timer
        timer.start()

    def _twap_fire(self, rec, state: dict, index: int, tier: str,
                   route_live: bool) -> None:
        """Timer-chain body for slices 2..n. Best-effort by design: process
        death leaves remaining slices unplaced (the filled quantity is
        already protected per slice); a detectable failure aborts the chain
        with a critical alert."""
        state["next_slice"] = index + 1
        try:
            if self.kill_switch.engaged:
                return self._twap_abort(
                    rec, state, index,
                    reason=self.kill_switch.reason or "kill switch engaged")
            result = self._twap_submit_slice(rec, state, index, tier,
                                             route_live)
        except Exception as exc:  # noqa: BLE001 — must alert, never die silent
            logger.exception("twap slice %d/%d for %s raised",
                             index + 1, state["slices"], rec.symbol)
            return self._twap_abort(rec, state, index, reason=str(exc))
        if result.status not in ("filled", "submitted"):
            return self._twap_abort(rec, state, index,
                                    reason=result.reason or result.status)
        self._twap_schedule_next(rec, state, tier, route_live)

    def _twap_abort(self, rec, state: dict, index: int, reason: str) -> None:
        state["failed"] = True
        state["complete"] = True
        unplaced = state["slices"] - index
        detail = (
            f"TWAP for {rec.symbol} aborted at slice {index + 1}/"
            f"{state['slices']}: {reason} — {unplaced} slice(s) unplaced; "
            "the filled quantity stays protected (per-slice venue stops / "
            "bar-close management)")
        logger.error(detail)
        self.audit.append("twap_slice_failed", self._stamped({
            "recommendation_id": rec.id, "symbol": rec.symbol,
            "slice": index + 1, "reason": reason,
            "unplaced_slices": unplaced,
        }))
        if self.alerts is not None:
            try:
                self.alerts.emit("critical", "twap_slice_failed", detail,
                                 symbol=rec.symbol)
            except Exception:
                logger.exception("twap failure alert delivery failed")

    def twap_summary(self, recommendation_id: str) -> dict | None:
        """P3-10 TCA aggregation: sliced execution (arrival mid of slice 1
        -> quantity-weighted average fill) vs the single-order counterfactual
        (the slice-1 fill price for the whole quantity), in signed bps —
        positive slippage = paid worse than arrival."""
        state = self.twap_executions.get(recommendation_id)
        if state is None:
            return None
        fills = [f for f in state["fills"]
                 if f["quantity"] > 0 and f["fill_price"] > 0]
        side = 1.0 if state["side"] == "BUY" else -1.0

        def bps(fill_price: float, reference) -> float | None:
            if not reference:
                return None
            return side * (fill_price - reference) / reference * 10_000.0

        summary = {
            "slices_planned": state["slices"],
            "slices_filled": len(fills),
            "window_minutes": state["window_minutes"],
            "intended_quantity": state["total_quantity"],
            "filled_quantity": round(sum(f["quantity"] for f in fills), 10),
            "commission": sum(f["commission"] for f in fills),
            "complete": state["complete"],
            "failed": state["failed"],
            "per_slice": [{
                "slice": f["slice"],
                "quantity": f["quantity"],
                "arrival_mid": f["arrival_mid"],
                "fill_price": f["fill_price"],
                "slippage_bps": bps(f["fill_price"], f["arrival_mid"]),
            } for f in fills],
        }
        if fills:
            arrival = fills[0]["arrival_mid"]
            total = sum(f["quantity"] for f in fills)
            wavg = sum(f["quantity"] * f["fill_price"] for f in fills) / total
            summary["arrival_mid"] = arrival
            summary["avg_fill_price"] = wavg
            summary["twap_slippage_bps"] = bps(wavg, arrival)
            summary["single_order_slippage_bps"] = bps(
                fills[0]["fill_price"], arrival)
            if summary["twap_slippage_bps"] is not None:
                summary["improvement_bps"] = (
                    summary["single_order_slippage_bps"]
                    - summary["twap_slippage_bps"])
        return summary

    def _canary_sized(self, rec):
        """A copy of the recommendation resized to the live venue's minimum
        viable size — canary proves the pipe with the smallest real order."""
        try:
            info = self.live_oms.adapter.instruments.get(rec.symbol)
            quantity = min(rec.position_size.quantity,
                           info.to_quantity(info.min_contracts))
            if quantity <= 0:
                return None
            return rec.model_copy(update={
                "position_size": rec.position_size.model_copy(update={
                    "quantity": quantity,
                    "notional": quantity * rec.entry_price,
                }),
            })
        except Exception:
            logger.exception("canary sizing failed")
            return None

    def _maybe_shadow(self, rec, tier: str, result: OrderResult) -> None:
        """Shadow mode: after a PAPER fill, record the would-have-been live
        fill so paper-vs-live divergence is measured (Phase 6)."""
        if (tier != "shadow" or self.shadow_tracker is None
                or result.status != "filled"):
            return
        try:
            self.shadow_tracker.record(
                symbol=rec.symbol, side=rec.action.value,
                quantity=result.filled_quantity,
                paper_fill_price=result.fill_price,
            )
        except Exception:
            logger.exception("shadow fill recording failed; continuing")

    def record_close(self, symbol: str, pnl: float) -> None:
        """Called by the position manager when a trade closes; feeds the
        breaker and clears the local book entry."""
        self.local_book.pop(symbol, None)
        self.breaker.record_trade_result(pnl)
        self.audit.append("position_closed", {"symbol": symbol, "pnl": pnl})
        state = self.breaker.check()
        if state.tripped:
            self.audit.append("circuit_breaker_tripped", {"reason": state.reason})

    def resolve_unknown_positions(self, marks: dict[str, float] | None = None,
                                  operator: str = "dashboard") -> dict:
        """Operator remediation for reconciliation drift: close every venue
        position the local book does not know (they have no surviving
        stop/TP plan, so adopting them would leave unmanaged risk on the
        book). Closes at the provided mark (falls back to the venue's own
        avg price), audited per symbol; never raises past a symbol.
        """
        marks = marks or {}
        flattened, errors = [], []
        for position in self.adapter.positions():
            if position.symbol in self.local_book:
                continue
            reference = marks.get(position.symbol, position.avg_price)
            try:
                result = self.adapter.close_position(position.symbol, reference)
                flattened.append(position.symbol)
                self.audit.append("drift_resolved_flatten", {
                    "symbol": position.symbol,
                    "operator": operator,
                    "reference_price": reference,
                    "filled": getattr(result, "filled_quantity", None),
                })
            except Exception as exc:  # noqa: BLE001 — per-symbol tolerance
                logger.warning("drift flatten failed for %s", position.symbol,
                               exc_info=True)
                errors.append(f"{position.symbol}: {exc}")
        return {"flattened": flattened, "errors": errors}

    def reconcile(self) -> ReconciliationReport:
        # Phase 6: a symbol holds risk on exactly one venue at a time
        # (its tier decides), so the truth set is the union of venues
        venue_positions = {p.symbol: p for p in self.adapter.positions()}
        if self.live_oms is not None:
            try:
                for p in self.live_oms.adapter.positions():
                    venue_positions[p.symbol] = p
            except Exception:
                logger.warning("live venue unreachable during reconcile; "
                               "its positions are missing from this pass",
                               exc_info=True)
        missing, unknown, mismatched = [], [], []
        for symbol, local_quantity in self.local_book.items():
            venue_position = venue_positions.get(symbol)
            if venue_position is None:
                missing.append(symbol)
                continue
            venue_signed = (
                venue_position.quantity
                if venue_position.side == "BUY"
                else -venue_position.quantity
            )
            if abs(venue_signed - local_quantity) > 1e-9:
                mismatched.append(
                    f"{symbol}: local {local_quantity} vs venue {venue_signed}"
                )
        for symbol in venue_positions:
            if symbol not in self.local_book:
                unknown.append(symbol)
        report = ReconciliationReport(
            in_sync=not (missing or unknown or mismatched),
            missing_on_venue=tuple(missing),
            unknown_on_venue=tuple(unknown),
            quantity_mismatches=tuple(mismatched),
        )
        self.audit.append("reconciliation", {
            "in_sync": report.in_sync,
            "missing_on_venue": list(report.missing_on_venue),
            "unknown_on_venue": list(report.unknown_on_venue),
            "quantity_mismatches": list(report.quantity_mismatches),
        })
        # retained so the dashboard can STATE the halt: drift blocks every new
        # entry (see service._run_once), and a silent halt on an armed system
        # reads exactly like a working one
        self.last_reconciliation = report
        return report

    # --- internals -----------------------------------------------------------

    def _check_live_gates(self, rec, equity: float, spread_bps: float | None):
        """Live-capital gates (Phase 3): account allocation, sizing, rate
        limits, error cooldowns — after the breaker, before any order."""
        notional = rec.position_size.quantity * rec.entry_price
        open_notional = sum(
            p.quantity * p.avg_price for p in self.adapter.positions()
        )
        risk_amount = (abs(rec.entry_price - rec.stop_loss)
                       * rec.position_size.quantity
                       if rec.stop_loss is not None else None)
        return self.live_gates.check_entry(
            notional=notional, equity=equity,
            open_notional=open_notional,
            open_positions=len(self.local_book),
            max_open_positions=self.limits.max_open_positions,
            risk_amount=risk_amount,
            max_risk_pct=self.limits.max_risk_per_trade_pct,
            spread_bps=spread_bps,
        )

    def _submit_via_oms(self, rec: TradeRecommendation, oms=None,
                        tier: str = "paper") -> OrderResult:
        """Phase-2 path: deterministic plan -> journaled OMS execution.
        Phase 6: ``oms`` selects the venue (paper vs live); canary clamps
        the quantity to the venue minimum and live tiers use venue-side
        bracket protection."""
        from tradingagents.pro.execution import ids
        from tradingagents.pro.execution.interface import BracketSpec, OrderState
        from tradingagents.pro.execution.orders import ExecutionPlan

        oms = oms if oms is not None else self.oms
        route_live = tier in ("canary", "live") and oms is self.live_oms
        quantity = rec.position_size.quantity  # canary already sized upstream

        bracket = None
        if rec.stop_loss is not None:
            bracket = BracketSpec(
                stop_loss_price=rec.stop_loss,
                take_profits=tuple((tp.price, tp.size_fraction)
                                   for tp in rec.take_profits),
            )
        order_type, limit_price = "market", None
        if self.live_gates is not None:
            limits = self.live_gates.limits
            notional = quantity * rec.entry_price
            if notional > limits.market_order_notional_cap:
                # Phase 3: big orders never cross the book unbounded —
                # limit at reference +/- max_cross_bps tolerance
                cross = limits.max_cross_bps / 10_000.0
                sign = 1 if rec.action.value == "BUY" else -1
                order_type = "limit"
                limit_price = rec.entry_price * (1 + sign * cross)
        plan = ExecutionPlan(
            run_id=rec.id,
            decision_hash=ids.decision_hash(rec),
            symbol=rec.symbol,
            side=rec.action.value,
            quantity=quantity,
            reference_price=rec.entry_price,
            bracket=bracket,
            protection_mode="venue_bracket" if route_live
            else self.protection_mode,
            order_type=order_type,
            limit_price=limit_price,
        )
        order = oms.execute(plan)
        if (self.live_gates is not None
                and order.state not in (OrderState.REJECTED,
                                        OrderState.ABANDONED)):
            self.live_gates.record_order()  # rate-limit accounting
        status = {
            OrderState.FILLED: "filled",
            OrderState.REJECTED: "rejected",
            OrderState.ABANDONED: "rejected",
            OrderState.CANCELED: "rejected",
        }.get(order.state, "submitted")
        result = OrderResult(
            status=status, idempotency_key=rec.id,
            venue=oms.adapter.name, venue_symbol=order.spec.venue_symbol,
            filled_quantity=order.filled_quantity,
            fill_price=order.avg_fill_price,
            commission=order.commission,
            reason=order.reason,
        )
        self.audit.append("order_result", self._stamped({
            "recommendation_id": rec.id,
            "status": result.status,
            "fill_price": result.fill_price,
            "filled_quantity": result.filled_quantity,
            "venue": result.venue,
            "reason": result.reason,
        }))
        if result.status == "filled":
            sign = 1 if rec.action.value == "BUY" else -1
            self.local_book[rec.symbol] = (
                self.local_book.get(rec.symbol, 0.0)
                + sign * result.filled_quantity
            )
        return result

    def apply_closed_trades(self) -> list:
        """Consume OMS-detected exits (venue-side stops/TPs/flattens) into
        the same book/breaker path bar-close exits use."""
        if self.oms is None:
            return []
        closed = self.oms.drain_closed()
        for trade in closed:
            self.record_close(trade.symbol, trade.pnl)
        return closed

    def _refuse(self, rec, stage: str, reason: str) -> OrderResult:
        self.audit.append(stage, self._stamped({
            "recommendation_id": getattr(rec, "id", None), "reason": reason,
        }))
        return OrderResult(
            status="rejected",
            idempotency_key=getattr(rec, "id", "n/a"),
            venue=self.adapter.name,
            reason=f"{stage}: {reason}",
        )

    def _submit_with_retries(self, order: OrderRequest) -> OrderResult:
        last_error: Exception | None = None
        for attempt in range(1 + self.max_retries):
            try:
                return self.adapter.submit(order)
            except AdapterError as exc:
                last_error = exc
                self.audit.append("submit_retry", {
                    "idempotency_key": order.idempotency_key,
                    "attempt": attempt + 1,
                    "error": str(exc),
                })
        return OrderResult(
            status="rejected", idempotency_key=order.idempotency_key,
            venue=self.adapter.name,
            reason=f"adapter failed after {1 + self.max_retries} attempts: {last_error}",
        )
