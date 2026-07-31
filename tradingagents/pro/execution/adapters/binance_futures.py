"""Binance USDⓈ-M FUTURES adapter — TESTNET-first live transport (P3-01).

The dust-pilot venue: minimum-size orders on testnet.binancefuture.com,
graduating (owner sign-off only) to mainnet dust. Design points on top of
the Delta adapter's transport discipline:

- **Inert unless armed.** The adapter takes an ``armed_fn`` (wired to the
  ArmingStore ceremony); when it is absent or returns False EVERY
  operation — reads included — raises ``ExecutionNotEnabled``. No arming
  ceremony, no venue traffic. This is defense in depth under the router's
  own per-pair tier gate.
- **Entry without a resting venue stop is impossible** (roadmap risk #48:
  an LLM/app outage must never leave a naked position). A non-reduce-only
  order without a ``BracketSpec`` is REJECTED before any network call.
  With one, ``place_order`` runs the entry and a reduce-only STOP_MARKET
  protective stop in the SAME submission flow; if the entry fills but the
  stop cannot be placed, the entry is flattened immediately (reduce-only
  market) and a critical alert fires. If even the flatten fails, the kill
  switch is engaged — a human must intervene, and no further entries are
  possible.
- **Hard notional cap** (from live_config's ``max_notional_per_trade``):
  an oversize entry is refused adapter-side before any API call —
  belt-and-braces under the LiveGateChain. Reduce-only orders are exempt
  (flattening must never be blocked by an entry cap).
- **Reduce-only TP ladder**: ``BracketSpec.take_profits`` fractions become
  reduce-only LIMIT orders after the stop is confirmed resting.
  Best-effort — a TP failure never unwinds a protected position.
- **Credentials at call time**: keys are read from
  ``BINANCE_TESTNET_API_KEY``/``BINANCE_TESTNET_API_SECRET`` per request
  (binance_auth), never stored on the instance, never logged. Mainnet
  additionally requires ``PRO_BINANCE_MAINNET_ACK=dust-pilot-approved``.
- No write retries here — ``AdapterError`` on transport doubt and the OMS
  owns resolve-by-coid (client order ids dedupe venue-side).
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from tradingagents.pro.execution.adapters.binance_auth import (
    read_credentials,
    redact,
    signed_query,
)
from tradingagents.pro.execution.instruments import InstrumentInfo, InstrumentService
from tradingagents.pro.execution.interface import (
    AccountState,
    AdapterCapabilities,
    AdapterError,
    BracketSpec,
    BrokerPosition,
    ExecutionNotEnabled,
    OrderSpec,
    OrderState,
    OrderUpdate,
)

logger = logging.getLogger(__name__)

TESTNET_BASE = "https://testnet.binancefuture.com"
PROD_BASE = "https://fapi.binance.com"
MAINNET_ACK_ENV = "PRO_BINANCE_MAINNET_ACK"
MAINNET_ACK_PHRASE = "dust-pilot-approved"

# canonical -> Binance futures venue symbols this adapter will trade
SYMBOL_MAP = {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT"}
_REVERSE_MAP = {v: k for k, v in SYMBOL_MAP.items()}

# Binance futures order status -> OMS state
_STATUS_MAP = {
    "NEW": OrderState.ACKED,
    "PARTIALLY_FILLED": OrderState.PARTIALLY_FILLED,
    "FILLED": OrderState.FILLED,
    "CANCELED": OrderState.CANCELED,
    "REJECTED": OrderState.REJECTED,
    "EXPIRED": OrderState.CANCELED,
    "EXPIRED_IN_MATCH": OrderState.CANCELED,
}

_STOP_SUFFIX = "sl"


class HttpResponse(Protocol):
    status_code: int
    headers: dict

    def json(self) -> dict: ...


class HttpClient(Protocol):
    """Injectable transport seam — every test runs against a fake
    implementing this; nothing in tests ever reaches a network."""

    def request(self, method: str, url: str, *, headers: dict | None = None,
                timeout: float = 30.0) -> HttpResponse: ...


class _RequestsClient:
    def __init__(self):
        import requests

        self._session = requests.Session()

    def request(self, method, url, *, headers=None, timeout=30.0):
        return self._session.request(
            method, url,
            headers={"User-Agent": "tradingagents-pro/0.1", **(headers or {})},
            timeout=timeout,
        )


class _SemanticError(Exception):
    """4xx the venue means: terminal for this request, never retried."""


def _fmt(value: float) -> str:
    """Decimal string without float-repr noise (Binance rejects 1e-05)."""
    return f"{value:.10f}".rstrip("0").rstrip(".")


class BinanceFuturesAdapter:
    """VenueAdapter v2 for Binance USDⓈ-M futures (testnet by default)."""

    def __init__(self, *, base_url: str = TESTNET_BASE,
                 armed_fn: Callable[[], bool] | None = None,
                 max_order_notional: float = 100.0,
                 http: HttpClient | None = None,
                 instruments: InstrumentService | None = None,
                 alerts=None, audit=None, kill_switch=None,
                 recv_window_ms: int = 5000, max_read_retries: int = 3):
        self._base = base_url.rstrip("/")
        self.testnet = "testnet" in self._base
        if not self.testnet and os.environ.get(
                MAINNET_ACK_ENV, "") != MAINNET_ACK_PHRASE:
            raise ExecutionNotEnabled(
                f"mainnet base URL requires {MAINNET_ACK_ENV}="
                f"{MAINNET_ACK_PHRASE!r} — dust pilot graduates to real "
                "funds only via the LIVE_PILOT_RUNBOOK owner sign-off"
            )
        # armed_fn=None means NEVER armed: constructing the adapter grants
        # nothing; only the arming ceremony's wiring supplies a real check
        self._armed_fn = armed_fn
        self.max_order_notional = float(max_order_notional)
        self._http = http or _RequestsClient()
        self._alerts = alerts
        self._audit = audit
        self._kill_switch = kill_switch
        self._recv_window = int(recv_window_ms)
        self._max_read_retries = max_read_retries
        self.name = f"binance_futures:{'testnet' if self.testnet else 'MAINNET'}"
        self.instruments = instruments or InstrumentService(
            fetch=self._fetch_instruments, fail_closed=True,
        )
        # local mirror: coid -> (venue_symbol, last known OrderUpdate).
        # Correctness lives on the venue (resolve-by-coid); this only
        # scopes poll_updates to orders we might still care about.
        self._known: dict[str, tuple[str, OrderUpdate]] = {}

    @classmethod
    def from_env(cls, testnet: bool = True, **kwargs) -> BinanceFuturesAdapter:
        """Validate credentials exist (without storing them) and build the
        adapter. TESTNET is the default and the only target until the
        owner signs off on mainnet dust."""
        read_credentials(testnet=testnet)  # presence check only
        base = TESTNET_BASE if testnet else PROD_BASE
        return cls(base_url=base, **kwargs)

    # --- arming gate ---------------------------------------------------------------

    def _require_armed(self) -> None:
        if self._armed_fn is None or not self._armed_fn():
            raise ExecutionNotEnabled(
                f"{self.name}: no pair is armed at a live tier — the "
                "arming ceremony (tradingagents-pro arm-live) is the only "
                "path to venue traffic"
            )

    # --- transport -------------------------------------------------------------------

    def _request(self, method: str, path: str, params: dict | None = None, *,
                 signed: bool = True, retryable: bool = True) -> dict | list:
        self._require_armed()
        attempts = (1 + self._max_read_retries) if retryable else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            # credentials read per attempt, at call time (never cached)
            key, secret = read_credentials(testnet=self.testnet)
            if signed:
                body = dict(params or {})
                body["recvWindow"] = self._recv_window
                body["timestamp"] = int(time.time() * 1000)
                query = signed_query(body, secret)
            else:
                from urllib.parse import urlencode

                query = urlencode(params or {})
            url = f"{self._base}{path}" + (f"?{query}" if query else "")
            try:
                response = self._http.request(
                    method, url, headers={"X-MBX-APIKEY": key})
            except Exception as exc:  # network layer
                last_error = AdapterError(
                    redact(f"{method} {path}: {exc}", key, secret))
                self._sleep_backoff(attempt)
                continue
            if response.status_code in (418, 429):
                retry_after = float(response.headers.get("Retry-After", "1"))
                last_error = AdapterError(
                    f"{response.status_code} from venue; retry in {retry_after}s")
                time.sleep(min(retry_after, 10.0))
                continue
            if response.status_code >= 500:
                last_error = AdapterError(f"{response.status_code} from venue")
                self._sleep_backoff(attempt)
                continue
            payload = response.json()
            if response.status_code >= 400:
                message = (payload.get("msg", str(payload))
                           if isinstance(payload, dict) else str(payload))
                code = (payload.get("code") if isinstance(payload, dict)
                        else None)
                raise _SemanticError(redact(f"[{code}] {message}", key, secret))
            return payload
        raise last_error or AdapterError(f"{method} {path}: exhausted retries")

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(min(0.25 * (2 ** attempt), 4.0) * (0.5 + random.random()))

    # --- instruments -------------------------------------------------------------

    def _fetch_instruments(self) -> dict[str, InstrumentInfo]:
        payload = self._request("GET", "/fapi/v1/exchangeInfo", signed=False)
        infos: dict[str, InstrumentInfo] = {}
        for product in payload.get("symbols", []):
            canonical = _REVERSE_MAP.get(product.get("symbol", ""))
            if canonical is None:
                continue
            filters = {f["filterType"]: f for f in product.get("filters", [])}
            step = float(filters.get("LOT_SIZE", {}).get("stepSize") or 0.001)
            min_qty = float(filters.get("LOT_SIZE", {}).get("minQty") or step)
            tick = float(filters.get("PRICE_FILTER", {}).get("tickSize") or 0.1)
            infos[canonical] = InstrumentInfo(
                symbol=canonical,
                venue_symbol=product["symbol"],
                tick_size=tick,
                contract_value=step,  # 1 "contract" = one lot step
                min_contracts=max(1, int(round(min_qty / step))),
                as_of=time.time(),
            )
        missing = set(SYMBOL_MAP) - set(infos)
        if missing:
            raise AdapterError(f"venue exchangeInfo missing {sorted(missing)}")
        return infos

    def supported_symbols(self) -> set[str]:
        return set(SYMBOL_MAP)

    def capabilities(self) -> AdapterCapabilities:
        # native_bracket=True is the honest contract even though Binance
        # has no single-request bracket: place_order itself guarantees
        # entry+stop as one unit (or no position at all), so the OMS must
        # not layer its synthetic limit-order "stop" on top.
        return AdapterCapabilities(
            native_bracket=True,
            terminal_on_place=False,
            supports_client_oid_lookup=True,
            supports_streams=False,
        )

    # --- order mapping -----------------------------------------------------------

    def _to_update(self, order: dict) -> OrderUpdate:
        venue_symbol = order.get("symbol", "")
        canonical = _REVERSE_MAP.get(venue_symbol)
        update = OrderUpdate(
            client_order_id=order.get("clientOrderId", ""),
            state=_STATUS_MAP.get(order.get("status", ""), OrderState.UNKNOWN),
            venue_order_id=str(order.get("orderId", "")),
            filled_quantity=float(order.get("executedQty") or 0),
            avg_fill_price=float(order.get("avgPrice") or 0),
            commission=0.0,  # futures order payloads carry no fee; TCA
                             # slippage is measured independently (P1-04)
            reason=str(order.get("msg") or ""),
            ts=datetime.now(timezone.utc),
            raw={**order, "canonical_symbol": canonical},
        )
        if update.client_order_id:
            self._known[update.client_order_id] = (venue_symbol, update)
        return update

    # --- the money path ------------------------------------------------------------

    def place_order(self, spec: OrderSpec,
                    bracket: BracketSpec | None = None) -> OrderUpdate:
        """Entry + protective stop as one unit.

        Outcomes (and only these):
        1. entry working/filled AND a reduce-only STOP_MARKET resting on
           the venue (+ best-effort reduce-only TP ladder), or
        2. no order and REJECTED (gate refusal / venue 4xx), or
        3. entry flattened + critical alert (stop placement failed), or
        4. ``AdapterError`` before the entry landed — the OMS resolves by
           coid and re-enters this same flow, so a retried entry still
           gets its stop.
        Reduce-only specs (stops, TPs, flattens) go straight through.
        """
        self._require_armed()
        info = self.instruments.get(spec.symbol)
        contracts = info.to_contracts(spec.quantity)
        if contracts < info.min_contracts:
            return OrderUpdate(
                client_order_id=spec.client_order_id,
                state=OrderState.REJECTED,
                reason=f"{spec.quantity} {spec.symbol} rounds to {contracts} "
                       f"lots, below venue minimum {info.min_contracts}",
            )
        quantity = info.to_quantity(contracts)

        if not spec.reduce_only:
            # risk #48: an entry that could rest without venue-side
            # protection is refused before it exists
            if bracket is None or bracket.stop_loss_price <= 0:
                return OrderUpdate(
                    client_order_id=spec.client_order_id,
                    state=OrderState.REJECTED,
                    reason="naked entry refused: a protective stop "
                           "(BracketSpec.stop_loss_price) is mandatory on "
                           "the live venue",
                )
            reference = (spec.limit_price
                         if spec.order_type == "limit" and spec.limit_price
                         else spec.reference_price)
            notional = quantity * reference
            if notional > self.max_order_notional:
                return OrderUpdate(
                    client_order_id=spec.client_order_id,
                    state=OrderState.REJECTED,
                    reason=f"notional {notional:.2f} exceeds the live "
                           f"dust-pilot cap {self.max_order_notional:.2f} "
                           "(live_config max_notional_per_trade)",
                )

        entry = self._submit_single(spec, info, quantity)
        if spec.reduce_only or bracket is None or entry.state in (
                OrderState.REJECTED,):
            return entry

        # protective stop in the same submission flow — no return path
        # leaves a working entry without it
        stop = self._place_protective_stop(spec, bracket, info, quantity)
        if stop is None or stop.state in (OrderState.REJECTED,
                                          OrderState.UNKNOWN):
            return self._flatten_unprotected(spec, entry, info,
                                             stop_reason=getattr(
                                                 stop, "reason", "transport"))
        self._place_tp_ladder(spec, bracket, info, contracts)
        return entry

    def _submit_single(self, spec: OrderSpec, info: InstrumentInfo,
                       quantity: float) -> OrderUpdate:
        params: dict = {
            "symbol": info.venue_symbol,
            "side": spec.side,
            "type": "LIMIT" if spec.order_type == "limit" else "MARKET",
            "quantity": _fmt(quantity),
            "newClientOrderId": spec.client_order_id,
            "newOrderRespType": "RESULT",  # market fills report avgPrice
        }
        if spec.order_type == "limit" and spec.limit_price is not None:
            params["price"] = _fmt(info.round_price(spec.limit_price))
            params["timeInForce"] = spec.time_in_force.upper()
        if spec.reduce_only:
            params["reduceOnly"] = "true"
        try:
            result = self._request("POST", "/fapi/v1/order", params,
                                   retryable=False)  # OMS owns resolve
        except _SemanticError as exc:
            return OrderUpdate(client_order_id=spec.client_order_id,
                               state=OrderState.REJECTED, reason=str(exc))
        return self._to_update(result)

    def _place_protective_stop(self, spec: OrderSpec, bracket: BracketSpec,
                               info: InstrumentInfo,
                               quantity: float) -> OrderUpdate | None:
        exit_side = "SELL" if spec.side == "BUY" else "BUY"
        params = {
            "symbol": info.venue_symbol,
            "side": exit_side,
            "type": "STOP_MARKET",
            "stopPrice": _fmt(info.round_price(bracket.stop_loss_price)),
            "quantity": _fmt(quantity),
            "reduceOnly": "true",
            "newClientOrderId": f"{spec.client_order_id}{_STOP_SUFFIX}",
            "workingType": ("MARK_PRICE" if bracket.stop_trigger == "mark"
                            else "CONTRACT_PRICE"),
        }
        try:
            result = self._request("POST", "/fapi/v1/order", params,
                                   retryable=False)
        except _SemanticError as exc:
            return OrderUpdate(
                client_order_id=params["newClientOrderId"],
                state=OrderState.REJECTED, reason=str(exc))
        except AdapterError as exc:
            # one resolve attempt: the stop may have landed despite the
            # transport doubt — only a confirmed absence triggers flatten
            logger.warning("stop send uncertain (%s); resolving by coid", exc)
            try:
                return self.get_order(params["newClientOrderId"])
            except AdapterError:
                return None
        return self._to_update(result)

    def _flatten_unprotected(self, spec: OrderSpec, entry: OrderUpdate,
                             info: InstrumentInfo,
                             stop_reason: str) -> OrderUpdate:
        """The entry exists but its stop does not: close the exposure NOW,
        alert, and report the unit as rejected (net effect: no position)."""
        detail = (f"protective stop placement failed for "
                  f"{spec.symbol} ({stop_reason}); flattening entry "
                  f"{spec.client_order_id}")
        logger.critical(detail)
        if self._audit is not None:
            self._audit.append("live_stop_placement_failed", {
                "coid": spec.client_order_id, "symbol": spec.symbol,
                "reason": stop_reason,
                "entry_filled": entry.filled_quantity,
            })
        flatten_error: Exception | None = None
        try:
            if entry.filled_quantity > 0:
                self._request("POST", "/fapi/v1/order", {
                    "symbol": info.venue_symbol,
                    "side": "SELL" if spec.side == "BUY" else "BUY",
                    "type": "MARKET",
                    "quantity": _fmt(entry.filled_quantity),
                    "reduceOnly": "true",
                    "newClientOrderId": f"{spec.client_order_id}xx",
                    "newOrderRespType": "RESULT",
                }, retryable=False)
            else:
                # nothing filled yet (resting limit): cancel it instead
                self._request("DELETE", "/fapi/v1/order", {
                    "symbol": info.venue_symbol,
                    "origClientOrderId": spec.client_order_id,
                }, retryable=False)
        except Exception as exc:  # noqa: BLE001 — last line of defense next
            flatten_error = exc
        if flatten_error is not None:
            # could not close a naked position: stop the machine
            if self._kill_switch is not None:
                self._kill_switch.engage(
                    f"naked position on {self.name}: stop AND flatten "
                    f"failed for {spec.symbol}")
            if self._alerts is not None:
                self._alerts.emit(
                    "critical", "live_naked_position",
                    f"MANUAL INTERVENTION: {spec.symbol} entry has no stop "
                    f"and flatten failed ({flatten_error}) — kill switch "
                    "engaged", symbol=spec.symbol)
            if self._audit is not None:
                self._audit.append("live_flatten_failed", {
                    "coid": spec.client_order_id, "symbol": spec.symbol,
                    "error": str(flatten_error),
                })
        elif self._alerts is not None:
            self._alerts.emit(
                "critical", "live_entry_flattened",
                f"{spec.symbol} entry flattened: protective stop could not "
                f"be placed ({stop_reason})", symbol=spec.symbol)
        return OrderUpdate(
            client_order_id=spec.client_order_id,
            state=OrderState.REJECTED,
            venue_order_id=entry.venue_order_id,
            reason=f"protective stop placement failed ({stop_reason}); "
                   + ("entry flattened"
                      if flatten_error is None
                      else f"FLATTEN FAILED ({flatten_error}) — kill switch "
                           "engaged, manual intervention required"),
            raw={"entry": entry.raw},
        )

    def _place_tp_ladder(self, spec: OrderSpec, bracket: BracketSpec,
                         info: InstrumentInfo, contracts: int) -> None:
        """Reduce-only limit ladder at the bracket's TP levels. Best-effort:
        the stop is already resting, so a TP failure logs and moves on."""
        exit_side = "SELL" if spec.side == "BUY" else "BUY"
        for index, (price, fraction) in enumerate(bracket.take_profits):
            leg_contracts = int(math.floor(contracts * fraction + 1e-9))
            if leg_contracts < 1:
                continue
            try:
                self._request("POST", "/fapi/v1/order", {
                    "symbol": info.venue_symbol,
                    "side": exit_side,
                    "type": "LIMIT",
                    "price": _fmt(info.round_price(price)),
                    "quantity": _fmt(info.to_quantity(leg_contracts)),
                    "timeInForce": "GTC",
                    "reduceOnly": "true",
                    "newClientOrderId": f"{spec.client_order_id}tp{index + 1}",
                }, retryable=False)
            except Exception:  # noqa: BLE001 — best-effort by contract
                logger.warning("reduce-only TP leg %d failed for %s",
                               index + 1, spec.symbol, exc_info=True)

    # --- order reads ---------------------------------------------------------------

    def cancel_order(self, client_order_id: str) -> OrderUpdate:
        self._require_armed()
        venue_symbol = self._symbol_for(client_order_id)
        if venue_symbol is None:
            return OrderUpdate(client_order_id=client_order_id,
                               state=OrderState.REJECTED,
                               reason="unknown order")
        try:
            result = self._request("DELETE", "/fapi/v1/order", {
                "symbol": venue_symbol,
                "origClientOrderId": client_order_id,
            }, retryable=False)
        except _SemanticError as exc:
            existing = self.get_order(client_order_id)
            if existing is not None and existing.state.terminal:
                return existing  # already done — report current truth
            return OrderUpdate(client_order_id=client_order_id,
                               state=OrderState.UNKNOWN, reason=str(exc))
        return self._to_update(result)

    def get_order(self, client_order_id: str) -> OrderUpdate | None:
        venue_symbol = self._symbol_for(client_order_id)
        candidates = ([venue_symbol] if venue_symbol
                      else list(SYMBOL_MAP.values()))
        for symbol in candidates:
            try:
                result = self._request("GET", "/fapi/v1/order", {
                    "symbol": symbol,
                    "origClientOrderId": client_order_id,
                })
            except _SemanticError:
                continue  # -2013 order does not exist (on this symbol)
            if isinstance(result, dict) and result.get("orderId") is not None:
                return self._to_update(result)
        return None

    def _symbol_for(self, client_order_id: str) -> str | None:
        known = self._known.get(client_order_id)
        return known[0] if known else None

    def open_orders(self) -> list[OrderUpdate]:
        result = self._request("GET", "/fapi/v1/openOrders", {})
        return [self._to_update(o) for o in result]

    def poll_updates(self, since: datetime) -> list[OrderUpdate]:
        updates = {u.client_order_id: u for u in self.open_orders()}
        # resolve locally-known orders that stopped being open (filled or
        # canceled since the last poll) — the OMS needs their terminal truth
        for coid, (_, last) in list(self._known.items()):
            if coid in updates or last.state.terminal:
                continue
            resolved = self.get_order(coid)
            if resolved is not None:
                updates[coid] = resolved
        return sorted(updates.values(), key=lambda u: u.ts)

    def has_resting_stop(self, symbol: str) -> bool:
        """True when a reduce-only STOP_MARKET is working for the symbol —
        the drill's 'protection is on the venue' verification."""
        venue_symbol = SYMBOL_MAP.get(symbol, symbol)
        for update in self.open_orders():
            raw = update.raw
            if (raw.get("symbol") == venue_symbol
                    and raw.get("type") == "STOP_MARKET"
                    and str(raw.get("reduceOnly")).lower() == "true"
                    and not update.state.terminal):
                return True
        return False

    # --- account -------------------------------------------------------------------

    def positions(self) -> list[BrokerPosition]:
        result = self._request("GET", "/fapi/v2/positionRisk", {})
        out = []
        for row in result:
            canonical = _REVERSE_MAP.get(row.get("symbol", ""))
            amount = float(row.get("positionAmt") or 0)
            if canonical is None or amount == 0:
                continue
            out.append(BrokerPosition(
                symbol=canonical,
                side="BUY" if amount > 0 else "SELL",
                quantity=abs(amount),
                avg_price=float(row.get("entryPrice") or 0),
            ))
        return out

    def account(self) -> AccountState:
        result = self._request("GET", "/fapi/v2/account", {})
        return AccountState(
            venue=self.name,
            equity=float(result.get("totalMarginBalance") or 0),
            cash=float(result.get("availableBalance") or 0),
            positions=tuple(self.positions()),
        )

    def mark_price(self, symbol: str) -> float:
        """Venue mark price — the drill's reference price source."""
        venue_symbol = SYMBOL_MAP.get(symbol, symbol)
        result = self._request("GET", "/fapi/v1/premiumIndex",
                               {"symbol": venue_symbol}, signed=False)
        return float(result.get("markPrice") or 0)

    # --- v1 surface (emergency flatten / drift resolution) --------------------------

    def close_position(self, symbol: str, reference_price: float):
        """Reduce-only market close of whatever the venue reports —
        used by emergency_flatten and resolve_unknown_positions."""
        from tradingagents.pro.execution.interface import OrderResult

        self._require_armed()
        position = next((p for p in self.positions() if p.symbol == symbol),
                        None)
        if position is None:
            return OrderResult(status="rejected",
                               idempotency_key=f"close:{symbol}",
                               venue=self.name,
                               reason=f"no open position in {symbol}")
        info = self.instruments.get(symbol)
        result = self._request("POST", "/fapi/v1/order", {
            "symbol": info.venue_symbol,
            "side": "SELL" if position.side == "BUY" else "BUY",
            "type": "MARKET",
            "quantity": _fmt(position.quantity),
            "reduceOnly": "true",
            "newClientOrderId": f"close{int(time.time() * 1000)}",
            "newOrderRespType": "RESULT",
        }, retryable=False)
        update = self._to_update(result)
        return OrderResult(
            status="filled" if update.state is OrderState.FILLED else "submitted",
            idempotency_key=f"close:{symbol}", venue=self.name,
            venue_symbol=info.venue_symbol,
            filled_quantity=update.filled_quantity,
            fill_price=update.avg_fill_price,
        )
