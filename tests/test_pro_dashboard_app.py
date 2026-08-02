"""Dashboard FastAPI endpoints (skipped without the dashboard extra)."""

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from tests.test_pro_pipeline_graph import CONFIG, FakePipelineLLM, pipeline_snapshot  # noqa: E402
from tradingagents.pro.dashboard.app import DashboardState, create_app  # noqa: E402
from tradingagents.pro.memory import ProMemory  # noqa: E402


@pytest.fixture()
def client():
    state = DashboardState(memory=ProMemory())
    state.recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory
    )
    return TestClient(create_app(state))


class _StubStructured:
    def __init__(self, answer):
        self._answer = answer

    def invoke(self, prompt):
        self._captured = prompt
        return self._answer


class _Chunk:
    def __init__(self, content):
        self.content = content


class _StubLLM:
    """Minimal ModelBundle-coercible llm: canned EvidenceAnswer for the
    structured endpoint + a token stream for the streaming endpoint."""

    def __init__(self, answer, stream_chunks=None):
        self._answer = answer
        self._stream = stream_chunks or []

    def with_structured_output(self, schema):
        return _StubStructured(self._answer)

    def stream(self, prompt):
        for text in self._stream:
            yield _Chunk(text)


def test_ask_run_answers_from_the_record():
    from tradingagents.pro.pipeline.qa import EvidenceAnswer

    state = DashboardState(memory=ProMemory())
    run = state.recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory
    )
    answer = EvidenceAnswer(
        answerable=True, answer="The bull side led per rsi.",
        cited_agent_ids=["rsi"],
    )
    state.trigger = type("T", (), {"service": type("S", (), {"llm": _StubLLM(answer)})()})()
    client = TestClient(create_app(state))

    resp = client.post(f"/api/runs/{run.run_id}/ask",
                       json={"question": "What decided it?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["answerable"] is True
    assert body["cited_agent_ids"] == ["rsi"]

    # guards
    assert client.post(f"/api/runs/{run.run_id}/ask", json={}).status_code == 422
    assert client.post("/api/runs/nope/ask",
                       json={"question": "x"}).status_code == 404


def test_ask_run_stream_yields_prose_and_sources(tmp_path):
    from tradingagents.pro.pipeline.qa import EvidenceAnswer

    state = DashboardState(memory=ProMemory())
    run = state.recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory
    )
    chunks = ["The bear side ", "carried it per rsi.", "\nSOURCES: rsi, macd"]
    llm = _StubLLM(
        EvidenceAnswer(answerable=True, answer="x", cited_agent_ids=["rsi"]),
        stream_chunks=chunks,
    )
    state.trigger = type("T", (), {"service": type("S", (), {"llm": llm})()})()
    client = TestClient(create_app(state))

    with client.stream("POST", f"/api/runs/{run.run_id}/ask/stream",
                       json={"question": "why?"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "carried it per rsi." in body
    assert body.endswith("SOURCES: rsi, macd")

    # guards shared with /ask
    assert client.post(f"/api/runs/{run.run_id}/ask/stream",
                       json={}).status_code == 422


def _run_with_llm(llm):
    state = DashboardState(memory=ProMemory())
    run = state.recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory
    )
    state.trigger = type("T", (), {"service": type("S", (), {"llm": llm})()})()
    return run, TestClient(create_app(state))


def test_ask_run_stream_through_production_wrapper_shape():
    """Regression: the wrapped-bundle shape main.py actually builds.

    CostTrackingLLM implements only with_structured_output, so
    bundle.deep.stream(...) raised AttributeError and the endpoint yielded
    it as the answer body — every question returned
    '[stream interrupted: AttributeError]'. The old _StubLLM was passed
    bare, so no test ever exercised a wrapper.
    """
    from tradingagents.pro.models import ModelBundle
    from tradingagents.pro.observability import (
        CostTrackingLLM,
        MetricsRegistry,
        supports_streaming,
    )
    from tradingagents.pro.pipeline.qa import EvidenceAnswer

    metrics = MetricsRegistry()
    inner = _StubLLM(
        EvidenceAnswer(answerable=True, answer="x", cited_agent_ids=["rsi"]),
        stream_chunks=["macd led ", "the call.", "\nSOURCES: macd"],
    )
    tracked = CostTrackingLLM(inner, metrics=metrics)
    assert supports_streaming(tracked)
    run, client = _run_with_llm(ModelBundle.single(tracked))

    with client.stream("POST", f"/api/runs/{run.run_id}/ask/stream",
                       json={"question": "why?"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "[stream interrupted" not in body
    assert "macd led the call." in body
    assert body.endswith("SOURCES: macd")
    # streamed tokens still land in the cost report
    assert tracked.report.by_schema["stream"] == 1
    assert tracked.report.est_output_tokens > 0
    assert metrics.counter("llm_calls_total", schema="stream") == 1


def test_ask_run_stream_501_when_model_cannot_stream():
    """A non-streaming model must produce a status the client can branch
    on — not a 200 whose body is an error string (which made the
    documented /ask fallback unreachable)."""
    from tradingagents.pro.observability import supports_streaming
    from tradingagents.pro.pipeline.qa import EvidenceAnswer

    class _NoStreamLLM:
        def with_structured_output(self, schema):
            return _StubStructured(
                EvidenceAnswer(answerable=True, answer="from /ask",
                               cited_agent_ids=["rsi"]))

    llm = _NoStreamLLM()
    assert not supports_streaming(llm)
    run, client = _run_with_llm(llm)

    assert client.post(f"/api/runs/{run.run_id}/ask/stream",
                       json={"question": "why?"}).status_code == 501
    # ...and the fallback the client takes still works
    resp = client.post(f"/api/runs/{run.run_id}/ask", json={"question": "why?"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "from /ask"


def test_ask_run_stream_mid_stream_failure_emits_sentinel():
    """Once bytes are on the wire there is no status left — the in-band
    sentinel is what the client watches for to retry against /ask."""
    from tradingagents.pro.dashboard.app import STREAM_INTERRUPTED
    from tradingagents.pro.pipeline.qa import EvidenceAnswer

    class _FailsMidStream(_StubLLM):
        def stream(self, prompt):
            yield _Chunk("partial ")
            raise RuntimeError("connection reset")

    run, client = _run_with_llm(_FailsMidStream(
        EvidenceAnswer(answerable=True, answer="x", cited_agent_ids=["rsi"])))
    with client.stream("POST", f"/api/runs/{run.run_id}/ask/stream",
                       json={"question": "why?"}) as resp:
        assert resp.status_code == 200  # headers already sent
        body = "".join(resp.iter_text())
    assert body.startswith("partial ")
    assert STREAM_INTERRUPTED in body


def test_mid_stream_failure_charges_cost_but_not_the_call_counter():
    """llm_calls_total means 'successful calls' on the structured path;
    the stream path must not quietly redefine it. Partial tokens were
    still billed, so cost is charged either way."""
    from tradingagents.pro.observability import CostTrackingLLM, MetricsRegistry
    from tradingagents.pro.pipeline.qa import EvidenceAnswer

    class _FailsMidStream(_StubLLM):
        def stream(self, prompt):
            yield _Chunk("partial ")
            raise RuntimeError("connection reset")

    metrics = MetricsRegistry()
    tracked = CostTrackingLLM(
        _FailsMidStream(EvidenceAnswer(answerable=True, answer="x",
                                       cited_agent_ids=["rsi"])),
        metrics=metrics)
    with pytest.raises(RuntimeError):
        list(tracked.stream("why?"))
    assert metrics.counter("llm_failures_total", schema="stream") == 1
    assert metrics.counter("llm_calls_total", schema="stream") == 0
    assert tracked.report.calls == 0
    assert tracked.report.est_output_tokens >= 0
    assert tracked.report.est_input_tokens > 0


def test_ask_run_503_without_model():
    state = DashboardState(memory=ProMemory())
    run = state.recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory
    )
    client = TestClient(create_app(state))  # no trigger/service attached
    resp = client.post(f"/api/runs/{run.run_id}/ask",
                       json={"question": "why?"})
    assert resp.status_code == 503


def test_chart_annotations_endpoint():
    from tradingagents.contracts import Timeframe
    from tradingagents.pro.dashboard import marketdata as md

    # stub registry: the lazy default probes vendors over the network
    spec = md.SymbolSpec(
        symbol="XAUUSD", vendor_symbol="XAUUSD", source="test",
        timeframes=(Timeframe.D1,), live=False, feed_factory=lambda: None,
    )
    state = DashboardState(
        memory=ProMemory(),
        marketdata=md.MarketDataService(registry={"XAUUSD": spec}),
    )
    run = state.recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory
    )
    client = TestClient(create_app(state))

    body = client.get("/api/chart/annotations",
                      params={"symbol": "XAUUSD"}).json()
    assert body["symbol"] == "XAUUSD"
    assert body["cadence_seconds"] > 0
    ids = [v["run_id"] for v in body["runs"]]
    assert run.run_id in ids
    view = body["runs"][ids.index(run.run_id)]
    if view["action"] in ("BUY", "SELL"):  # scripted judge decides
        assert view["geometry"]["entry"] > 0
        assert view["span"]["reason"] in ("open", "closed", "superseded")

    resp = client.get("/api/chart/annotations", params={"symbol": "NOPE"})
    assert resp.status_code == 404


def test_index_serves_dashboard_html(client):
    # "/" serves the SPA when a frontend build exists, else the legacy page;
    # both carry the product name. The legacy page stays at /legacy.
    response = client.get("/")
    assert response.status_code == 200
    assert "TradingAgents Pro" in response.text
    legacy = client.get("/legacy")
    assert legacy.status_code == 200
    for section in ("overview", "recommendation", "timeline", "agents", "journal"):
        assert f'id="{section}"' in legacy.text


def test_index_quick_win_markers(client):
    """UX quick wins are wired into the legacy page (structural check)."""
    html = client.get("/legacy").text
    assert 'name="viewport"' in html                 # A11Y-02
    assert "@media (max-width: 900px)" in html       # A11Y-02
    assert "X-API-Key" in html                       # SEC-UI-01
    assert "STALE" in html and 'id="conn"' in html   # ALERT-01
    assert "dirGlyph" in html and "▲" in html        # A11Y-01
    assert "invalidation" in html                    # EXPL-02
    assert "document.hidden" in html                 # PERF-01
    assert 'id="haltBanner"' in html                 # RISK-01
    assert 'id="alerts"' in html                     # ALERT-02
    assert 'id="runSelector"' in html                # NAV-01
    assert "equityChart" in html                     # VIZ-01
    assert "counterarguments" in html                # EXPL-01


def test_api_surface(client):
    overview = client.get("/api/overview").json()
    assert overview["symbol"] == "XAUUSD"

    runs = client.get("/api/runs").json()
    assert len(runs) == 1 and runs[0]["action"] == "BUY"
    run_id = runs[0]["run_id"]

    timeline = client.get(f"/api/runs/{run_id}/timeline").json()
    assert any(e["speaker"] == "judge" for e in timeline["entries"])

    evidence = client.get(f"/api/runs/{run_id}/evidence").json()
    assert "technical" in evidence

    recommendation = client.get("/api/recommendation/latest").json()
    assert recommendation["action"] == "BUY" and "vote_breakdown" in recommendation
    assert "invalidation" in recommendation  # EXPL-02: reflection surfaced

    status = client.get("/api/status").json()
    # no router wired; arming absent = every pair paper (go-live Phase 4)
    assert status["attached"] is False
    assert status["trading_halted"] is None
    assert status["live_armed"] is False

    alerts = client.get("/api/alerts").json()
    assert alerts == {"alerts": []}  # clean accepted run

    for path in ("/api/journal", "/api/backtest", "/api/memory", "/api/agents"):
        assert client.get(path).status_code == 200


def test_unknown_run_is_404(client):
    assert client.get("/api/runs/nope/timeline").status_code == 404


class TestRunPersistence:
    def test_round_trip_preserves_views(self, tmp_path):
        from tradingagents.pro.dashboard import service as views
        from tradingagents.pro.dashboard.recorder import PipelineRecorder

        memory = ProMemory()
        recorder = PipelineRecorder(store_dir=tmp_path)
        run = recorder.record_run(
            FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=memory
        )
        reloaded = PipelineRecorder(store_dir=tmp_path)
        assert [r.run_id for r in reloaded.runs] == [run.run_id]
        loaded = reloaded.runs[0]
        assert views.debate_timeline(loaded) == views.debate_timeline(run)
        assert views.evidence_panels(loaded) == views.evidence_panels(run)
        assert views.market_overview(loaded) == views.market_overview(run)
        assert loaded.recommendation.action == run.recommendation.action
        assert loaded.timeframe == "1d"

    def test_trigger_provenance_persists_and_defaults(self, tmp_path):
        """R3.2: runs carry who asked for them; pre-field files load 'loop'."""
        import json

        from tradingagents.pro.dashboard.recorder import PipelineRecorder

        memory = ProMemory()
        recorder = PipelineRecorder(store_dir=tmp_path)
        run = recorder.record_run(
            FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=memory,
            trigger="operator",
        )
        assert run.trigger == "operator"
        reloaded = PipelineRecorder(store_dir=tmp_path)
        assert reloaded.runs[0].trigger == "operator"
        # a pre-provenance file (no trigger key) loads as the schedule
        raw = json.loads((tmp_path / f"{run.run_id}.json").read_text())
        del raw["trigger"]
        (tmp_path / f"{run.run_id}.json").write_text(json.dumps(raw))
        legacy = PipelineRecorder(store_dir=tmp_path)
        assert legacy.runs[0].trigger == "loop"

    def test_pre_timing_file_loads_with_empty_node_times(self, tmp_path):
        """Runs persisted before node_times existed load fine (UI omits latency)."""
        import json

        from tradingagents.pro.dashboard.recorder import PipelineRecorder

        memory = ProMemory()
        recorder = PipelineRecorder(store_dir=tmp_path)
        run = recorder.record_run(
            FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=memory
        )
        assert run.node_times  # new runs record timings
        path = tmp_path / f"{run.run_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        del raw["node_times"]  # simulate a pre-R9 file
        path.write_text(json.dumps(raw), encoding="utf-8")
        loaded = PipelineRecorder(store_dir=tmp_path).runs[0]
        assert loaded.node_times == []
        assert loaded.node_sequence == run.node_sequence

    def test_corrupt_file_skipped(self, tmp_path):
        from tradingagents.pro.dashboard.recorder import PipelineRecorder

        (tmp_path / "bad.json").write_text("{nope", encoding="utf-8")
        recorder = PipelineRecorder(store_dir=tmp_path)
        assert recorder.runs == []

    def test_prune_beyond_cap(self, tmp_path):
        from tradingagents.pro.dashboard.recorder import PipelineRecorder

        memory = ProMemory()
        recorder = PipelineRecorder(max_runs=2, store_dir=tmp_path)
        for _ in range(3):
            recorder.record_run(FakePipelineLLM(), CONFIG, pipeline_snapshot(),
                                memory=memory)
        assert len(list(tmp_path.glob("*.json"))) == 2
        assert len(PipelineRecorder(max_runs=2, store_dir=tmp_path).runs) == 2

    def test_runs_rows_carry_timeframe(self, client):
        rows = client.get("/api/runs").json()
        assert rows[0]["timeframe"] == "1d"


# P3-06 golden pack: every deterministic field of the export, verbatim.
# Volatile fields (ids, timestamps, hashes) are normalized to "<volatile>"
# before comparison; transcript/evidence/recommendation are asserted
# separately against the view helpers they must reuse.
_EXPORT_GOLDEN = {
    "pack_format": 1,
    "generated_at": "<volatile>",
    "run_id": "<volatile>",
    "symbol": "XAUUSD",
    "asset": "XAU",
    "started_at": "<volatile>",
    "trigger": "loop",
    "timeframe": "1d",
    "versions": {
        "git_sha": "<volatile>",
        "prompt_hash": "<volatile>",
        "model_ids": ["gpt-5.4-mini", "gpt-5.5"],
        "config_hash": "<volatile>",
    },
    "snapshot": {
        "symbol": "XAUUSD",
        "as_of": "2026-07-06T14:30:00+00:00",
        "last_close": 130.0,
        "n_bars": 60,
        "session": None,
        "missing_feeds": [],
        "regime": "trending_up",
        "bar_range": {
            "first": "2026-06-01T00:00:00+00:00",
            "last": "2026-07-30T00:00:00+00:00",
        },
    },
    "gates": {
        "risk": {
            "passed": True,
            "checks": {
                "var_available": True,
                "var_within_limit": True,
                "cvar_within_limit": True,
            },
            "reasons": [],
        },
        "critic": {
            "passed": True,
            "issues": [],
            "samples": 3,
            "votes_pass": 3,
        },
    },
    "execution": {
        "execution_status": "accepted:paper",
        "order": {
            "trade_record_id": "<volatile>",
            "recommendation_id": "<volatile>",
            "action": "BUY",
            "confidence": 72,
            "regime": "trending_up",
            "entry_price": 130.0,
            "stop_loss": 125.0,
            "take_profits": [132.5, 147.5],
            "risk_reward": 2.0,
        },
    },
    "outcome": None,
    "calibration": None,
}


def _normalize_pack(pack: dict) -> dict:
    """Blank the volatile fields (ids/timestamps/hashes); drop the three
    sections asserted separately against their view helpers."""
    import copy

    norm = copy.deepcopy(pack)
    for key in ("generated_at", "run_id", "started_at"):
        norm[key] = "<volatile>"
    for key in ("git_sha", "prompt_hash", "config_hash"):
        norm["versions"][key] = "<volatile>"
    order = norm["execution"]["order"]
    if order is not None:
        order["trade_record_id"] = "<volatile>"
        order["recommendation_id"] = "<volatile>"
    for section in ("transcript", "evidence", "recommendation"):
        norm.pop(section)
    return norm


class TestExportPack:
    """P3-06 decision-audit export packs."""

    def test_export_pack_golden(self):
        import json

        from tradingagents.pro.dashboard import service

        state = DashboardState(memory=ProMemory())
        run = state.recorder.record_run(
            FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory
        )
        client = TestClient(create_app(state))
        resp = client.get(f"/api/runs/{run.run_id}/export")
        assert resp.status_code == 200
        assert resp.headers["content-disposition"] == (
            f'attachment; filename="run-{run.run_id}.json"')
        pack = resp.json()

        # complete structure: exactly the documented keys, no drift
        assert set(pack) == set(_EXPORT_GOLDEN) | {
            "transcript", "evidence", "recommendation"}
        # P3-07 stamp present with all four provenance keys
        assert set(pack["versions"]) == {
            "git_sha", "prompt_hash", "model_ids", "config_hash"}
        assert pack["versions"]["git_sha"]
        # full transcript, straight from the debate_timeline view helper
        assert pack["transcript"]["entries"]
        assert pack["transcript"] == json.loads(
            json.dumps(service.debate_timeline(run)))
        assert [e["speaker"] for e in pack["transcript"]["entries"]] == [
            "technical_bull", "technical_bear", "macro_bull", "macro_bear",
            "sentiment", "critic", "reflection", "judge"]
        # evidence panels, straight from the evidence_panels view helper
        assert pack["evidence"] == json.loads(
            json.dumps(service.evidence_panels(run)))
        # the ticket is the full recommendation view
        rec = pack["recommendation"]
        assert rec["action"] == "BUY" and rec["confidence"] == 72
        assert rec["invalidation"] and rec["rejection"] is None
        # deterministic remainder matches the checked-in golden verbatim
        assert _normalize_pack(pack) == _EXPORT_GOLDEN

    def test_export_pack_includes_outcome_and_calibration(self):
        state = DashboardState(memory=ProMemory())
        run = state.recorder.record_run(
            FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=state.memory
        )
        trade = state.memory.find_trade_by_recommendation(
            run.recommendation.id)
        state.memory.close_trade(trade.id, pnl=42.0, details={
            "mode": "paper", "venue_order_id": "V-1", "fill_price": 130.2})
        # enough scored outcomes for a p_win estimate (min_n=5)
        for _ in range(5):
            extra = state.memory.record_trade(run.recommendation)
            state.memory.close_trade(extra.id, pnl=1.0, write_lesson=False)
        client = TestClient(create_app(state))
        pack = client.get(f"/api/runs/{run.run_id}/export").json()
        assert pack["outcome"]["pnl"] == 42.0
        assert pack["outcome"]["won"] is True
        assert pack["outcome"]["venue_order_id"] == "V-1"
        assert pack["outcome"]["fill_price"] == 130.2
        assert pack["calibration"]["p_win"] == 1.0
        assert pack["calibration"]["n"] >= 5

    def test_export_unknown_run_is_404(self, client):
        assert client.get("/api/runs/nope/export").status_code == 404


class TestPipelineTriggerEndpoint:
    @pytest.fixture()
    def triggered_client(self):
        from tradingagents.pro.main import PipelineTrigger

        state = DashboardState(memory=ProMemory())

        # a stub trigger keeps this test free of execution wiring
        class StubTrigger(PipelineTrigger):
            def __init__(self):
                super().__init__(service=None)
                self.calls = []

            def run(self, symbol, timeframe):
                self.calls.append((symbol, timeframe))
                return {"ok": True}

        state.trigger = StubTrigger()
        return TestClient(create_app(state)), state.trigger

    def test_started(self, triggered_client):
        client, trigger = triggered_client
        response = client.post("/api/pipeline/run",
                               json={"symbol": "XAUUSD", "timeframe": "1h"})
        assert response.status_code == 202
        assert response.json()["status"] == "started"
        import time
        for _ in range(50):
            if trigger.calls:
                break
            time.sleep(0.02)
        assert trigger.calls == [("XAUUSD", "1h")]

    def test_validation(self, triggered_client):
        client, _ = triggered_client
        assert client.post("/api/pipeline/run",
                           json={"symbol": "DOGE", "timeframe": "1h"}).status_code == 422
        assert client.post("/api/pipeline/run",
                           json={"symbol": "XAUUSD", "timeframe": "5m"}).status_code == 422

    def test_fx_intraday_without_oanda_is_422_not_500(self, triggered_client,
                                                      monkeypatch):
        # yfinance FX fallback is daily-only: the endpoint must refuse the
        # run up front (typed TriggerUnsupported → 422) instead of 202-ing
        # a worker thread that crashes in the builder
        client, trigger = triggered_client
        monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
        response = client.post("/api/pipeline/run",
                               json={"symbol": "EURUSD", "timeframe": "1h"})
        assert response.status_code == 422
        assert "OANDA_API_TOKEN" in response.json()["detail"]
        assert trigger.calls == []  # never reached the run thread
        # daily FX stays runnable on the fallback
        assert client.post("/api/pipeline/run",
                           json={"symbol": "EURUSD",
                                 "timeframe": "1d"}).status_code == 202

    def test_busy_and_untriggered(self, triggered_client):
        client, trigger = triggered_client
        trigger._busy.acquire()
        try:
            assert client.post("/api/pipeline/run",
                               json={"symbol": "XAUUSD", "timeframe": "1h"}).status_code == 409
        finally:
            trigger._busy.release()
        bare = TestClient(create_app(DashboardState()))
        assert bare.post("/api/pipeline/run",
                         json={"symbol": "XAUUSD", "timeframe": "1h"}).status_code == 503


class TestMetricsEndpoint:
    def test_scrapeable_and_open(self, client):
        # no registry attached -> empty but 200 (scrape target always up)
        response = client.get("/metrics")
        assert response.status_code == 200
        assert response.text == ""

    def test_renders_registry(self):
        from tradingagents.pro.observability import MetricsRegistry

        state = DashboardState(memory=ProMemory())
        state.metrics = MetricsRegistry()
        state.metrics.inc("runs_total")
        text = TestClient(create_app(state)).get("/metrics").text
        assert "runs_total 1" in text

    def test_open_with_auth_enabled_and_prometheus_content_type(self):
        # scrapers carry no dashboard credentials: /metrics must bypass the
        # API-key middleware (like /healthz) and serve the exposition
        # content type, even when a token gates every /api route (P2-08)
        from tradingagents.pro.observability import MetricsRegistry

        state = DashboardState(memory=ProMemory())
        state.metrics = MetricsRegistry()
        state.metrics.inc("runs_total")
        state.metrics.inc("iteration_errors_total")
        state.metrics.set_gauge("last_run_ts", 1700000000.0)
        client = TestClient(create_app(state, api_token="secret-token"))

        assert client.get("/api/runs").status_code == 401  # auth is on...
        resp = client.get("/metrics")                      # ...metrics open
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "version=0.0.4" in resp.headers["content-type"]
        assert "# TYPE runs_total counter\nruns_total 1.0" in resp.text
        assert "# TYPE iteration_errors_total counter" in resp.text
        assert "# TYPE last_run_ts gauge\nlast_run_ts 1700000000.0" in resp.text


class TestRolesAndEntitlements:
    """P3-05 multi-tenant auth: users table, roles, operator-only mutation
    endpoints, per-user prefs isolation. Sessions are minted the same way
    TestGoogleSession does — a monkeypatched Firebase verifier — so these
    tests never hit Google; the fake verifier treats the bearer token
    itself as the signed-in email."""

    ENV = {
        "PRO_FIREBASE_PROJECT_ID": "demo-project",
        "PRO_ALLOWED_EMAILS": "op@example.com,eve@example.com",
        "PRO_FIREBASE_WEB_CONFIG": '{"apiKey": "public"}',
    }

    def _setup(self, tmp_path, monkeypatch):
        import tradingagents.pro.dashboard.app as app_module
        from tradingagents.pro.dashboard.prefs import PrefsStore
        from tradingagents.pro.store import EventStore

        for key, value in self.ENV.items():
            monkeypatch.setenv(key, value)

        def fake_verify(id_token, audience):
            assert audience == "demo-project"
            return {"email": id_token, "email_verified": True}

        monkeypatch.setattr(app_module, "_verify_firebase_token", fake_verify)
        store = EventStore(tmp_path / "pro.db")
        state = DashboardState(memory=ProMemory())
        state.prefs = PrefsStore(store=store)
        app = create_app(state, api_token="secret-token")
        return app, state, store

    def _login(self, app, email):
        client = TestClient(app)
        resp = client.post("/api/session",
                           headers={"Authorization": f"Bearer {email}"})
        assert resp.status_code == 200
        return client, resp.json()

    def test_allowlist_seeded_as_operators_once(self, tmp_path, monkeypatch):
        from tradingagents.pro.store import seed_users

        app, state, store = self._setup(tmp_path, monkeypatch)
        users = {u["email"]: u["role"] for u in store.list_users()}
        assert users == {"op@example.com": "operator",
                         "eve@example.com": "operator"}
        # idempotent: a non-empty table is the source of truth — reseeding
        # (another boot, or after an explicit demotion) changes nothing
        store.put_user("eve@example.com", "viewer")
        assert seed_users(store, ["op@example.com", "eve@example.com"]) == 0
        assert store.get_user_role("eve@example.com") == "viewer"

    def test_session_carries_role_claim(self, tmp_path, monkeypatch):
        import base64
        import json

        app, state, store = self._setup(tmp_path, monkeypatch)
        store.put_user("eve@example.com", "viewer")
        client, body = self._login(app, "eve@example.com")
        assert body["identity"] == "eve@example.com"
        assert body["role"] == "viewer"
        payload_b64 = client.cookies.get("__session").split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(
            payload_b64 + "=" * (-len(payload_b64) % 4)))
        assert payload["sub"] == "eve@example.com"
        assert payload["role"] == "viewer"

    def test_viewer_reads_but_cannot_mutate(self, tmp_path, monkeypatch):
        app, state, store = self._setup(tmp_path, monkeypatch)
        store.put_user("eve@example.com", "viewer")
        viewer, _ = self._login(app, "eve@example.com")

        # read-only GETs stay viewer-accessible
        assert viewer.get("/api/overview").status_code == 200
        assert viewer.get("/api/watchlists").status_code == 200
        assert viewer.get("/api/notifications").status_code == 200

        # the operator-only mutation set 403s with a clear detail
        denied = viewer.post("/api/pipeline/run",
                             json={"symbol": "XAUUSD", "timeframe": "1h"})
        assert denied.status_code == 403
        assert "viewer" in denied.json()["detail"]
        assert viewer.post("/api/flatten",
                           json={"confirm": "FLATTEN"}).status_code == 403
        assert viewer.post("/api/watchlists",
                           json={"name": "w", "symbols": []}).status_code == 403
        assert viewer.delete("/api/watchlists/w").status_code == 403
        assert viewer.put("/api/prefs", json={}).status_code == 403
        assert viewer.post("/api/backtest/run", json={}).status_code == 403
        assert viewer.post("/api/notifications/read").status_code == 403

    def test_operator_passes_the_mutation_gate(self, tmp_path, monkeypatch):
        app, state, store = self._setup(tmp_path, monkeypatch)
        operator, body = self._login(app, "op@example.com")
        assert body["role"] == "operator"
        created = operator.post("/api/watchlists",
                                json={"name": "majors", "symbols": ["XAUUSD"]})
        assert created.status_code == 200
        # authz passed; these fail later for wiring reasons, not role
        assert operator.post("/api/flatten",
                             json={"confirm": "FLATTEN"}).status_code == 503
        assert operator.post(
            "/api/pipeline/run",
            json={"symbol": "XAUUSD", "timeframe": "1h"}).status_code == 503

    def test_prefs_isolated_per_identity(self, tmp_path, monkeypatch):
        app, state, store = self._setup(tmp_path, monkeypatch)
        op, _ = self._login(app, "op@example.com")
        eve, _ = self._login(app, "eve@example.com")

        op.post("/api/watchlists", json={"name": "ops", "symbols": ["XAUUSD"]})
        eve.post("/api/watchlists", json={"name": "eves", "symbols": ["BTC-USD"]})

        assert [w["name"] for w in op.get("/api/watchlists").json()] == ["ops"]
        assert [w["name"] for w in eve.get("/api/watchlists").json()] == ["eves"]

        # each identity gets its own kv document; the legacy shared one
        # (token auth + the service's system-state writes) is untouched
        assert store.get_kv("dashboard_prefs:op@example.com") is not None
        assert store.get_kv("dashboard_prefs:eve@example.com") is not None
        assert store.get_kv("dashboard_prefs") is None

        # token auth reads/writes the legacy shared document
        token_client = TestClient(app,
                                  headers={"X-API-Key": "secret-token"})
        token_client.post("/api/watchlists",
                          json={"name": "shared", "symbols": []})
        assert [w["name"] for w in
                token_client.get("/api/watchlists").json()] == ["shared"]
        assert [w["name"] for w in op.get("/api/watchlists").json()] == ["ops"]

    def test_token_auth_unchanged_full_operator(self, tmp_path, monkeypatch):
        app, state, store = self._setup(tmp_path, monkeypatch)
        client = TestClient(app, headers={"X-API-Key": "secret-token"})
        minted = client.post("/api/session")
        assert minted.status_code == 200
        assert minted.json()["identity"] is None
        assert minted.json()["role"] == "operator"
        assert client.put("/api/prefs", json={}).status_code == 200
        assert client.post("/api/notifications/read").status_code == 200

    def test_users_admin_endpoint(self, tmp_path, monkeypatch):
        app, state, store = self._setup(tmp_path, monkeypatch)
        store.put_user("eve@example.com", "viewer")
        op, _ = self._login(app, "op@example.com")
        eve, _ = self._login(app, "eve@example.com")

        listed = op.get("/api/users").json()["users"]
        assert {u["email"]: u["role"] for u in listed} == {
            "op@example.com": "operator", "eve@example.com": "viewer"}
        assert all(u["created_at"] for u in listed)

        # operator-only in BOTH directions: the roster GET and the upsert
        assert eve.get("/api/users").status_code == 403
        assert eve.post("/api/users", json={
            "email": "eve@example.com", "role": "operator"}).status_code == 403

        promoted = op.post("/api/users", json={
            "email": "eve@example.com", "role": "operator"})
        assert promoted.status_code == 200
        assert promoted.json()["role"] == "operator"
        assert store.get_user_role("eve@example.com") == "operator"

        # validation: bad role / malformed email / non-string body
        assert op.post("/api/users", json={
            "email": "x@example.com", "role": "admin"}).status_code == 422
        assert op.post("/api/users", json={
            "email": "not-an-email", "role": "viewer"}).status_code == 422
        assert op.post("/api/users", json={
            "email": None, "role": "viewer"}).status_code == 422

    def test_demoted_operator_loses_mutations_immediately(self, tmp_path,
                                                          monkeypatch):
        """CONTROLS §1: mutating verbs re-resolve the role from the users
        table per request — a demotion bites on the very next mutation,
        even though the 7-day session JWT still carries role=operator.
        Reads keep the fast signed-claim path (accepted, documented lag)."""
        app, state, store = self._setup(tmp_path, monkeypatch)
        op, _ = self._login(app, "op@example.com")
        assert op.post("/api/watchlists",
                       json={"name": "w", "symbols": []}).status_code == 200
        store.put_user("op@example.com", "viewer")  # demotion
        # the outstanding cookie still says operator (stateless JWT), but
        # the mutation gate checks the users table NOW: 403 immediately
        denied = op.post("/api/watchlists",
                         json={"name": "w2", "symbols": []})
        assert denied.status_code == 403
        assert "viewer" in denied.json()["detail"]
        assert op.put("/api/prefs", json={}).status_code == 403
        # reads keep working under the stale claim (accepted read-path lag)
        assert op.get("/api/overview").status_code == 200
        assert op.get("/api/watchlists").status_code == 200
        # the next page load re-establishes and mints an honest viewer cookie
        again = op.post("/api/session")
        assert again.json()["role"] == "viewer"
        assert op.post("/api/watchlists",
                       json={"name": "w3", "symbols": []}).status_code == 403
        # and a promotion is picked up just as immediately
        store.put_user("op@example.com", "operator")
        assert op.post("/api/watchlists",
                       json={"name": "w4", "symbols": []}).status_code == 200


# --- P5-05 run diff endpoint --------------------------------------------------------

class TestRunDiffEndpoint:
    """GET /api/runs/{id}/diff?against=<run_id|previous>."""

    def _state(self):
        from tests.test_pro_dashboard_views import diff_evidence, diff_run
        from tradingagents.contracts import Direction, TradeAction

        state = DashboardState(memory=ProMemory())
        state.recorder.runs = [
            diff_run("gold-1", minutes=0, symbol="XAUUSD",
                     action=TradeAction.BUY, confidence=62,
                     evidence=[diff_evidence("rsi", Direction.BULLISH, 70)]),
            diff_run("btc-1", minutes=30, symbol="BTCUSD"),
            diff_run("gold-2", minutes=60, symbol="XAUUSD", action=None,
                     rejection={"stage": "critic", "reasons": ["thin evidence"]},
                     evidence=[diff_evidence("rsi", Direction.BEARISH, 40)]),
        ]
        return state

    def test_default_against_previous_resolves_same_symbol(self):
        client = TestClient(create_app(self._state()))
        resp = client.get("/api/runs/gold-2/diff")
        assert resp.status_code == 200
        body = resp.json()
        # the BTC run sits between them in time and must be skipped
        assert body["earlier"]["run_id"] == "gold-1"
        assert body["later"]["run_id"] == "gold-2"
        assert body["symbol"] == "XAUUSD"
        assert body["headline"] == "BUY 62 → rejected at critic"
        assert body["headline_driver"] == "verdict"
        assert body["evidence"]["flipped"][0]["agent_id"] == "rsi"

    def test_explicit_against_run_id(self):
        client = TestClient(create_app(self._state()))
        body = client.get("/api/runs/gold-1/diff?against=gold-2").json()
        assert body["earlier"]["run_id"] == "gold-1"  # always forward in time
        assert body["later"]["run_id"] == "gold-2"

    def test_cross_symbol_comparison_is_422_with_a_clear_message(self):
        client = TestClient(create_app(self._state()))
        resp = client.get("/api/runs/gold-2/diff?against=btc-1")
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "different symbols" in detail
        assert "XAUUSD" in detail and "BTCUSD" in detail

    def test_unknown_runs_and_first_run_are_404(self):
        client = TestClient(create_app(self._state()))
        assert client.get("/api/runs/nope/diff").status_code == 404
        assert client.get(
            "/api/runs/gold-2/diff?against=nope").status_code == 404
        # gold-1 is the symbol's first run: no earlier run to compare against
        first = client.get("/api/runs/gold-1/diff")
        assert first.status_code == 404
        assert "first recorded run" in first.json()["detail"]

    def test_diff_is_auth_gated_like_every_run_view(self, monkeypatch):
        monkeypatch.delenv("PRO_DASHBOARD_TOKEN", raising=False)
        client = TestClient(create_app(self._state(), api_token="s3cret"))
        assert client.get("/api/runs/gold-2/diff").status_code == 401
        assert client.get("/api/runs/gold-2/diff",
                          headers={"X-API-Key": "s3cret"}).status_code == 200
