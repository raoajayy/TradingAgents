"""OANDA intraday feed (spot FX + metals, practice API).

Free practice-tier REST API: https://developer.oanda.com/rest-live-v20/.
Env: ``OANDA_API_TOKEN`` (bearer token from a practice account) and
optional ``OANDA_ENV`` (``practice`` default | ``live``). Missing token
raises VendorNotConfiguredError per the taxonomy — callers register the
gap (yfinance daily fallback in the dashboard registry) instead of
crashing.

``OandaFeed`` is instrument-parameterized (P2-10): ``XAU_USD`` for gold,
``EUR_USD`` / ``USD_JPY`` for the FX majors. Pass ``instrument`` to bind
a feed to one pair (per-call symbols are then ignored — the feed IS that
instrument), or leave it None and pass OANDA-format symbols per call.
``OandaGoldFeed`` is the pre-P2-10 name, kept as a thin alias so existing
imports, tests, and the ``oanda_gold`` registry source keep working.

Candles are requested as mid prices (deterministic: no bid/ask ambiguity)
and incomplete candles are dropped — the whole system assumes bar-close
semantics. Quotes derive bid/ask from the latest 5-second bid/ask candle,
which avoids needing an OANDA_ACCOUNT_ID for the pricing endpoint.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from tradingagents.contracts import OHLCVBar, SpotQuote, Timeframe
from tradingagents.dataflows.errors import NoMarketDataError, VendorNotConfiguredError
from tradingagents.pro.ingestion.base import HttpTransport, RequestsTransport

_HOSTS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}


class OandaNotConfiguredError(VendorNotConfiguredError):
    pass


class OandaFeed:
    """Bars + quotes for one OANDA v20 instrument (XAU_USD, EUR_USD, ...)."""

    name = "oanda"

    GRANULARITY: dict[Timeframe, str] = {
        Timeframe.M1: "M1",
        Timeframe.M5: "M5",
        Timeframe.M15: "M15",
        Timeframe.M30: "M30",
        Timeframe.H1: "H1",
        Timeframe.H4: "H4",
        Timeframe.D1: "D",
        Timeframe.W1: "W",
    }

    def __init__(self, transport: HttpTransport | None = None,
                 token: str | None = None, env: str | None = None,
                 instrument: str | None = None):
        token = token or os.environ.get("OANDA_API_TOKEN", "")
        if not token:
            raise OandaNotConfiguredError(
                "OANDA_API_TOKEN not set; OANDA intraday unavailable "
                "(daily bars fall back to yfinance)"
            )
        env = (env or os.environ.get("OANDA_ENV", "practice")).lower()
        if env not in _HOSTS:
            raise ValueError(f"OANDA_ENV must be one of {sorted(_HOSTS)}, got {env!r}")
        self._base = _HOSTS[env]
        # binds the feed to one instrument; per-call symbols are ignored so
        # dashboard names (EURUSD) never leak into the vendor URL
        self.instrument = instrument
        self._transport = transport or RequestsTransport(
            headers={"Authorization": f"Bearer {token}"}
        )

    @staticmethod
    def configured() -> bool:
        return bool(os.environ.get("OANDA_API_TOKEN"))

    @classmethod
    def probe(cls, timeout: float = 8.0, instrument: str = "XAU_USD") -> bool:
        """One cheap candles request proves the token actually works.
        A configured-but-invalid token (wrong product, expired) must
        degrade to the yfinance fallback, not brick charts. Token validity
        is instrument-independent — one probe covers gold and FX alike."""
        if not cls.configured():
            return False
        if os.environ.get("PRO_DISABLE_LIVE_VENDORS") == "1":
            return False
        try:
            feed = cls(transport=RequestsTransport(
                timeout=timeout,
                headers={"Authorization":
                         f"Bearer {os.environ.get('OANDA_API_TOKEN', '')}"},
            ))
            feed.get_bars(instrument, Timeframe.D1, limit=2)
            return True
        except Exception:
            return False

    def _candles(self, instrument: str, params: dict) -> list[dict]:
        payload = self._transport.get_json(
            f"{self._base}/v3/instruments/{instrument}/candles", params
        )
        return payload.get("candles", [])

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        *,
        limit: int = 250,
        end: datetime | None = None,
    ) -> list[OHLCVBar]:
        granularity = self.GRANULARITY.get(timeframe)
        if granularity is None:
            raise ValueError(f"{self.name} does not support {timeframe.value}")
        params: dict = {
            "granularity": granularity,
            "count": min(max(limit + 1, 2), 5000),  # +1: last may be incomplete
            "price": "M",
        }
        if end is not None:
            params["to"] = end.astimezone(timezone.utc).isoformat()
        candles = self._candles(self.instrument or symbol, params)
        bars = [
            OHLCVBar(
                timeframe=timeframe,
                start=datetime.fromisoformat(row["time"].replace("Z", "+00:00")),
                open=float(row["mid"]["o"]),
                high=float(row["mid"]["h"]),
                low=float(row["mid"]["l"]),
                close=float(row["mid"]["c"]),
                volume=float(row.get("volume", 0)),
            )
            for row in candles
            if row.get("complete")  # bar-close semantics only
        ]
        if not bars:
            raise NoMarketDataError(symbol, detail="OANDA returned no complete candles")
        return bars[-limit:]

    def get_quote(self, symbol: str) -> SpotQuote:
        candles = self._candles(
            self.instrument or symbol,
            {"granularity": "S5", "count": 1, "price": "BAM"},
        )
        if not candles:
            raise NoMarketDataError(symbol, detail="OANDA returned no quote candle")
        row = candles[-1]  # freshest 5s candle; may be incomplete (that's fine
        # for a quote — it is the most recent traded picture)
        return SpotQuote(
            bid=float(row["bid"]["c"]),
            ask=float(row["ask"]["c"]),
            last=float(row["mid"]["c"]),
            ts=datetime.fromisoformat(row["time"].replace("Z", "+00:00")),
        )


class OandaGoldFeed(OandaFeed):
    """Backward-compat alias: the pre-P2-10 gold-only name. Keeps the
    ``oanda_gold`` provenance string the dashboard registry and tick-poller
    wiring already know; behavior is OandaFeed's."""

    name = "oanda_gold"
