"""Dashboard view models over a real recorded pipeline run (no FastAPI)."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.test_pro_agents_base import make_bars, make_snapshot
from tests.test_pro_memory_facade import make_recommendation
from tests.test_pro_pipeline_graph import CONFIG, FakePipelineLLM, pipeline_snapshot
from tradingagents.contracts import (
    AgentEvidence,
    AgentTeam,
    DataRef,
    Direction,
    MarketRegime,
    MetricReading,
    RiskLimits,
    SourceAttribution,
    SourceType,
    Timeframe,
    TradeAction,
)
from tradingagents.pro.backtest import BacktestEngine, BarReplay, SimBroker
from tradingagents.pro.dashboard import PipelineRecorder
from tradingagents.pro.dashboard.recorder import RunRecord
from tradingagents.pro.dashboard.service import (
    agent_performance,
    alert_feed,
    backtest_view,
    debate_timeline,
    evidence_panels,
    market_overview,
    memory_insights,
    previous_run_for,
    recommendation_view,
    run_diff,
    system_status,
    trade_journal,
)
from tradingagents.pro.execution import (
    VENUES,
    AuditLog,
    CircuitBreaker,
    ExecutionRouter,
    KillSwitch,
    PaperVenueAdapter,
)
from tradingagents.pro.memory import ProMemory
from tradingagents.pro.pipeline.nodes import ABSTAINED_ARGUMENT


@pytest.fixture()
def recorded():
    memory = ProMemory()
    recorder = PipelineRecorder()
    run = recorder.record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=memory
    )
    return recorder, run, memory


class TestRunViews:
    def test_market_overview(self, recorded):
        _, run, _ = recorded
        view = market_overview(run)
        assert view["symbol"] == "XAUUSD"
        assert view["execution_status"] == "accepted:paper"
        assert view["regime"] is not None
        assert view["rejected_at"] is None
        assert market_overview(None) == {"status": "no runs yet"}

    def test_recommendation_view_renders_full_schema(self, recorded):
        _, run, _ = recorded
        view = recommendation_view(run.recommendation)
        # every field of the Phase 0 contract is present
        for field in (
            "action", "confidence", "entry_price", "stop_loss", "take_profits",
            "position_size", "market_regime", "evidence", "counterarguments",
            "vote_breakdown", "historical_analogs", "risk_reward",
        ):
            assert field in view, field
        assert view["action"] == "BUY"
        assert view["vote_tally"]["BUY"] >= 1
        assert view["n_evidence"] == len(view["evidence"])
        assert view["invalidation"] is None  # not supplied

    def test_recommendation_view_carries_invalidation(self, recorded):
        _, run, _ = recorded
        view = recommendation_view(
            run.recommendation, invalidation="close below 2300 invalidates"
        )
        assert view["invalidation"] == "close below 2300 invalidates"

    def test_recommendation_view_explains_rejection(self):
        view = recommendation_view(
            None, rejection={"stage": "risk_gate", "reasons": ["stop too wide"]}
        )
        assert view["status"] == "rejected"
        assert view["rejection"]["stage"] == "risk_gate"


def make_router() -> ExecutionRouter:
    limits = RiskLimits()
    return ExecutionRouter(
        adapter=PaperVenueAdapter(VENUES["mt5"]),
        limits=limits,
        kill_switch=KillSwitch(),
        breaker=CircuitBreaker(limits, equity_base=100_000.0),
        audit=AuditLog(),
    )


class TestSystemStatus:
    def test_unattached(self):
        view = system_status(None)
        assert view["attached"] is False
        assert view["trading_halted"] is None
        assert view["live_armed"] is False  # arming absent = all paper

    def test_healthy_router(self):
        router = make_router()
        router.local_book["XAUUSD"] = 5.0
        view = system_status(router, equity=100_000.0)
        assert view["trading_halted"] is False
        assert view["kill_switch"]["engaged"] is False
        assert view["circuit_breaker"]["tripped"] is False
        [pos] = view["open_positions"]
        assert pos["symbol"] == "XAUUSD" and pos["quantity"] == 5.0
        # book-only position (no adapter fill): every P&L field honest-null
        assert pos["entry_price"] is None and pos["unrealized_pnl"] is None
        assert pos["mark_source"] == "entry"
        assert view["equity"] == 100_000.0
        assert view["unrealized_total"] is None

    def test_kill_switch_halts(self):
        router = make_router()
        router.kill_switch.engage("operator halt")
        view = system_status(router)
        assert view["trading_halted"] is True
        assert view["kill_switch"]["reason"] == "operator halt"

    def test_tripped_breaker_halts(self):
        router = make_router()
        for _ in range(3):  # default consecutive-loss limit
            router.breaker.record_trade_result(-10.0)
        view = system_status(router)
        assert view["trading_halted"] is True
        assert "consecutive losses" in view["circuit_breaker"]["reason"]


class TestAlertFeed:
    def test_accepted_clean_run_raises_no_alerts(self, recorded):
        recorder, _, _ = recorded
        assert alert_feed(recorder.runs) == {"alerts": []}

    def test_alerts_from_degraded_and_rejected_runs(self, recorded):
        recorder, run, _ = recorded
        run.state["snapshot"] = run.state["snapshot"].model_copy(
            update={"missing_feeds": ["news:quarantined:0", "macro:fred"]}
        )
        run.state["rejection"] = {"stage": "risk_gate", "reasons": ["stop too wide"]}
        run.state["execution_status"] = "blocked:reconciliation"
        feed = alert_feed(recorder.runs)["alerts"]
        severities = {a["severity"] for a in feed}
        assert severities == {"critical", "warning", "info"}
        quarantine = next(a for a in feed if a["severity"] == "critical")
        assert "prompt injection" in quarantine["text"]
        assert all(a["run_id"] == run.run_id for a in feed)

    def test_consecutive_same_stage_rejections_coalesce(self):
        # trader review: five hourly event-gate refusals stacked as five
        # near-duplicate warnings — they must merge (varying countdown text)
        memory = ProMemory()
        recorder = PipelineRecorder()
        for i in range(3):
            run = recorder.record_run(
                FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=memory)
            run.state["rejection"] = {
                "stage": "event_gate",
                "reasons": [f"Retail Sales in {3 - i}.0h — entries blocked"],
            }
        feed = alert_feed(recorder.runs)["alerts"]
        gate = [a for a in feed if "event_gate" in a["text"]]
        assert len(gate) == 1
        assert gate[0]["count"] == 3
        # newest occurrence wins the display text
        assert "1.0h" in gate[0]["text"]

    def test_debate_timeline_orders_speakers(self, recorded):
        _, run, _ = recorded
        view = debate_timeline(run)
        speakers = [e["speaker"] for e in view["entries"]]
        assert "technical_bull" in speakers and "judge" in speakers
        assert speakers.index("technical_bull") < speakers.index("judge")
        assert view["node_sequence"][0] == "prepare"
        assert view["rejection"] is None
        # per-node latency rides parallel to node_sequence (R9 pipeline board)
        assert [t["node"] for t in view["node_times"]] == view["node_sequence"]
        assert all(t["elapsed_s"] >= 0 for t in view["node_times"])
        assert view["execution_status"] == "accepted:paper"

    def test_evidence_panels_grouped_by_team(self, recorded):
        _, run, _ = recorded
        panels = evidence_panels(run)
        assert "technical" in panels and panels["technical"]
        entry = panels["technical"][0]
        assert {"agent_id", "direction", "confidence", "claim",
                "data_refs", "sources"} <= set(entry)


class TestPortfolioViews:
    def test_trade_journal_totals(self):
        memory = ProMemory()
        for pnl in (120.0, -60.0):
            trade = memory.record_trade(make_recommendation())
            memory.close_trade(trade.id, pnl=pnl)
        journal = trade_journal(memory)
        assert journal["n_trades"] == 2
        assert journal["total_pnl"] == pytest.approx(60.0)
        assert journal["win_rate"] == pytest.approx(0.5)
        assert journal["entries"][0]["action"] == "BUY"

    def test_journal_performance_reconstructs_equity_curve(self):
        from tradingagents.pro.dashboard.service import journal_performance

        memory = ProMemory()
        for pnl in (120.0, -60.0, 90.0):
            trade = memory.record_trade(make_recommendation())
            memory.close_trade(trade.id, pnl=pnl)
        perf = journal_performance(memory, starting_equity=1_000.0)
        assert perf["equity_curve"] == [1_000.0, 1_120.0, 1_060.0, 1_150.0]
        assert perf["n_trades"] == 3
        assert perf["expectancy"] == pytest.approx(50.0)
        assert perf["profit_factor"] == pytest.approx(210.0 / 60.0)
        # peak 1120 -> trough 1060
        assert perf["max_drawdown"] == pytest.approx(60.0 / 1_120.0)
        assert perf["total_return"] == pytest.approx(0.15)

    def test_portfolio_exposure_aggregates_directionally(self):
        from tradingagents.pro.dashboard.service import portfolio_exposure

        positions = [
            {"symbol": "XAUUSD", "quantity": -2.0, "mark_price": 4000.0,
             "mark_source": "live"},
            {"symbol": "BTC-USD", "quantity": 0.1, "mark_price": 64000.0,
             "mark_source": "eod"},
            # unknown mark contributes nothing, never a fabricated number
            {"symbol": "SOL-USD", "quantity": 5.0, "mark_price": None,
             "mark_source": "entry"},
        ]
        view = portfolio_exposure(positions, equity=100_000.0,
                                  max_open_positions=3)
        assert view["n_positions"] == 3 and view["n_priced"] == 2
        assert view["short_exposure_pct"] == pytest.approx(8.0)
        assert view["long_exposure_pct"] == pytest.approx(6.4)
        assert view["gross_exposure_pct"] == pytest.approx(14.4)
        assert view["net_exposure_pct"] == pytest.approx(-1.6)
        assert view["largest_position_pct"] == pytest.approx(8.0)

    def test_risk_budget_exposes_daily_loss_vs_limit(self):
        from tradingagents.pro.dashboard.service import risk_budget

        limits = RiskLimits(max_daily_loss_pct=3.0)
        breaker = CircuitBreaker(limits, equity_base=100_000.0)
        breaker.record_trade_result(-1_500.0)
        router = ExecutionRouter(
            adapter=PaperVenueAdapter(VENUES["mt5"], starting_cash=100_000.0),
            limits=limits, kill_switch=KillSwitch(), breaker=breaker,
            audit=AuditLog(),
        )
        budget = risk_budget(router)
        assert budget["attached"] is True
        assert budget["daily_loss_limit_usd"] == pytest.approx(3_000.0)
        assert budget["daily_loss_used_usd"] == pytest.approx(1_500.0)
        assert budget["daily_loss_used_pct_of_budget"] == pytest.approx(50.0)
        assert budget["tripped"] is False
        assert risk_budget(None) == {"attached": False}

    def test_backtest_view_with_monte_carlo(self):
        from tests.pro_fakes import make_bars
        from tradingagents.contracts import AssetClass, ProConfig, TradingMode
        from tradingagents.pro.backtest import monte_carlo_summary

        config = ProConfig(asset=AssetClass.GOLD, mode=TradingMode.BACKTEST)
        replay = BarReplay("XAUUSD", AssetClass.GOLD, make_bars(n=140), window=60)
        result = BacktestEngine(
            FakePipelineLLM(), config, replay,
            broker=SimBroker(initial_equity=100_000.0),
            min_history=60, decide_every=10,
        ).run()
        mc = monte_carlo_summary([t.pnl for t in result.trades] * 3, 100_000.0,
                                 n_paths=50) if len(result.trades) >= 1 else None
        view = backtest_view(result, mc)
        assert view["report"]["n_trades"] == len(result.trades)
        assert len(view["equity_curve"]) == len(result.equity_curve)
        if mc:
            assert "monte_carlo" in view
        assert backtest_view(None) == {"status": "no backtest yet"}

    def test_memory_insights_counts_and_lessons(self):
        memory = ProMemory()
        trade = memory.record_trade(make_recommendation())
        memory.close_trade(trade.id, pnl=-10.0, lesson="sized too large for regime")
        insights = memory_insights(memory)
        assert insights["counts"]["trade"] == 1
        assert insights["counts"]["mistake"] == 1
        assert any("sized too large" in item["text"]
                   for item in insights["recent_lessons"])


class TestAgentPerformance:
    def test_hit_rates_scored_against_outcomes(self, recorded):
        recorder, run, memory = recorded
        rec = run.recommendation
        trade = memory.find_trade_by_recommendation(rec.id)
        assert trade is not None  # pipeline recorded it at execution
        memory.close_trade(trade.id, pnl=500.0)  # the BUY won

        perf = agent_performance(recorder.runs, memory)
        assert "judge" in perf
        judge = perf["judge"]
        assert judge["votes"] == 1
        assert judge["scored"] == 1
        assert judge["hit_rate"] == 1.0  # judge voted BUY, trade won
        # every evidence agent voted BUY (fake llm) -> all correct
        assert all(row["hit_rate"] in (None, 1.0) for row in perf.values())

    def test_unscored_agents_have_null_hit_rate(self, recorded):
        recorder, run, memory = recorded  # trade never closed
        perf = agent_performance(recorder.runs, memory)
        assert all(row["hit_rate"] is None for row in perf.values())
        assert all(row["votes"] >= 1 for row in perf.values())


# --- P5-05 run diff ---------------------------------------------------------------

DIFF_T0 = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
STAMP_A = {"git_sha": "aaaa1111", "prompt_hash": "p111",
           "model_ids": ["m-1"], "config_hash": "c111"}


def diff_evidence(agent_id: str, direction: Direction, confidence: int,
                  team: AgentTeam = AgentTeam.TECHNICAL) -> AgentEvidence:
    return AgentEvidence(
        agent_id=agent_id, team=team, claim=f"{agent_id} says {direction.value}",
        direction=direction, confidence=confidence, timeframe=Timeframe.D1,
        data_refs=[DataRef(name="X", value=1.0, source="indicator_engine")],
        sources=[SourceAttribution(id="indicator_engine",
                                   type=SourceType.INDICATOR, name="engine")],
    )


def diff_run(run_id: str, *, minutes: int = 0, symbol: str = "XAUUSD",
             action=TradeAction.BUY, confidence: int = 62, rejection=None,
             evidence=(), gates=None, metrics=None, missing_feeds=(),
             regime=MarketRegime.TRENDING_UP, versions=STAMP_A,
             debate=(), n_bars: int = 60) -> RunRecord:
    """A scripted RunRecord: only the state the diff reads, so each test
    isolates exactly one kind of change."""
    snapshot = make_snapshot(symbol=symbol, bars=make_bars(n=n_bars),
                             missing_feeds=list(missing_feeds))
    rec = None
    if action is not None:
        rec = make_recommendation(action=action, symbol=symbol).model_copy(
            update={"confidence": confidence})
    state = {
        "snapshot": snapshot,
        "recommendation": rec,
        "rejection": rejection,
        "regime": regime,
        "versions": versions,
        "debate": list(debate),
        "evidence_by_team": {AgentTeam.TECHNICAL.value: list(evidence)},
        "gate_results": gates or {},
        "quant_metrics": metrics or {},
    }
    return RunRecord(run_id=run_id, started_at=DIFF_T0 + timedelta(minutes=minutes),
                     symbol=symbol, asset="gold", state=state)


class TestRunDiff:
    def test_verdict_flip_headline_and_delta(self):
        earlier = diff_run("r1", minutes=0, action=TradeAction.BUY, confidence=62)
        later = diff_run("r2", minutes=60, action=None,
                         rejection={"stage": "critic", "reasons": ["thin evidence"]})
        diff = run_diff(earlier, later)

        assert diff["headline"] == "BUY 62 → rejected at critic"
        assert diff["headline_driver"] == "verdict"
        assert diff["verdict"]["action_changed"] is True
        assert diff["verdict"]["rejection_changed"] is True
        assert diff["verdict"]["before"]["confidence"] == 62
        assert diff["verdict"]["after"]["rejected_at"] == "critic"
        assert diff["earlier"]["run_id"] == "r1"
        assert diff["later"]["run_id"] == "r2"

    def test_argument_order_does_not_matter(self):
        earlier = diff_run("r1", minutes=0)
        later = diff_run("r2", minutes=60)
        # the diff always reads forward in time, whichever way it is called
        assert run_diff(later, earlier)["earlier"]["run_id"] == "r1"
        assert run_diff(later, earlier)["later"]["run_id"] == "r2"

    def test_agent_flip_abstention_and_movers(self):
        earlier = diff_run("r1", minutes=0, evidence=[
            diff_evidence("rsi", Direction.BULLISH, 70),
            diff_evidence("macd", Direction.BEARISH, 55),
            diff_evidence("vol", Direction.NEUTRAL, 50),
        ])
        later = diff_run("r2", minutes=60, evidence=[
            diff_evidence("rsi", Direction.BEARISH, 65),
            diff_evidence("macd", Direction.BEARISH, 80),
            diff_evidence("flows", Direction.BULLISH, 60),
        ])
        diff = run_diff(earlier, later)

        flipped = diff["evidence"]["flipped"]
        assert [f["agent_id"] for f in flipped] == ["rsi"]
        # both stances travel with the flip — never just "changed"
        assert flipped[0]["before_direction"] == "bullish"
        assert flipped[0]["after_direction"] == "bearish"
        assert flipped[0]["before_claim"].endswith("bullish")

        assert [a["agent_id"] for a in diff["evidence"]["newly_abstaining"]] == ["vol"]
        assert [s["agent_id"] for s in diff["evidence"]["newly_speaking"]] == ["flows"]

        movers = diff["evidence"]["confidence_movers"]
        assert [m["agent_id"] for m in movers] == ["macd", "rsi"]  # |+25| > |-5|
        assert movers[0]["delta"] == 25
        assert movers[1]["delta"] == -5
        assert diff["headline_driver"] == "evidence"
        assert diff["headline"] == "rsi flipped bullish → bearish"

    def test_dropped_debate_turn_counts_as_newly_abstaining(self):
        speaking = {"speaker": "technical_bull", "stance": "bull",
                    "argument": "trend intact", "confidence": 70,
                    "abstained": False}
        dropped = {"speaker": "technical_bull", "stance": "bull",
                   "argument": ABSTAINED_ARGUMENT, "confidence": None,
                   "abstained": True}
        diff = run_diff(diff_run("r1", minutes=0, debate=[speaking]),
                        diff_run("r2", minutes=60, debate=[dropped]))
        assert [a["agent_id"] for a in diff["evidence"]["newly_abstaining"]] == [
            "technical_bull"]

    def test_feed_loss_and_metric_drift(self):
        earlier = diff_run("r1", minutes=0, metrics={
            "ATR_14": MetricReading(name="ATR_14", value=2.0, unit="usd"),
            "RSI_14": MetricReading(name="RSI_14", value=50.0),
        })
        later = diff_run("r2", minutes=60, missing_feeds=["news:reuters"], metrics={
            "ATR_14": MetricReading(name="ATR_14", value=3.0, unit="usd"),
            "RSI_14": MetricReading(name="RSI_14", value=52.0),  # +4%, immaterial
        })
        diff = run_diff(earlier, later)

        assert diff["data"]["feeds_lost"] == ["news:reuters"]
        assert diff["data"]["feeds_restored"] == []
        # only the material mover survives the 10% threshold
        assert [m["name"] for m in diff["data"]["metrics"]] == ["ATR_14"]
        assert diff["data"]["metrics"][0]["pct_change"] == pytest.approx(50.0)
        assert diff["data"]["pct_threshold"] == 10.0
        # feed loss outranks metric drift inside the data tier
        assert diff["headline_driver"] == "data"
        assert "feed lost: news:reuters" in diff["headline"]

    def test_feed_restored_and_regime_change(self):
        earlier = diff_run("r1", minutes=0, missing_feeds=["news:reuters"],
                           regime=MarketRegime.TRENDING_UP)
        later = diff_run("r2", minutes=60, regime=MarketRegime.RANGING)
        diff = run_diff(earlier, later)
        assert diff["data"]["feeds_restored"] == ["news:reuters"]
        assert diff["data"]["regime"] == {"before": "trending_up",
                                          "after": "ranging", "changed": True}
        assert "feed restored" in diff["headline"]

    def test_gate_flip_reports_reasons(self):
        earlier = diff_run("r1", minutes=0, gates={
            "risk": {"passed": True, "reasons": []}})
        later = diff_run("r2", minutes=60, gates={
            "risk": {"passed": False, "reasons": ["stop too wide"]},
            "critic": {"passed": True, "issues": []}})
        diff = run_diff(earlier, later)

        changed = diff["gates"]["changed"]
        assert [g["gate"] for g in changed] == ["risk"]
        assert changed[0]["before_passed"] is True
        assert changed[0]["after_passed"] is False
        assert changed[0]["after_reasons"] == ["stop too wide"]
        assert [g["gate"] for g in diff["gates"]["added"]] == ["critic"]
        assert diff["headline_driver"] == "gate"
        assert diff["headline"] == "risk gate pass → fail: stop too wide"

    def test_versions_change_outranks_every_other_driver(self):
        """The precedence rule's whole point: when the code moved, nothing
        downstream is honestly attributable to the market."""
        earlier = diff_run("r1", minutes=0, action=TradeAction.BUY,
                           confidence=62, versions=STAMP_A,
                           gates={"risk": {"passed": True, "reasons": []}},
                           evidence=[diff_evidence("rsi", Direction.BULLISH, 70)])
        later = diff_run(
            "r2", minutes=60, action=None,
            rejection={"stage": "critic", "reasons": ["thin"]},
            missing_feeds=["news:reuters"],
            versions={**STAMP_A, "git_sha": "bbbb2222", "prompt_hash": "p222"},
            gates={"risk": {"passed": False, "reasons": ["stop too wide"]}},
            evidence=[diff_evidence("rsi", Direction.BEARISH, 40)])
        diff = run_diff(earlier, later)

        assert diff["headline_driver"] == "versions"
        assert "the code changed, not just the market" in diff["headline"]
        assert diff["versions"]["changed"] is True
        assert [f["field"] for f in diff["versions"]["fields"]] == [
            "git_sha", "prompt_hash"]
        assert diff["precedence"][0] == "versions"
        # the lower-precedence facts are still all reported, just not headlined
        assert diff["verdict"]["action_changed"] is True
        assert diff["gates"]["changed"][0]["gate"] == "risk"
        assert diff["evidence"]["flipped"][0]["agent_id"] == "rsi"
        assert diff["data"]["feeds_lost"] == ["news:reuters"]

    def test_unstamped_run_is_not_claimed_unchanged(self):
        diff = run_diff(diff_run("r1", minutes=0, versions=None),
                        diff_run("r2", minutes=60, versions=STAMP_A))
        assert diff["versions"]["comparable"] is False
        assert diff["versions"]["changed"] is None  # unknown, not False
        assert "unknown" in diff["versions"]["note"]

    def test_no_change_says_so(self):
        earlier = diff_run("r1", minutes=0,
                           evidence=[diff_evidence("rsi", Direction.BULLISH, 70)],
                           gates={"risk": {"passed": True, "reasons": []}})
        later = diff_run("r2", minutes=60,
                         evidence=[diff_evidence("rsi", Direction.BULLISH, 70)],
                         gates={"risk": {"passed": True, "reasons": []}})
        diff = run_diff(earlier, later)

        assert diff["headline_driver"] == "none"
        assert diff["headline"].startswith("no material change")
        assert diff["verdict"]["action_changed"] is False
        assert diff["verdict"]["confidence_delta"] == 0
        assert diff["gates"]["changed"] == []
        assert diff["evidence"]["flipped"] == []
        assert diff["data"]["metrics"] == []

    def test_confidence_only_move_is_its_own_driver(self):
        diff = run_diff(diff_run("r1", minutes=0, confidence=62),
                        diff_run("r2", minutes=60, confidence=71))
        assert diff["headline_driver"] == "confidence"
        assert diff["verdict"]["confidence_delta"] == 9
        assert diff["headline"] == "same BUY call, confidence 62 → 71"

    def test_bar_window_advance_is_reported(self):
        diff = run_diff(diff_run("r1", minutes=0, n_bars=60),
                        diff_run("r2", minutes=60, n_bars=62))
        bars = diff["data"]["bars"]
        assert bars["before_n"] == 60
        assert bars["after_n"] == 62
        assert bars["new_bars"] == 2
        assert bars["after_last_bar"] > bars["before_last_bar"]

    def test_cross_symbol_and_self_diff_refuse(self):
        with pytest.raises(ValueError, match="different symbols"):
            run_diff(diff_run("r1", symbol="XAUUSD"),
                     diff_run("r2", minutes=60, symbol="BTCUSD"))
        with pytest.raises(ValueError, match="against itself"):
            run_diff(diff_run("r1"), diff_run("r1"))


class TestPreviousRunFor:
    def test_picks_most_recent_earlier_run_of_same_symbol(self):
        a = diff_run("a", minutes=0, symbol="XAUUSD")
        b = diff_run("b", minutes=10, symbol="BTCUSD")
        c = diff_run("c", minutes=20, symbol="XAUUSD")
        d = diff_run("d", minutes=30, symbol="XAUUSD")
        runs = [a, b, c, d]

        assert previous_run_for(runs, d) is c
        assert previous_run_for(runs, c) is a   # b is a different symbol
        assert previous_run_for(runs, a) is None  # first run for the symbol
        assert previous_run_for(runs, b) is None
