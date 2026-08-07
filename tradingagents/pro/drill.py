"""Kill-switch drill against the armed TESTNET venue (P3-01).

Proves, with a real (testnet) order, the whole safety chain the pilot
depends on: entry+stop atomicity, kill-switch engagement, emergency
flatten, disarm, and the audit trail. Refuses to run against anything
that is not a testnet adapter, refuses when the pair is not armed, and
ALWAYS attempts flatten + disarm even when a verification step fails —
a failed drill must not leave testnet exposure behind.

Entry points:
- ``run_kill_switch_drill(...)`` — the service-level drill (unit-tested
  against transport-stubbed adapters).
- ``python -m tradingagents.pro.drill`` — the operator CLI wrapped by
  ``scripts/pro_live_drill.sh``; requires a typed confirmation phrase.
"""

from __future__ import annotations

import hashlib
import logging
import time

logger = logging.getLogger(__name__)

CONFIRM_PHRASE = "RUN DRILL"
# stop distance for the drill order: far enough not to trigger while the
# drill runs, close enough to be an obviously-real protective order
DRILL_STOP_FRACTION = 0.02


class DrillRefused(Exception):
    """Preconditions for a safe drill are not met — nothing was sent."""


def run_kill_switch_drill(*, oms, arming, kill_switch, audit, operator: str,
                          symbol: str = "BTC-USD",
                          reference_price: float | None = None,
                          alerts=None, reset_kill_switch: bool = True) -> dict:
    """Place a minimum-size protected order, verify the venue stop is
    resting, engage the kill switch, flatten + disarm, and write a drill
    record to the hash-chained audit log. Returns
    ``{"passed": bool, "steps": [{"step", "ok", "detail"}, ...]}``."""
    adapter = oms.adapter

    # --- preconditions: refuse before any order exists -------------------------
    if not operator:
        raise DrillRefused("operator identity required for a drill")
    if "testnet" not in getattr(adapter, "name", ""):
        raise DrillRefused(
            f"drill refused: adapter {getattr(adapter, 'name', '?')!r} is "
            "not a testnet venue — the drill only ever runs on TESTNET")
    if not arming.is_live(symbol):
        raise DrillRefused(
            f"drill refused: {symbol} is not armed at a live tier — run "
            "the arming ceremony (tradingagents-pro arm-live) first")
    if not oms.recovered:
        raise DrillRefused("drill refused: OMS boot recovery has not run")

    steps: list[dict] = []

    def record(step: str, ok: bool, detail: str = "") -> bool:
        steps.append({"step": step, "ok": ok, "detail": detail})
        (logger.info if ok else logger.error)("drill %s: %s %s",
                                              "PASS" if ok else "FAIL",
                                              step, detail)
        return ok

    if reference_price is None:
        reference_price = adapter.mark_price(symbol)
    entered = False
    try:
        # --- 1. minimum-size protected entry -----------------------------------
        from tradingagents.pro.execution.interface import (
            BracketSpec,
            OrderState,
        )
        from tradingagents.pro.execution.orders import ExecutionPlan

        info = adapter.instruments.get(symbol)
        quantity = info.to_quantity(info.min_contracts)
        run_id = f"drill-{int(time.time())}"
        plan = ExecutionPlan(
            run_id=run_id,
            decision_hash=hashlib.sha256(
                f"{run_id}|{symbol}|{operator}".encode()).hexdigest(),
            symbol=symbol, side="BUY", quantity=quantity,
            reference_price=reference_price,
            bracket=BracketSpec(
                stop_loss_price=reference_price * (1 - DRILL_STOP_FRACTION)),
            protection_mode="venue_bracket",
        )
        order = oms.execute(plan)
        entered = order.state not in (OrderState.REJECTED,
                                      OrderState.ABANDONED)
        record("min_size_entry", entered,
               f"{quantity} {symbol} @~{reference_price} -> "
               f"{order.state.value} ({order.reason})")

        # --- 2. protective stop resting ON VENUE --------------------------------
        stop_resting = entered and adapter.has_resting_stop(symbol)
        record("venue_stop_resting", stop_resting,
               "reduce-only STOP_MARKET confirmed on venue" if stop_resting
               else "no resting venue stop found")

        # --- 3. engage the kill switch ------------------------------------------
        kill_switch.engage(f"kill_switch_drill:{operator}")
        record("kill_switch_engaged", kill_switch.engaged,
               kill_switch.reason)
    finally:
        # --- 4. flatten: cancel everything, close everything ---------------------
        # runs even when a step above failed or raised — a drill never
        # leaves exposure or resting orders behind
        errors: list[str] = []
        for managed in list(oms.orders.values()):
            if managed.sent and not managed.state.terminal:
                try:
                    oms._apply(managed,
                               adapter.cancel_order(managed.client_order_id))
                except Exception as exc:  # noqa: BLE001 — collect, continue
                    errors.append(f"cancel {managed.client_order_id}: {exc}")
        # venue-resting protections (the adapter-placed STOP_MARKET) are
        # not OMS-journaled orders — cancel them venue-side too
        try:
            for update in adapter.open_orders():
                if not update.state.terminal:
                    adapter.cancel_order(update.client_order_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cancel venue orders: {exc}")
        try:
            open_positions = adapter.positions()
        except Exception as exc:  # noqa: BLE001
            open_positions = []
            errors.append(f"positions read: {exc}")
        for position in open_positions:
            try:
                oms.flatten_position(
                    symbol=position.symbol, quantity=position.quantity,
                    side=position.side, reference_price=position.avg_price,
                    reason=f"kill_switch_drill:{operator}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"flatten {position.symbol}: {exc}")
        record("flatten_all", not errors, "; ".join(errors) or
               f"cancelled + flattened ({len(open_positions)} position(s))")

        # --- 5. verify flat -------------------------------------------------------
        try:
            flat = not adapter.positions()
        except Exception as exc:  # noqa: BLE001
            flat = False
            errors.append(f"flat verification: {exc}")
        record("verified_flat", flat,
               "venue reports no open positions" if flat
               else "POSITIONS REMAIN — manual intervention required")
        if not flat and alerts is not None:
            alerts.emit("critical", "drill_flatten_incomplete",
                        f"kill-switch drill left positions on {adapter.name}",
                        symbol=symbol)

        # --- 6. disarm -------------------------------------------------------------
        arming.disarm_all(f"kill_switch_drill:{operator}", operator)
        disarmed = not any(
            v["tier"] in ("canary", "live") for v in arming.status().values())
        record("disarmed_all", disarmed, "all pairs back to paper")

        if reset_kill_switch and flat:
            # drill over, book flat: hand the machine back (re-arming
            # still requires the full ceremony)
            kill_switch.reset(operator)
            record("kill_switch_reset", not kill_switch.engaged,
                   "explicit operator reset after verified-flat drill")

        passed = all(s["ok"] for s in steps)
        audit.append("kill_switch_drill", {
            "operator": operator, "symbol": symbol,
            "venue": getattr(adapter, "name", "?"),
            "passed": passed, "steps": steps,
            "entered": entered,
        })
    return {"passed": passed, "steps": steps}


def _build_and_run(symbol: str, operator: str, data_dir: str) -> dict:
    """Operator wiring for the CLI drill: TESTNET Binance adapter + the
    production arming/kill/audit state under ``data_dir``."""
    import os
    from pathlib import Path

    from tradingagents.contracts import LiveRiskLimits
    from tradingagents.pro.arming import ArmingStore
    from tradingagents.pro.execution import AuditLog, KillSwitch, OrderManager
    data_path = Path(data_dir)
    audit = AuditLog(data_path / "audit.jsonl")
    arming = ArmingStore(data_path / "arming.json", audit=audit)
    kill_switch = KillSwitch(data_path / "KILL")

    max_notional = LiveRiskLimits().max_notional_per_trade
    config_path = os.environ.get("PRO_LIVE_CONFIG", "")
    if config_path:
        from tradingagents.pro.live_config import load_live_config

        max_notional = load_live_config(config_path).risk.max_notional_per_trade

    # venue follows PRO_LIVE_EXCHANGE like the CLI ceremony (found live:
    # the drill was Binance-hardcoded while the pilot ran on Delta).
    # Either way the drill NEVER targets mainnet — testnet=True is fixed,
    # and run_kill_switch_drill refuses non-testnet adapter names anyway.
    exchange = os.environ.get("PRO_LIVE_EXCHANGE", "delta").strip().lower()
    if exchange == "binance":
        from tradingagents.pro.execution.adapters.binance_futures import (
            BinanceFuturesAdapter,
        )

        adapter = BinanceFuturesAdapter.from_env(
            testnet=True,
            armed_fn=lambda: arming.is_live(symbol),
            max_order_notional=max_notional,
            audit=audit, kill_switch=kill_switch,
        )
    else:
        from tradingagents.pro.execution.adapters.delta import DeltaAdapter

        adapter = DeltaAdapter.from_env(testnet=True)
    oms = OrderManager(adapter,
                       journal_path=data_path / "oms" / "drill_journal.jsonl",
                       audit=audit)
    oms.recover()
    return run_kill_switch_drill(
        oms=oms, arming=arming, kill_switch=kill_switch, audit=audit,
        operator=operator, symbol=symbol)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="P3-01 kill-switch drill (Binance FUTURES TESTNET only)")
    parser.add_argument("--symbol", default="BTC-USD")
    parser.add_argument("--operator", required=True)
    parser.add_argument("--data-dir", required=True,
                        help="the running deployment's /data volume path")
    parser.add_argument("--confirmed", action="store_true",
                        help="skip the interactive phrase (the wrapping "
                             "script already collected it)")
    args = parser.parse_args(argv)

    if not args.confirmed:
        typed = input(f"This places a REAL TESTNET order. Type "
                      f"'{CONFIRM_PHRASE}' to proceed: ").strip()
        if typed != CONFIRM_PHRASE:
            print("aborted: confirmation phrase mismatch")
            return 1
    from tradingagents.pro.execution.interface import (
        AdapterError,
        ExecutionNotEnabled,
    )

    try:
        result = _build_and_run(args.symbol, args.operator, args.data_dir)
    except (DrillRefused, AdapterError, ExecutionNotEnabled) as exc:
        print(f"REFUSED: {exc}")
        return 2
    for step in result["steps"]:
        print(f"  [{'PASS' if step['ok'] else 'FAIL'}] {step['step']}: "
              f"{step['detail']}")
    print(json.dumps({"passed": result["passed"]}))
    return 0 if result["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
