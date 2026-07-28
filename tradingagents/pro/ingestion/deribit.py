"""Deribit public volatility feed (keyless).

DVOL is Deribit's 30-day annualized implied-volatility index, published
for BTC and ETH as hourly candles on a free public endpoint. We surface
the latest level plus the ~24h change — the same shape the gold side
gets from GVZ (GOLD_VOL_INDEX / _CHANGE_1D).

ponytail: level + 1d change only; the full term structure / vol surface
is P3-09 (per-expiry instrument summaries, a much bigger pull).
"""

from __future__ import annotations

from datetime import datetime, timezone

from tradingagents.contracts import MetricReading, utc_now
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.pro.ingestion.base import HttpTransport, RequestsTransport

DERIBIT_BASE = "https://www.deribit.com/api/v2"
DVOL_CURRENCIES = ("BTC", "ETH")  # Deribit publishes DVOL for these only


class DeribitVolFeed:
    name = "deribit_dvol"

    def __init__(self, transport: HttpTransport | None = None,
                 currency: str = "BTC"):
        self._transport = transport or RequestsTransport()
        self._currency = currency

    def get_metrics(self) -> list[MetricReading]:
        end_ms = int(utc_now().timestamp() * 1000)
        payload = self._transport.get_json(
            f"{DERIBIT_BASE}/public/get_volatility_index_data",
            {
                "currency": self._currency,
                # 25h of hourly candles: last close = level, first ≈ 24h ago
                "start_timestamp": end_ms - 25 * 3600 * 1000,
                "end_timestamp": end_ms,
                "resolution": 3600,
            },
        )
        candles = (payload.get("result") or {}).get("data") or []
        if not candles:
            raise NoMarketDataError(
                self._currency, detail="Deribit returned no DVOL candles"
            )
        ts_ms, _o, _h, _l, close = candles[-1]
        as_of = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        readings = [
            MetricReading(name="DVOL", value=float(close), unit="% ann. IV",
                          as_of=as_of, source=self.name)
        ]
        if len(candles) >= 2:
            readings.append(
                MetricReading(name="DVOL_CHANGE_1D",
                              value=float(close) - float(candles[0][4]),
                              unit="% ann. IV", as_of=as_of, source=self.name)
            )
        return readings
