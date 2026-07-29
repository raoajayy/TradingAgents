"""Token Terminal fundamentals (free tier, keyed) — P1-05d.

Protocol revenue/fee and usage fundamentals for the traded L1s: the
"cash-flow" view the on-chain feeds don't cover. Free tier requires an
API key (tokenterminal.com → settings → API); without one the feed
reports unavailable and everything else proceeds — the adapter is
explicitly optional (lowest priority of the P1-05 sprint).

ponytail: latest daily value only; historical series and per-contract
breakdowns are paid-tier and out of scope.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from tradingagents.contracts import MetricReading
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.pro.ingestion.base import HttpTransport, RequestsTransport

TOKEN_TERMINAL_BASE = "https://api.tokenterminal.com/v2"
KEY_ENV = "TOKENTERMINAL_API_KEY"

# CoinMetrics-style asset code -> Token Terminal project id
PROJECT_IDS = {"btc": "bitcoin", "eth": "ethereum", "sol": "solana"}

# Token Terminal metric id -> our MetricReading name + unit
_METRICS = {
    "fees": ("TT_FEES_USD_24H", "USD/day"),
    "user_dau": ("TT_ACTIVE_USERS_24H", "users/day"),
}


class TokenTerminalFeed:
    name = "token_terminal"

    def __init__(self, asset: str = "btc",
                 transport: HttpTransport | None = None,
                 api_key: str | None = None):
        self._asset = asset.lower()
        self._transport = transport  # None => built lazily with auth header
        self._api_key = api_key

    def get_metrics(self) -> list[MetricReading]:
        key = self._api_key or os.environ.get(KEY_ENV)
        if not key:
            # keyless: report unavailable, never block the snapshot
            # (message stays under the 80-char intel-UI cutoff)
            raise NoMarketDataError(
                self._asset, detail="TOKENTERMINAL_API_KEY not set (free key)"
            )
        project = PROJECT_IDS.get(self._asset)
        if project is None:
            raise NoMarketDataError(
                self._asset, detail="no Token Terminal project mapping"
            )
        # the transport carries headers at construction (get_json has no
        # header param); injected transports (tests) bypass auth entirely
        transport = self._transport or RequestsTransport(
            headers={"Authorization": f"Bearer {key}"}
        )
        payload = transport.get_json(
            f"{TOKEN_TERMINAL_BASE}/projects/{project}/metrics",
            {"metric_ids": ",".join(_METRICS)},
        )
        rows = payload.get("data") or []
        if not rows:
            raise NoMarketDataError(
                self._asset, detail="Token Terminal returned no rows"
            )
        # rows: [{"timestamp": iso, "metric_id"/or metric keys: value}, ...]
        # newest first per docs; tolerate either orientation by sorting
        def _ts(row: dict) -> str:
            return str(row.get("timestamp") or "")

        latest = max(rows, key=_ts)
        as_of = _parse_ts(latest.get("timestamp"))
        readings: list[MetricReading] = []
        for metric_id, (name, unit) in _METRICS.items():
            value = latest.get(metric_id)
            if value is None:
                continue
            readings.append(
                MetricReading(name=name, value=float(value), unit=unit,
                              as_of=as_of, source=self.name)
            )
        if not readings:
            raise NoMarketDataError(
                self._asset, detail="Token Terminal rows had no known metrics"
            )
        return readings


def _parse_ts(raw) -> datetime:
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(tz=timezone.utc)
