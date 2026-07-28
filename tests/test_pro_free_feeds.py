"""P1-05 free-data adapters: Deribit DVOL + Goldhub monthly CSV."""

from datetime import datetime, timezone

import pytest

from tests.test_pro_agents_base import FakeLLM, make_snapshot
from tradingagents.contracts import MetricReading
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.pro.agents import EvidenceAgent
from tradingagents.pro.agents.roster import MACRO_SPECS
from tradingagents.pro.ingestion.deribit import DeribitVolFeed
from tradingagents.pro.ingestion.goldhub import GoldhubCsvFeed


class StubTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        return self.payload


DVOL_PAYLOAD = {
    "jsonrpc": "2.0",
    "result": {
        "data": [
            [1785200400000, 37.61, 38.62, 37.61, 38.62],
            [1785204000000, 38.62, 38.79, 38.03, 38.75],
            [1785207600000, 38.75, 38.75, 37.95, 37.95],
        ],
        "continuation": None,
    },
}


class TestDeribitVol:
    def test_latest_level_and_1d_change(self):
        transport = StubTransport(DVOL_PAYLOAD)
        readings = DeribitVolFeed(transport=transport, currency="ETH").get_metrics()
        by_name = {r.name: r for r in readings}
        assert by_name["DVOL"].value == pytest.approx(37.95)
        assert by_name["DVOL"].as_of == datetime.fromtimestamp(
            1785207600, tz=timezone.utc
        )
        # change = last close - first close (~24h back)
        assert by_name["DVOL_CHANGE_1D"].value == pytest.approx(37.95 - 38.62)
        url, params = transport.calls[0]
        assert "get_volatility_index_data" in url
        assert params["currency"] == "ETH" and params["resolution"] == 3600

    def test_no_candles_raises(self):
        transport = StubTransport({"result": {"data": []}})
        with pytest.raises(NoMarketDataError):
            DeribitVolFeed(transport=transport).get_metrics()


class TestGoldhubCsv:
    def test_latest_month_wins(self, tmp_path):
        path = tmp_path / "goldhub_monthly.csv"
        path.write_text(
            "month,gold_etf_flows_tonnes,cb_net_purchases_tonnes\n"
            "2026-05,8.1,41.0\n"
            "2026-06,-12.4,53.0\n"
        )
        readings = GoldhubCsvFeed(path).get_metrics()
        by_name = {r.name: r for r in readings}
        assert by_name["GOLD_ETF_FLOWS_TONNES"].value == pytest.approx(-12.4)
        assert by_name["CB_GOLD_NET_PURCHASES_TONNES"].value == pytest.approx(53.0)
        assert all(r.unit == "tonnes" for r in readings)
        assert by_name["GOLD_ETF_FLOWS_TONNES"].as_of == datetime(
            2026, 6, 1, tzinfo=timezone.utc
        )

    def test_partial_columns_served(self, tmp_path):
        path = tmp_path / "goldhub_monthly.csv"
        path.write_text("month,gold_etf_flows_tonnes\n2026-06,-12.4\n")
        readings = GoldhubCsvFeed(path).get_metrics()
        assert [r.name for r in readings] == ["GOLD_ETF_FLOWS_TONNES"]

    def test_missing_file_discloses_refresh_instruction(self, tmp_path):
        with pytest.raises(NoMarketDataError, match="gold.org/goldhub"):
            GoldhubCsvFeed(tmp_path / "nope.csv").get_metrics()


def test_dvol_reaches_implied_vol_agent_prompt():
    # AC: new metrics reach agent prompts through the normal rendering path
    spec = next(s for s in MACRO_SPECS if s.agent_id == "implied_volatility")
    llm = FakeLLM()
    snapshot = make_snapshot(onchain=[
        MetricReading(name="DVOL", value=55.2, unit="% ann. IV",
                      source="deribit_dvol"),
        MetricReading(name="DVOL_CHANGE_1D", value=-1.3, unit="% ann. IV",
                      source="deribit_dvol"),
    ])
    EvidenceAgent(spec, llm).analyze(snapshot)
    prompt = llm.structured.prompts[0]
    assert "DVOL: 55.2 % ann. IV" in prompt
    assert "DVOL_CHANGE_1D: -1.3 % ann. IV" in prompt
