"""P4-02: public live track record — flag-gated ledger + honest aggregates.

The endpoint publishes pre-registered decisions (timestamp + version stamp
existed before the outcome), graded post-hoc from the trade journal only,
with rejections counted and open positions shown. Legal-pending default:
the route 404s unless PRO_PUBLIC_TRACK_RECORD=1.
"""

import json

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from tests.test_pro_pipeline_graph import (  # noqa: E402
    CONFIG,
    FakePipelineLLM,
    pipeline_snapshot,
)
from tradingagents.pro.dashboard.app import DashboardState, create_app  # noqa: E402
from tradingagents.pro.dashboard.prefs import PrefsStore  # noqa: E402
from tradingagents.pro.memory import ProMemory  # noqa: E402
from tradingagents.pro.store import EventStore  # noqa: E402

OPERATOR = {"X-API-Key": "secret-token"}


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _build(tmp_path, monkeypatch, flag: str | None = "1", n_runs: int = 1):
    """Event-store-backed dashboard app with recorded run(s); flag on by
    default so the shape tests exercise the live route."""
    if flag is not None:
        monkeypatch.setenv("PRO_PUBLIC_TRACK_RECORD", flag)
    else:
        monkeypatch.delenv("PRO_PUBLIC_TRACK_RECORD", raising=False)
    store = EventStore(tmp_path / "pro.db")
    state = DashboardState(memory=ProMemory())
    state.prefs = PrefsStore(store=store)
    runs = [state.recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory)
        for _ in range(n_runs)]
    client = TestClient(create_app(state, api_token="secret-token"))
    token = store.create_api_token("tr", ["read:decisions"])["token"]
    return client, state, store, runs, token


class TestFeatureFlag:
    def test_flag_off_is_404_even_with_a_valid_token(self, tmp_path,
                                                     monkeypatch):
        client, state, store, runs, token = _build(
            tmp_path, monkeypatch, flag=None)
        assert client.get("/public/v1/track-record",
                          headers=_bearer(token)).status_code == 404
        # and without any credentials: the route simply does not exist
        assert client.get("/public/v1/track-record").status_code == 404

    def test_flag_zero_is_off(self, tmp_path, monkeypatch):
        client, state, store, runs, token = _build(
            tmp_path, monkeypatch, flag="0")
        assert client.get("/public/v1/track-record",
                          headers=_bearer(token)).status_code == 404

    def test_spa_page_shell_gated_by_the_same_flag(self, tmp_path,
                                                   monkeypatch):
        # the pre-auth SPA page at /public/track-record is the ONE /public
        # path that serves HTML — and only while the flag is on
        client, *_ = _build(tmp_path, monkeypatch, flag="1")
        shell = client.get("/public/track-record")
        assert shell.status_code == 200
        assert "text/html" in shell.headers["content-type"]
        # every other /public path keeps 404ing (no HTML to API probes)
        assert client.get("/public/nope").status_code == 404

        off, *_ = _build(tmp_path / "off", monkeypatch, flag=None)
        assert off.get("/public/track-record").status_code == 404

    def test_flag_on_still_requires_bearer_and_scope(self, tmp_path,
                                                     monkeypatch):
        client, state, store, runs, token = _build(tmp_path, monkeypatch)
        assert client.get("/public/v1/track-record").status_code == 401
        # operator dashboard credential is not a public-API credential
        assert client.get("/public/v1/track-record",
                          headers=OPERATOR).status_code == 401
        calib_only = store.create_api_token("c", ["read:calibration"])["token"]
        denied = client.get("/public/v1/track-record",
                            headers=_bearer(calib_only))
        assert denied.status_code == 403
        assert "read:decisions" in denied.json()["detail"]


