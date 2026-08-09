"""Typed FRED adapter for the macro series gold cares about.

The base framework's dataflows.fred returns markdown reports for agent
prompts; this adapter returns numeric MetricReading contracts for the
deterministic snapshot layer instead. Same free API, same FRED_API_KEY,
same not-configured error type — a missing key routes through the standard
taxonomy so the snapshot builder records the feed as missing.

FRED rate limit: 120 requests/minute (free key). One snapshot build issues
one request per series (~6), far below the limit. The ``units`` transform
is applied server-side by FRED (pc1 = percent change vs year ago, chg =
change from previous value), keeping all math out of this process.

P3-02 vintages: FRED is really ALFRED underneath — every observation
carries ``realtime_start``, the date this exact value became public.
When a ``vintage_sink`` is injected, each fetch also appends
(name, value, observed_at=realtime_start, as_of=observation date) so
revised macro data never rewrites what a past decision saw. Without a
sink the feed behaves exactly as before.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone

from tradingagents.contracts import MetricReading
from tradingagents.dataflows.fred import FredNotConfiguredError
from tradingagents.pro.ingestion.base import HttpTransport, RequestsTransport

logger = logging.getLogger(__name__)

API_BASE = "https://api.stlouisfed.org/fred"

# Releases that actually move price on publication. This drives the
# pipeline's 4h event gate — a trading kill-switch — so it is an ALLOWLIST
# of anchored names, not a substring search.
#
# It used to be an unanchored `re.search` written for ordering a briefing
# widget. Reused as a gate, it blocked 26% of production runs: "Debt to
# Gross Domestic Product Ratios" matched "gross domestic product", and the
# STATE-level "State Unemployment Insurance Weekly Claims Report" matched
# the national weekly-claims pattern and blocked 4h every single week.
_MAJOR_RELEASES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in (
        r"^FOMC\b",
        r"^Consumer Price Index$",
        r"^Employment Situation$",
        r"^Producer Price Index",
        r"^Advance Monthly Sales for Retail(?: and Food Services)?$",
        r"^Retail Sales$",
        r"^Personal Income and Outlays$",
        r"^Gross Domestic Product$",
        # national weekly claims only — the "State ..." variant is a
        # regional breakdown that does not move the tape
        r"^Unemployment Insurance Weekly Claims Report$",
        r"^University of Michigan Consumer (?:Survey|Sentiment)",
        r"^ISM (?:Manufacturing|Services|Non-Manufacturing)",
        r"^Job Openings and Labor Turnover Survey$",
        r"^JOLTS$",
    )
)

#: names containing a major phrase that are derived/regional, never the
#: market-moving print. Belt-and-braces against allowlist drift.
_NEVER_MAJOR = re.compile(
    r"\b(?:state|regional|by (?:state|industry|county|metro)|ratio|"
    r"revision|annual revision|nowcast)\b",
    re.IGNORECASE,
)


def is_major_release(release_name: str) -> bool:
    """True only for releases whose publication reliably moves price.

    Anchored allowlist: this gates trading, so a false positive costs real
    signal (4h of blocked entries) while a false negative only forfeits
    caution around a second-tier print.
    """
    name = (release_name or "").strip()
    if not name or _NEVER_MAJOR.search(name):
        return False
    return any(pattern.search(name) for pattern in _MAJOR_RELEASES)

# P3-02 vintage sink: called once per usable observation with keyword args
# (name, value, observed_at, as_of, source) — the exact signature of
# EventStore.record_vintage, so the store method IS a valid sink.
VintageSink = Callable[..., None]

# metric name -> (FRED series id, units transform, unit label)
DEFAULT_SERIES: dict[str, tuple[str, str, str]] = {
    "FED_FUNDS_RATE": ("DFF", "lin", "percent"),
    "US10Y": ("DGS10", "lin", "percent"),
    "US10Y_REAL": ("DFII10", "lin", "percent"),
    "DXY_BROAD": ("DTWEXBGS", "lin", "index"),
    "CPI_YOY": ("CPIAUCSL", "pc1", "percent"),
    # PPIFIS = headline PPI (final demand) — what traders mean by "PPI YoY".
    # The previous PPIACO (all-commodities) runs ~3x hotter and repeatedly
    # steered macro debates toward "staggering 10% PPI" (trader review).
    "PPI_YOY": ("PPIFIS", "pc1", "percent"),
    "NFP_CHANGE": ("PAYEMS", "chg", "thousands"),
    "GDP_YOY": ("GDP", "pc1", "percent"),
}


class FredMacroFeed:
    name = "fred_macro"

    def __init__(
        self,
        transport: HttpTransport | None = None,
        series: dict[str, tuple[str, str, str]] | None = None,
        api_key: str | None = None,
        vintage_sink: VintageSink | None = None,
    ):
        self._transport = transport or RequestsTransport()
        self._series = series or DEFAULT_SERIES
        self._api_key = api_key
        self._vintage_sink = vintage_sink

    def _key(self) -> str:
        key = self._api_key or os.environ.get("FRED_API_KEY", "")
        if not key:
            raise FredNotConfiguredError(
                "FRED_API_KEY not set; get a free key at https://fred.stlouisfed.org/docs/api/api_key.html"
            )
        return key

    def get_release_dates(self, days_ahead: int = 30) -> list[dict]:
        """Upcoming release dates (economic calendar). Returns
        [{"date": "YYYY-MM-DD", "release": name, "release_id": id}, ...]."""
        from datetime import date, timedelta

        key = self._key()
        today = date.today()
        payload = self._transport.get_json(
            f"{API_BASE}/releases/dates",
            {
                "api_key": key,
                "file_type": "json",
                "include_release_dates_with_no_data": "true",
                "realtime_start": today.isoformat(),
                "realtime_end": (today + timedelta(days=days_ahead)).isoformat(),
                "sort_order": "asc",
                "limit": 200,
            },
        )
        return [
            {
                "date": row["date"],
                "release": row.get("release_name", ""),
                "release_id": row.get("release_id"),
                "major": is_major_release(row.get("release_name", "")),
            }
            for row in payload.get("release_dates", [])
            if today.isoformat() <= row.get("date", "")
        ]

    def get_metrics(self) -> list[MetricReading]:
        key = self._key()
        readings: list[MetricReading] = []
        for name, (series_id, units, unit_label) in self._series.items():
            payload = self._transport.get_json(
                f"{API_BASE}/series/observations",
                {
                    "series_id": series_id,
                    "api_key": key,
                    "file_type": "json",
                    "units": units,
                    "sort_order": "desc",
                    "limit": 5,  # tolerate a few leading "." placeholders
                },
            )
            reading_emitted = False
            for obs in payload.get("observations", []):
                if obs.get("value") in (".", "", None):
                    continue
                value = float(obs["value"])
                as_of = datetime.fromisoformat(obs["date"]).replace(
                    tzinfo=timezone.utc
                )
                self._record_vintage(name, value, obs, as_of,
                                     f"fred:{series_id}")
                if not reading_emitted:
                    readings.append(
                        MetricReading(
                            name=name,
                            value=value,
                            unit=unit_label,
                            as_of=as_of,
                            source=f"fred:{series_id}",
                        )
                    )
                    reading_emitted = True
                if self._vintage_sink is None:
                    break  # no sink: stop at the first usable value, as before
        return readings

    def _record_vintage(self, name: str, value: float, obs: dict,
                        as_of: datetime, source: str) -> None:
        """P3-02: append (value, observed_at, as_of) through the sink.

        ``observed_at`` is ALFRED's ``realtime_start`` — the date the world
        first knew this exact value (FRED echoes it on every observation).
        Sink failures only log: vintage capture must never degrade the feed.
        """
        if self._vintage_sink is None:
            return
        try:
            raw = obs.get("realtime_start") or ""
            observed_at = (
                datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
                if raw else datetime.now(timezone.utc)
            )
            self._vintage_sink(name=name, value=value,
                               observed_at=observed_at, as_of=as_of,
                               source=source)
        except Exception:  # noqa: BLE001 — sink is best-effort by contract
            logger.warning("vintage sink failed for %s", name, exc_info=True)
