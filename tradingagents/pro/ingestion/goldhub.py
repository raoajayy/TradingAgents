"""World Gold Council Goldhub monthly series (operator CSV drop).

Goldhub publishes ETF flows and central-bank net purchases — the two
structural gold-demand drivers — only as monthly spreadsheet downloads
behind a JS site; there is no stable keyless API (probed 2026-07: the
data pages are an SPA, the download URLs rotate). So the operator drops
a two-line CSV in the data dir once a month:

    month,gold_etf_flows_tonnes,cb_net_purchases_tonnes
    2026-06,-12.4,53.0

A missing or empty file surfaces via the intel ``missing_feeds``
convention with the refresh instruction — disclosed, never faked.

ponytail: manual monthly drop; replace with a scraper/paid feed if the
refresh cadence ever needs to beat once-a-month.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from tradingagents.contracts import MetricReading
from tradingagents.dataflows.errors import NoMarketDataError

GOLDHUB_CSV_NAME = "goldhub_monthly.csv"

_COLUMNS = {
    "gold_etf_flows_tonnes": "GOLD_ETF_FLOWS_TONNES",
    "cb_net_purchases_tonnes": "CB_GOLD_NET_PURCHASES_TONNES",
}


class GoldhubCsvFeed:
    name = "goldhub_csv"

    def __init__(self, path: Path | str):
        self._path = Path(path)

    def get_metrics(self) -> list[MetricReading]:
        if not self._path.exists():
            raise NoMarketDataError(
                "XAUUSD",
                detail="goldhub CSV missing; download monthly from gold.org/goldhub",
            )
        with self._path.open(newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if (r.get("month") or "").strip()]
        if not rows:
            raise NoMarketDataError("XAUUSD", detail="goldhub CSV has no data rows")
        row = max(rows, key=lambda r: r["month"])  # YYYY-MM sorts lexically
        as_of = datetime.strptime(row["month"].strip(), "%Y-%m").replace(
            tzinfo=timezone.utc
        )
        readings = []
        for column, metric in _COLUMNS.items():
            raw = (row.get(column) or "").strip()
            if not raw:
                continue
            readings.append(
                MetricReading(name=metric, value=float(raw), unit="tonnes",
                              as_of=as_of, source=self.name)
            )
        if not readings:
            raise NoMarketDataError(
                "XAUUSD", detail="goldhub CSV latest row has no metric values"
            )
        return readings