class TestLedgerShape:
    def test_shape_ledger_aggregates_and_methodology(self, tmp_path,
                                                     monkeypatch):
        client, state, store, runs, token = _build(tmp_path, monkeypatch)
        run = runs[0]
        # grade the decision post-hoc through the journal
        trade = state.memory.record_trade(run.recommendation)
        state.memory.close_trade(
            trade.id, pnl=250.0, write_lesson=False,
            details={"mode": "paper", "entry_price": 100.0,
                     "fill_price": 104.0})

        body = client.get("/public/v1/track-record",
                          headers=_bearer(token)).json()
        assert set(body) == {"ledger", "aggregates", "methodology"}

        row = body["ledger"][0]
        assert set(row) == {"run_id", "symbol", "started_at", "action",
                            "confidence", "versions", "rejected_at",
                            "open", "outcome"}
        assert row["run_id"] == run.run_id
        assert row["symbol"] == "XAUUSD"
        assert row["action"] == "BUY"
        assert row["confidence"] == 72
        assert row["started_at"] == run.started_at.isoformat()
        # the contamination proof: version stamp recorded at run time
        assert set(row["versions"]) == {"git_sha", "prompt_hash",
                                        "model_ids", "config_hash"}
        assert row["open"] is False
        assert row["outcome"]["pnl"] == 250.0
        assert row["outcome"]["won"] is True
        assert row["outcome"]["closed_at"]

        agg = body["aggregates"]
        assert agg["n_decisions"] == 1
        assert agg["n_graded"] == 1 and agg["win_rate_n"] == 1
        assert agg["win_rate"] == 1.0
        # calibration reused verbatim: brier + fixed buckets with per-n
        assert agg["calibration"]["n"] == 1
        buckets = {(b["confidence_lo"], b["confidence_hi"]): b
                   for b in agg["calibration"]["buckets"]}
        assert buckets[(60, 80)]["n"] == 1
        assert buckets[(0, 20)]["p_win"] is None  # never invented

        # methodology note ships with the data, not just the page
        method = body["methodology"]
        assert set(method) == {"pre_registered", "graded_post_hoc",
                               "rejections_included", "open_positions_shown"}
        # audit surfaces stay operator-only
        text = json.dumps(body)
        for private in ("transcript", "evidence", "debate", "speaker"):
            assert private not in text

    def test_avg_r_from_fills_with_sample_size(self, tmp_path, monkeypatch):
        client, state, store, runs, token = _build(
            tmp_path, monkeypatch, n_runs=2)
        # run 0: full fill data -> R = (104-100)/(100-95) = 0.8 for a BUY
        for run, details in zip(runs, [
            {"mode": "paper", "entry_price": 100.0, "fill_price": 104.0},
            {"mode": "paper"},  # no fills recorded -> excluded from avg R
        ], strict=True):
            trade = state.memory.record_trade(run.recommendation)
            trade.payload["stop_loss"] = 95.0
            trade.payload["entry_price"] = 100.0
            state.memory.close_trade(trade.id, pnl=1.0, write_lesson=False,
                                     details=details)
        agg = client.get("/public/v1/track-record",
                         headers=_bearer(token)).json()["aggregates"]
        assert agg["n_graded"] == 2
        assert agg["avg_r_n"] == 1  # only the row with real fills counts
        assert agg["avg_r"] == pytest.approx(0.8)

    def test_empty_record_reports_null_not_zero(self, tmp_path, monkeypatch):
        client, state, store, runs, token = _build(tmp_path, monkeypatch)
        agg = client.get("/public/v1/track-record",
                         headers=_bearer(token)).json()["aggregates"]
        assert agg["n_graded"] == 0
        assert agg["win_rate"] is None and agg["win_rate_n"] == 0
        assert agg["avg_r"] is None and agg["avg_r_n"] == 0


class TestHonestyRules:
    def test_open_positions_flagged_open_not_trimmed(self, tmp_path,
                                                     monkeypatch):
        client, state, store, runs, token = _build(tmp_path, monkeypatch)
        state.memory.record_trade(runs[0].recommendation)  # opened, not closed

        body = client.get("/public/v1/track-record",
                          headers=_bearer(token)).json()
        row = body["ledger"][0]
        assert row["open"] is True
        assert row["outcome"] is None
        agg = body["aggregates"]
        assert agg["n_open"] == 1
        assert agg["n_graded"] == 0  # an open trade is not a graded one

    def test_rejected_runs_stay_in_the_ledger(self, tmp_path, monkeypatch):
        client, state, store, runs, token = _build(
            tmp_path, monkeypatch, n_runs=2)
        runs[1].state["rejection"] = {"stage": "risk_gate",
                                      "reasons": ["stop too wide"]}
        body = client.get("/public/v1/track-record",
                          headers=_bearer(token)).json()
        assert body["aggregates"]["n_decisions"] == 2
        assert body["aggregates"]["n_rejected"] == 1
        by_id = {r["run_id"]: r for r in body["ledger"]}
        assert by_id[runs[1].run_id]["rejected_at"] == "risk_gate"
        assert by_id[runs[0].run_id]["rejected_at"] is None

    def test_retro_outcomes_never_grade_the_ledger(self, tmp_path,
                                                   monkeypatch):
        client, state, store, runs, token = _build(tmp_path, monkeypatch)
        trade = state.memory.record_trade(runs[0].recommendation)
        # a retro-scored grade (calibration backfill) must not appear as a
        # journal outcome — the row stays open, nothing is graded
        state.memory.close_trade(trade.id, pnl=500.0, write_lesson=False,
                                 details={"mode": "retro"})
        body = client.get("/public/v1/track-record",
                          headers=_bearer(token)).json()
        row = body["ledger"][0]
        assert row["outcome"] is None
        assert row["open"] is True
        assert body["aggregates"]["n_graded"] == 0
        assert body["aggregates"]["win_rate"] is None
