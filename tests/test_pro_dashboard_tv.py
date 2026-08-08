"""/api/tv/history — Pyth history proxy for the TradingView chart."""

import pytest

fastapi = pytest.importorskip("fastapi")

import requests  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tradingagents.pro.dashboard.app import DashboardState, create_app  # noqa: E402
from tradingagents.pro.dashboard.marketdata import PYTH_SYMBOLS  # noqa: E402

UDF_OK = {"s": "ok", "t": [1786100000], "o": [1.0], "h": [2.0],
          "l": [0.5], "c": [1.5], "v": [10.0]}


class FakeResponse:
    def __init__(self, payload=None, text="", status_code=200):
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@pytest.fixture()
def client():
    return TestClient(create_app(DashboardState()))


def test_happy_path_passthrough_and_symbol_mapping(client, monkeypatch):
    seen = {}

    def fake_get(self, url, params=None, timeout=None):
        seen.update(params)
        seen["url"] = url
        return FakeResponse(payload=UDF_OK)

    monkeypatch.setattr(requests.Session, "get", fake_get)
    r = client.get("/api/tv/history", params={
        "symbol": "BTC-USD", "resolution": "60",
        "from": 1786000000, "to": 1786200000})
    assert r.status_code == 200 and r.json() == UDF_OK
    assert seen["symbol"] == "Crypto.BTC/USD"
    assert seen["resolution"] == "60"
    assert seen["url"].endswith("/api/pyth/history")


def test_daily_resolution_mapped_to_upstream_D(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        requests.Session, "get",
        lambda self, url, params=None, timeout=None: (
            seen.update(params), FakeResponse(payload=UDF_OK))[1])
    client.get("/api/tv/history", params={
        "symbol": "XAUUSD", "resolution": "1D",
        "from": 0, "to": 1})
    assert seen["resolution"] == "D"
    assert seen["symbol"] == "Metal.XAU/USD"


def test_unknown_dashboard_symbol_404s_without_upstream_call(client, monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("must not call upstream")

    monkeypatch.setattr(requests.Session, "get", boom)
    r = client.get("/api/tv/history", params={
        "symbol": "DOGE", "resolution": "60", "from": 0, "to": 1})
    assert r.status_code == 404


def test_upstream_plaintext_error_becomes_udf_error(client, monkeypatch):
    monkeypatch.setattr(
        requests.Session, "get",
        lambda self, url, params=None, timeout=None: FakeResponse(
            text="symbol not found.", status_code=404))
    r = client.get("/api/tv/history", params={
        "symbol": "BTC-USD", "resolution": "60", "from": 0, "to": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["s"] == "error" and "symbol not found." in body["errmsg"]


def test_upstream_timeout_maps_to_503(client, monkeypatch):
    def timeout(*a, **kw):
        raise requests.Timeout("upstream slow")

    monkeypatch.setattr(requests.Session, "get", timeout)
    r = client.get("/api/tv/history", params={
        "symbol": "BTC-USD", "resolution": "60", "from": 0, "to": 1})
    assert r.status_code == 503
    assert r.headers.get("retry-after") == "30"


def test_synthetic_mode_serves_bars_without_egress(client, monkeypatch):
    monkeypatch.setenv("PRO_TV_HISTORY_BASE", "synthetic")

    def boom(*a, **kw):
        raise AssertionError("synthetic mode must not call upstream")

    monkeypatch.setattr(requests.Session, "get", boom)
    r = client.get("/api/tv/history", params={
        "symbol": "BTC-USD", "resolution": "60",
        "from": 1786000000, "to": 1786100000})
    body = r.json()
    assert r.status_code == 200 and body["s"] == "ok"
    lengths = {len(body[k]) for k in ("t", "o", "h", "l", "c", "v")}
    assert lengths == {len(body["t"])} and len(body["t"]) > 0
    assert all(lo <= hi for lo, hi in zip(body["l"], body["h"], strict=True))
    # deterministic: same window → same bars
    again = client.get("/api/tv/history", params={
        "symbol": "BTC-USD", "resolution": "60",
        "from": 1786000000, "to": 1786100000}).json()
    assert again == body


def test_identical_window_served_from_ttl_cache(client, monkeypatch):
    calls = {"n": 0}

    def fake_get(self, url, params=None, timeout=None):
        calls["n"] += 1
        return FakeResponse(payload=UDF_OK)

    monkeypatch.setattr(requests.Session, "get", fake_get)
    window = {"symbol": "BTC-USD", "resolution": "60",
              "from": 1786000000, "to": 1786200000}
    first = client.get("/api/tv/history", params=window).json()
    second = client.get("/api/tv/history", params=window).json()
    assert first == second == UDF_OK
    assert calls["n"] == 1  # second hit served from the 15s TTL cache
    # a different window is a different cache key
    client.get("/api/tv/history", params={**window, "to": 1786200060})
    assert calls["n"] == 2


def test_upstream_errors_are_never_cached(client, monkeypatch):
    responses = [FakeResponse(text="symbol not found.", status_code=404),
                 FakeResponse(payload=UDF_OK)]
    monkeypatch.setattr(
        requests.Session, "get",
        lambda self, url, params=None, timeout=None: responses.pop(0))
    window = {"symbol": "BTC-USD", "resolution": "60", "from": 0, "to": 1}
    assert client.get("/api/tv/history", params=window).json()["s"] == "error"
    # the transient error must not stick: the retry reaches upstream
    assert client.get("/api/tv/history", params=window).json() == UDF_OK


def test_missing_asset_paths_404_instead_of_spa_shell(client):
    # HTML-served-as-JS poisoned missing chart chunks twice during the
    # TradingView rollout; extension-bearing misses must be hard 404s
    r = client.get("/charting_library/bundles/nope.abc123.js")
    assert r.status_code == 404
    assert "<html" not in r.text.lower()


def test_symbols_payload_discloses_pyth_symbol(client):
    rows = client.get("/api/symbols").json()
    by_name = {row["symbol"]: row for row in rows}
    for symbol, pyth in PYTH_SYMBOLS.items():
        if symbol in by_name:
            assert by_name[symbol]["pyth_symbol"] == pyth
    # non-mapped symbols disclose the absence honestly
    assert all("pyth_symbol" in row for row in rows)
