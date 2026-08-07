"""Operator CLI venue selection (P3-01) — transport-stubbed.

The ceremony used to be Delta-only: ``_build_adapter`` hardcoded
``DeltaAdapter`` and the readiness report asked for ``DELTA_*`` secrets,
so a correctly configured Binance dust pilot could not be armed at all.
These tests pin the fix: ``PRO_LIVE_EXCHANGE`` selects the venue for
every ceremony command, the secret-name checks follow the selection, and
the Binance mainnet double opt-in still bites.

Nothing here touches a network — the Binance transport is a fake.
"""

from urllib.parse import parse_qsl, urlsplit

import pytest

from tradingagents.pro.execution.adapters.binance_futures import (
    PROD_BASE,
    TESTNET_BASE,
    BinanceFuturesAdapter,
)
from tradingagents.pro.execution.adapters.delta import DeltaAdapter
from tradingagents.pro.execution.interface import ExecutionNotEnabled

ACCOUNT = {"totalMarginBalance": "1000", "availableBalance": "900"}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.status_code = status
        self.headers = {}
        self._payload = {} if payload is None else payload

    def json(self):
        return self._payload


class FakeHttp:
    """Every venue read answered locally; no socket is ever opened."""

    def __init__(self):
        self.calls = []

    def request(self, method, url, *, headers=None, timeout=30.0):
        parts = urlsplit(url)
        self.calls.append((method, parts.path, dict(parse_qsl(parts.query))))
        if parts.path == "/fapi/v2/account":
            return FakeResponse(ACCOUNT)
        if parts.path == "/fapi/v2/positionRisk":
            return FakeResponse([])
        if parts.path == "/fapi/v1/openOrders":
            return FakeResponse([])
        return FakeResponse({})


@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    """CLI pointed at a scratch /data dir, with stub venue credentials
    for both venues and no exchange selected yet."""
    from tradingagents.pro import cli

    monkeypatch.setattr(cli, "_data_dir", lambda: tmp_path)
    monkeypatch.setenv("DELTA_TESTNET_API_KEY", "delta-key")
    monkeypatch.setenv("DELTA_TESTNET_API_SECRET", "delta-secret")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "bnb-key")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "bnb-secret")
    monkeypatch.delenv("PRO_LIVE_EXCHANGE", raising=False)
    monkeypatch.delenv("PRO_LIVE_VENUE", raising=False)
    monkeypatch.delenv("PRO_LIVE_CONFIG", raising=False)
    monkeypatch.delenv("PRO_BINANCE_MAINNET_ACK", raising=False)
    return cli


class TestAdapterSelection:
    def test_default_is_delta(self, cli_env):
        assert isinstance(cli_env._build_adapter(True), DeltaAdapter)

    def test_explicit_delta_is_delta(self, cli_env, monkeypatch):
        monkeypatch.setenv("PRO_LIVE_EXCHANGE", "delta")
        assert isinstance(cli_env._build_adapter(True), DeltaAdapter)

    def test_binance_selected_by_env(self, cli_env, monkeypatch):
        monkeypatch.setenv("PRO_LIVE_EXCHANGE", "BINANCE")  # case-insensitive
        adapter = cli_env._build_adapter(True)
        assert isinstance(adapter, BinanceFuturesAdapter)
        assert adapter.testnet and adapter._base == TESTNET_BASE

    def test_binance_missing_credentials_refuses(self, cli_env, monkeypatch):
        monkeypatch.setenv("PRO_LIVE_EXCHANGE", "binance")
        monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
        with pytest.raises(Exception, match="BINANCE_TESTNET_API_KEY"):
            cli_env._build_adapter(True)


class TestArmingWiring:
    """Arming gates ORDERS, not reads: the CLI's adapter must consult the
    real ArmingStore for writes, while readiness-style reads work before
    (and after) any arming — otherwise the runbook deadlocks and a TTL
    demotion blinds the operator to open positions."""

    def _adapter(self, cli, monkeypatch):
        monkeypatch.setenv("PRO_LIVE_EXCHANGE", "binance")
        adapter = cli._build_adapter(True)
        adapter._http = FakeHttp()
        return adapter

    def test_unarmed_writes_refuse_honestly(self, cli_env, monkeypatch):
        from tradingagents.pro.execution.interface import OrderSpec

        adapter = self._adapter(cli_env, monkeypatch)
        spec = OrderSpec(client_order_id="ta" + "0" * 24, symbol="BTC-USD",
                         venue_symbol="", side="BUY", quantity=0.001,
                         reference_price=100_000.0)
        with pytest.raises(ExecutionNotEnabled, match="arm-live"):
            adapter.place_order(spec)
        with pytest.raises(ExecutionNotEnabled, match="arm-live"):
            adapter.cancel_order("ta" + "0" * 24)

    def test_unarmed_reads_work(self, cli_env, monkeypatch):
        adapter = self._adapter(cli_env, monkeypatch)
        assert adapter.account().equity == 1000.0
        assert adapter.positions() == []

    def test_armed_pair_makes_reads_work(self, cli_env, monkeypatch):
        adapter = self._adapter(cli_env, monkeypatch)
        cli_env._arming().arm("BTC-USD", "canary", operator="test")
        assert adapter.account().equity == 1000.0

    def test_shadow_tier_is_not_live_arming(self, cli_env, monkeypatch):
        from tradingagents.pro.execution.interface import OrderSpec

        adapter = self._adapter(cli_env, monkeypatch)
        cli_env._arming().arm("BTC-USD", "shadow", operator="test")
        spec = OrderSpec(client_order_id="ta" + "0" * 24, symbol="BTC-USD",
                         venue_symbol="", side="BUY", quantity=0.001,
                         reference_price=100_000.0)
        with pytest.raises(ExecutionNotEnabled):
            adapter.place_order(spec)
        assert adapter.account().equity == 1000.0  # reads unaffected


