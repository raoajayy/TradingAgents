"""Binance futures liquidation stream + open-interest deltas (P2-11).

SIGNAL-GRADE ONLY — every magnitude here is disclaimed by construction:
Binance's ``<symbol>@forceOrder`` websocket pushes AT MOST ONE liquidation
order per second per symbol (their documented "snapshot" sampling). Counts
are therefore an intensity signal and notionals a FLOOR — never total
liquidation volume. Unit strings carry the "(sampled)" qualifier so the
disclaimer travels with the reading into prompts, alerts, and the UI.

Transport decision: ``websockets==15.0.1`` is in requirements.lock, so the
stream uses its synchronous client inside a daemon thread (same lifecycle
shape as ``QuoteTickPoller``: stop event, reconnect with exponential
backoff, never raises out of the thread). Open interest has no delta
endpoint, so ``GET /fapi/v1/openInterest`` is polled once a minute on a
second daemon thread and the 1h delta is computed from retained samples.

Tests inject recorded frames via ``ingest_frame`` and OI values via
``record_oi``/``poll_oi_once`` with a stub transport — no sockets open
unless ``start()`` runs (the intel wiring is the only autostart caller).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone

from tradingagents.contracts import MetricReading
from tradingagents.dataflows.errors import NoMarketDataError, VendorRateLimitError
from tradingagents.pro.ingestion.base import HttpTransport, RequestsTransport

logger = logging.getLogger(__name__)

WS_BASE = "wss://fstream.binance.com/ws"
FUTURES_BASE = "https://fapi.binance.com"

RING_WINDOW_S = 2 * 3600.0       # bounded in-memory ring: last 2h of events
METRIC_WINDOW_S = 3600.0         # *_1H metrics read the trailing hour
OI_POLL_INTERVAL_S = 60.0
OI_MIN_SPAN_S = 45 * 60.0        # honest 1h delta needs >= 45min of history
OI_RETENTION_S = RING_WINDOW_S

# process-wide shared streams (one websocket + one OI poller per symbol,
# no matter how many consumers): Intel and the snapshot builders must read
# the SAME ring, and a second stream would double the vendor connections.
_SHARED_STREAMS: dict[str, LiquidationStream] = {}
_SHARED_LOCK = threading.Lock()


def shared_stream(symbol: str = "BTCUSDT") -> LiquidationStream:
    """Process-wide singleton LiquidationStream per symbol.

    Construction (here) never opens a socket — the returned stream carries
    ``autostart=True``, so its websocket/OI threads start on the first
    ``get_metrics`` call (i.e. the first real snapshot or intel read),
    keeping imports and hermetic tests socket-free."""
    key = symbol.upper()
    with _SHARED_LOCK:
        stream = _SHARED_STREAMS.get(key)
        if stream is None:
            stream = LiquidationStream(key, autostart=True)
            _SHARED_STREAMS[key] = stream
        return stream


class LiquidationStream:
    """Sampled forceOrder events + polled open interest for ONE symbol.

    MetricsFeed-compatible (``get_metrics``); additionally serves
    ``price_buckets`` for the Intel heat strip. Construction never opens a
    socket — ``start()`` does, and only the dashboard wiring calls it
    (``autostart=True`` makes the first ``get_metrics`` call start it).
    """

    name = "binance_liquidations"

    def __init__(
        self,
        symbol: str = "BTCUSDT",
        transport: HttpTransport | None = None,
        *,
        autostart: bool = False,
        ring_window: float = RING_WINDOW_S,
        oi_poll_interval: float = OI_POLL_INTERVAL_S,
        backoff_cap: float = 60.0,
        now: Callable[[], float] = time.time,
    ):
        self.symbol = symbol.upper()
        self._transport = transport or RequestsTransport()
        self._autostart = autostart
        self._ring_window = ring_window
        self._oi_poll_interval = oi_poll_interval
        self._backoff_cap = backoff_cap
        self._now = now
        self._lock = threading.Lock()
        # ring of {ts, side, price, qty, notional}; pruned to ring_window
        self._events: deque[dict] = deque()
        self._oi_samples: deque[tuple[float, float]] = deque()  # (epoch, oi)
        self._stop = threading.Event()
        self._ws_thread: threading.Thread | None = None
        self._oi_thread: threading.Thread | None = None

    # --- lifecycle (QuoteTickPoller shape) --------------------------------

    @property
    def active(self) -> bool:
        return self._ws_thread is not None and not self._stop.is_set()

    def start(self) -> None:
        # hermetic escape hatch: tests must never open real sockets
        import os

        if os.environ.get("PRO_DISABLE_LIQUIDATION_STREAM") == "1":
            return
        if self._ws_thread is not None:
            return
        self._stop.clear()
        self._ws_thread = threading.Thread(
            target=self._run_ws, name=f"liq-ws-{self.symbol}", daemon=True
        )
        self._oi_thread = threading.Thread(
            target=self._run_oi, name=f"liq-oi-{self.symbol}", daemon=True
        )
        self._ws_thread.start()
        self._oi_thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for thread in (self._ws_thread, self._oi_thread):
            if thread is not None:
                thread.join(timeout=timeout)
        self._ws_thread = None
        self._oi_thread = None

    # --- ingestion (tests call these directly with recorded data) ---------

    def ingest_frame(self, payload: dict) -> None:
        """One forceOrder websocket frame → one ring event. Malformed or
        foreign-symbol frames are dropped silently — the stream thread must
        never die on vendor noise."""
        try:
            order = payload.get("o") or {}
            if str(order.get("s", "")).upper() != self.symbol:
                return
            # ap = average fill price (fallback: order price); z = filled qty
            price = float(order.get("ap") or order.get("p") or 0.0)
            qty = float(order.get("z") or order.get("q") or 0.0)
            side = str(order.get("S", "")).upper()
            if price <= 0 or qty <= 0 or side not in ("BUY", "SELL"):
                return
            ts = float(order.get("T") or payload.get("E") or self._now() * 1000) / 1000.0
        except (TypeError, ValueError):
            return
        event = {"ts": ts, "side": side, "price": price, "qty": qty,
                 "notional": price * qty}
        with self._lock:
            self._events.append(event)
            self._prune_locked()

    def record_oi(self, value: float, ts: float | None = None) -> None:
        ts = self._now() if ts is None else ts
        with self._lock:
            self._oi_samples.append((ts, float(value)))
            horizon = self._now() - OI_RETENTION_S
            while self._oi_samples and self._oi_samples[0][0] < horizon:
                self._oi_samples.popleft()

    def poll_oi_once(self) -> None:
        data = self._transport.get_json(
            f"{FUTURES_BASE}/fapi/v1/openInterest", {"symbol": self.symbol}
        )
        ts = data.get("time")
        self.record_oi(
            float(data["openInterest"]),
            float(ts) / 1000.0 if ts is not None else None,
        )

    def _prune_locked(self) -> None:
        horizon = self._now() - self._ring_window
        while self._events and self._events[0]["ts"] < horizon:
            self._events.popleft()

    # --- views -------------------------------------------------------------

    def get_metrics(self) -> list[MetricReading]:
        """MetricsFeed: trailing-1h intensity/notional/ratio + OI delta.

        Raises NoMarketDataError while warming up (no events AND no usable
        OI span) — the intel layer discloses that in missing_feeds instead
        of faking zeros (an empty hour after a 2h-old event IS a real zero
        and is served as one)."""
        if self._autostart:
            self.start()
        now = self._now()
        with self._lock:
            self._prune_locked()
            events = [e for e in self._events if e["ts"] >= now - METRIC_WINDOW_S]
            had_any_event = bool(self._events)
            oi = list(self._oi_samples)

        readings: list[MetricReading] = []
        if events or had_any_event:
            as_of = datetime.fromtimestamp(
                max((e["ts"] for e in events), default=now), tz=timezone.utc
            )
            buy = sum(e["notional"] for e in events if e["side"] == "BUY")
            sell = sum(e["notional"] for e in events if e["side"] == "SELL")
            readings.append(MetricReading(
                name="LIQ_INTENSITY_1H", value=float(len(events)),
                unit="events/h (sampled)", as_of=as_of, source=self.name,
            ))
            readings.append(MetricReading(
                name="LIQ_NOTIONAL_1H", value=buy + sell,
                unit="USD/h (sampled floor)", as_of=as_of, source=self.name,
            ))
            if buy > 0 and sell > 0:
                readings.append(MetricReading(
                    name="LIQ_BUY_SELL_RATIO_1H", value=buy / sell,
                    unit="ratio (sampled)", as_of=as_of, source=self.name,
                ))

        delta = self._oi_delta(oi, now)
        if delta is not None:
            readings.append(MetricReading(
                name="OI_DELTA_1H", value=delta,
                unit="% (1h)",
                as_of=datetime.fromtimestamp(oi[-1][0], tz=timezone.utc),
                source=self.name,
            ))

        if not readings:
            raise NoMarketDataError(
                self.symbol,
                detail="liquidation stream warming up — no sampled events yet",
            )
        return readings

    @staticmethod
    def _oi_delta(samples: list[tuple[float, float]], now: float) -> float | None:
        """% change vs the oldest sample inside the 1h window, only when
        the span is honest (>= 45min) — a 2-minute-old baseline must not
        masquerade as an hourly delta."""
        if len(samples) < 2:
            return None
        latest_ts, latest = samples[-1]
        window = [(ts, v) for ts, v in samples if ts >= latest_ts - METRIC_WINDOW_S]
        base_ts, base = window[0]
        if latest_ts - base_ts < OI_MIN_SPAN_S or base == 0:
            return None
        return (latest - base) / base * 100.0

    def price_buckets(self, n: int = 12) -> list[dict]:
        """Equal-width price buckets over the full ring (2h) for the heat
        strip: [{low, high, notional, count}], empty list when no events.
        Notionals are sampled floors — the UI repeats the disclaimer."""
        with self._lock:
            self._prune_locked()
            events = list(self._events)
        if not events:
            return []
        n = max(1, n)
        lo = min(e["price"] for e in events)
        hi = max(e["price"] for e in events)
        if hi == lo:
            return [{"low": lo, "high": hi,
                     "notional": sum(e["notional"] for e in events),
                     "count": len(events)}]
        width = (hi - lo) / n
        buckets = [{"low": lo + i * width, "high": lo + (i + 1) * width,
                    "notional": 0.0, "count": 0} for i in range(n)]
        for event in events:
            idx = min(n - 1, int((event["price"] - lo) / width))
            buckets[idx]["notional"] += event["notional"]
            buckets[idx]["count"] += 1
        return buckets

    # --- threads (never raise out; reconnect with backoff) -----------------

    def _run_ws(self) -> None:
        # websockets==15.0.1 (requirements.lock) — sync client in a thread
        from websockets.sync.client import connect

        url = f"{WS_BASE}/{self.symbol.lower()}@forceOrder"
        delay = 1.0
        while not self._stop.is_set():
            try:
                with connect(url, open_timeout=15, close_timeout=5) as ws:
                    delay = 1.0
                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=5.0)
                        except TimeoutError:
                            continue  # idle stream is normal; re-check stop
                        self.ingest_frame(json.loads(raw))
            except Exception:
                if self._stop.is_set():
                    break
                logger.warning(
                    "forceOrder stream for %s dropped; reconnecting in %.0fs",
                    self.symbol, delay, exc_info=True,
                )
                if self._stop.wait(delay):
                    break
                delay = min(delay * 2, self._backoff_cap)

    def _run_oi(self) -> None:
        delay = self._oi_poll_interval
        while not self._stop.is_set():
            try:
                self.poll_oi_once()
                delay = self._oi_poll_interval
            except VendorRateLimitError:
                delay = min(max(delay * 2, self._oi_poll_interval),
                            self._backoff_cap)
                logger.warning("%s openInterest poll throttled; backing off "
                               "to %.0fs", self.symbol, delay)
            except Exception:
                logger.warning("%s openInterest poll failed; continuing",
                               self.symbol, exc_info=True)
                delay = self._oi_poll_interval
            if self._stop.wait(delay):
                break
