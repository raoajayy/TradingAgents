"""Binance signed-request helpers + credential hygiene (P3-01).

Scheme (binance-docs): HMAC-SHA256 hex over the exact urlencoded query
string (``totalParams``), appended as a final ``signature`` parameter;
the API key rides in the ``X-MBX-APIKEY`` header. ``recvWindow`` bounds
how stale a signed request may be when the venue processes it.

Credential hygiene (same contract as delta_auth):
- Keys are read from the environment AT CALL TIME (``read_credentials``)
  — never cached on an object that could leak via repr, pickling, or a
  debugger snapshot, and never written to logs or error text. Everything
  that might carry them passes through ``redact``.
- TESTNET keys come from ``BINANCE_TESTNET_API_KEY`` /
  ``BINANCE_TESTNET_API_SECRET``. Mainnet keys are deliberately a
  different pair of names so a testnet drill can never accidentally sign
  against production.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from urllib.parse import urlencode

from tradingagents.pro.execution.adapters.delta_auth import redact  # noqa: F401
from tradingagents.pro.execution.interface import AdapterError

TESTNET_ENV = ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET")
MAINNET_ENV = ("BINANCE_API_KEY", "BINANCE_API_SECRET")


def read_credentials(testnet: bool = True) -> tuple[str, str]:
    """Read (api_key, api_secret) from the environment at call time.

    Raises ``AdapterError`` (with the env var NAMES only — never values)
    when either is missing: a live adapter must refuse to exist half-
    configured rather than send unsigned requests.
    """
    key_var, secret_var = TESTNET_ENV if testnet else MAINNET_ENV
    key = os.environ.get(key_var, "")
    secret = os.environ.get(secret_var, "")
    if not key or not secret:
        raise AdapterError(
            f"{key_var}/{secret_var} not set — refusing to construct a "
            "signed request without credentials"
        )
    return key, secret


def sign_query(secret: str, query: str) -> str:
    """HMAC-SHA256 hex signature over the exact encoded query string."""
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def signed_query(params: dict, secret: str) -> str:
    """Encode ``params`` (insertion order preserved — the signature must
    cover the exact bytes sent) and append the signature parameter."""
    query = urlencode(params)
    return f"{query}&signature={sign_query(secret, query)}"