class TestMainnetDoubleOptIn:
    def test_mainnet_without_ack_refuses(self, cli_env, monkeypatch):
        monkeypatch.setenv("PRO_LIVE_EXCHANGE", "binance")
        monkeypatch.setenv("PRO_LIVE_VENUE", "production")
        monkeypatch.setenv("BINANCE_API_KEY", "main-key")
        monkeypatch.setenv("BINANCE_API_SECRET", "main-secret")
        with pytest.raises(ExecutionNotEnabled,
                           match="PRO_BINANCE_MAINNET_ACK"):
            cli_env._build_adapter(False)

    def test_mainnet_with_ack_builds(self, cli_env, monkeypatch):
        monkeypatch.setenv("PRO_LIVE_EXCHANGE", "binance")
        monkeypatch.setenv("PRO_LIVE_VENUE", "production")
        monkeypatch.setenv("BINANCE_API_KEY", "main-key")
        monkeypatch.setenv("BINANCE_API_SECRET", "main-secret")
        monkeypatch.setenv("PRO_BINANCE_MAINNET_ACK", "dust-pilot-approved")
        adapter = cli_env._build_adapter(False)
        assert adapter._base == PROD_BASE and not adapter.testnet

    def test_testnet_flag_beats_production_env(self, cli_env, monkeypatch):
        """--testnet (the default) can only ever be more cautious."""
        monkeypatch.setenv("PRO_LIVE_EXCHANGE", "binance")
        monkeypatch.setenv("PRO_LIVE_VENUE", "production")
        assert cli_env._build_adapter(True).testnet


class TestFlattenRouterFollowsSelection:
    def test_flatten_router_builds_binance(self, cli_env, monkeypatch):
        from tradingagents.pro.execution.adapters import binance_futures

        monkeypatch.setenv("PRO_LIVE_EXCHANGE", "binance")
        monkeypatch.setattr(binance_futures, "_RequestsClient", FakeHttp)
        cli_env._arming().arm("BTC-USD", "canary", operator="test")
        router = cli_env._build_flatten_router(True)
        assert isinstance(router.adapter, BinanceFuturesAdapter)

    def test_flatten_router_defaults_to_delta(self, cli_env, monkeypatch):
        monkeypatch.setattr(
            DeltaAdapter, "account",
            lambda self: type("A", (), {"equity": 1000.0})())
        router = cli_env._build_flatten_router(True)
        assert isinstance(router.adapter, DeltaAdapter)


class TestReadinessSecretNames:
    def test_names_follow_exchange(self):
        from tradingagents.pro.preflight import venue_secret_names

        # delta testnet reads DELTA_TESTNET_* (adapter from_env prefix) —
        # the old expectation here was the bug found live 2026-08-07
        assert venue_secret_names("delta", True) == (
            "DELTA_TESTNET_API_KEY", "DELTA_TESTNET_API_SECRET")
        assert venue_secret_names("delta", False) == (
            "DELTA_API_KEY", "DELTA_API_SECRET")
        assert venue_secret_names("binance", True) == (
            "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET")
        assert venue_secret_names("binance", False) == (
            "BINANCE_API_KEY", "BINANCE_API_SECRET")

    def test_report_checks_binance_names(self, cli_env, monkeypatch):
        from tradingagents.pro.preflight import go_live_readiness

        monkeypatch.setenv("PRO_DASHBOARD_TOKEN", "x" * 32)
        report = go_live_readiness(adapter=None, exchange="binance",
                                   testnet=True)
        names = {c.name for c in report.checks}
        assert "secret_BINANCE_TESTNET_API_KEY" in names
        assert "secret_DELTA_API_KEY" not in names

    def test_binance_secrets_pass_when_set(self, cli_env, monkeypatch,
                                           tmp_path):
        import os

        from tradingagents.pro.preflight import go_live_readiness

        monkeypatch.setenv("PRO_DASHBOARD_TOKEN", "x" * 32)
        for name in ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"):
            path = tmp_path / name
            path.write_text("stub")
            os.chmod(path, 0o600)
            monkeypatch.setenv(f"{name}_FILE", str(path))
        report = go_live_readiness(adapter=None, exchange="binance",
                                   testnet=True)
        statuses = {c.name: c.status for c in report.checks}
        assert statuses["secret_BINANCE_TESTNET_API_KEY"] == "pass"
        assert statuses["secret_BINANCE_TESTNET_API_SECRET"] == "pass"

    def test_env_credentials_do_not_fail(self, cli_env, monkeypatch):
        """The reported defect: correctly set env creds were FAILing."""
        from tradingagents.pro.preflight import go_live_readiness

        monkeypatch.setenv("PRO_DASHBOARD_TOKEN", "x" * 32)
        report = go_live_readiness(adapter=None, exchange="binance",
                                   testnet=True)
        statuses = {c.name: c.status for c in report.checks}
        assert statuses["secret_BINANCE_TESTNET_API_KEY"] == "warn"
        assert statuses["secret_BINANCE_TESTNET_API_SECRET"] == "warn"

    def test_default_selection_still_delta(self, cli_env, monkeypatch):
        from tradingagents.pro.preflight import go_live_readiness

        monkeypatch.setenv("PRO_DASHBOARD_TOKEN", "x" * 32)
        report = go_live_readiness(adapter=None)
        names = {c.name for c in report.checks}
        assert "secret_DELTA_TESTNET_API_KEY" in names  # default = testnet
