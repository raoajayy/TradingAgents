"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os
from unittest.mock import MagicMock, patch

import pytest


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        # `or` not a .get default: an env var present but empty (e.g. a key left
        # blank in a .env copied from .env.example) must still get the placeholder.
        monkeypatch.setenv(env_var, os.environ.get(env_var) or "placeholder")


@pytest.fixture(autouse=True)
def _hermetic_operator_env(monkeypatch, tmp_path_factory):
    """tradingagents/__init__.py auto-loads the repo .env on import, so the
    operator's deployment settings leak into every test process. Neutralize
    the ones that change behavior: the dashboard must not silently gain
    auth (tests opt in via create_app(api_token=...)), and vendor probes
    must never hit the network from unit tests."""
    monkeypatch.delenv("PRO_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
    monkeypatch.setenv("PRO_DISABLE_LIVE_VENDORS", "1")
    # the liquidation stream's daemon threads must not open real sockets
    # from tests (observed: a Binance OI poll traceback bleeding into
    # unrelated suites' output)
    monkeypatch.setenv("PRO_DISABLE_LIQUIDATION_STREAM", "1")
    # default_data_dir()/default_db_path() must never point at the
    # developer's real ~/.tradingagents/pro: DashboardState's default
    # PrefsStore persists there, so cross-run state (e.g. the loop's
    # unchanged-bar skip memory, intel alert crossings) would leak into —
    # and out of — the test suite.
    monkeypatch.setenv("TRADINGAGENTS_PRO_DATA",
                       str(tmp_path_factory.mktemp("pro-data")))
    monkeypatch.delenv("TRADINGAGENTS_PRO_DB", raising=False)


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.
    """
    import copy

    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
