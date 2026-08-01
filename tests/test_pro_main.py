"""Production entrypoint assembly (tradingagents.pro.main)."""

import pytest

fastapi = pytest.importorskip("fastapi")

from tests.test_pro_pipeline_graph import FakePipelineLLM  # noqa: E402
from tradingagents.pro.main import has_llm_key, loop_enabled  # noqa: E402


class TestLoopEnabled:
    def test_disabled_flag_wins(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        monkeypatch.setenv("PRO_LOOP_DISABLED", "1")
        assert loop_enabled() is False

    def test_requires_provider_key(self, monkeypatch):
        monkeypatch.delenv("PRO_LOOP_DISABLED", raising=False)
        monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "deepseek")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        assert loop_enabled() is False
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        assert loop_enabled() is True

    def test_has_llm_key_ignores_loop_disabled(self, monkeypatch):
        # PRO_LOOP_DISABLED=1 must only gate the periodic thread — the
        # service (and on-demand runs) still need has_llm_key() to see the
        # real key so main() wires state.trigger regardless (Cloud Run
        # hosting: no automatic loop, but "Run pipeline now" still works).
        monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "deepseek")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        monkeypatch.setenv("PRO_LOOP_DISABLED", "1")
        assert has_llm_key() is True
        assert loop_enabled() is False


class TestBuildService:
    def test_assembles_full_stack_and_runs_one_iteration(self, tmp_path, monkeypatch):
        # snapshot source must not hit the network: patch the builder
        import tradingagents.pro.main as main_module
        from tests.test_pro_pipeline_graph import pipeline_snapshot
        from tradingagents.pro.ingestion import builder as builder_module

        seen = {}

        class FakeBuilder:
            def build(self, symbol, asset, **kwargs):
                seen["symbol"] = symbol
                return pipeline_snapshot()

        monkeypatch.setattr(builder_module, "build_gold_pipeline",
                            lambda *a, **k: FakeBuilder())
        monkeypatch.setattr(main_module, "build_service",
                            main_module.build_service)  # keep reference
        # hermetic event store: the default path persists across test runs,
        # and the unchanged-bar loop skip would remember the fake snapshot's
        # fixed bar from a previous run and skip this one
        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "pro.db"))

        service, state = main_module.build_service(
            llm=FakePipelineLLM(), data_dir=tmp_path
        )
        assert state.router is service.router
        assert (tmp_path / "dashboard_prefs.json").exists() is False  # lazy
        summary = service.run_once()
        # venue-tradable display symbol, never the vendor ticker (first
        # container run was refused at validation with GC=F)
        assert seen["symbol"] == "XAUUSD"
        assert summary["run_id"]
        assert state.latest_run() is not None
        assert (tmp_path / "audit.jsonl").exists()
        assert service.router.audit.verify()

    def test_env_knobs_var_caps_and_recorder_retention(self, tmp_path, monkeypatch):
        # PRO_MAX_PORTFOLIO_VAR_PCT / PRO_MAX_CORRELATED_GROSS_PCT plumb the
        # P2-05 portfolio caps; PRO_MAX_RUNS bounds recorder boot RAM
        from tradingagents.pro.main import build_service

        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "pro.db"))
        monkeypatch.setenv("PRO_MAX_PORTFOLIO_VAR_PCT", "2.5")
        monkeypatch.setenv("PRO_MAX_CORRELATED_GROSS_PCT", "40")
        monkeypatch.setenv("PRO_MAX_RUNS", "7")
        service, state = build_service(llm=FakePipelineLLM(),
                                       data_dir=tmp_path)
        assert service.router.limits.max_portfolio_var_pct == 2.5
        assert service.router.limits.max_correlated_gross_pct == 40.0
        assert state.recorder.max_runs == 7

    def test_env_knobs_unset_keep_contract_defaults(self, tmp_path, monkeypatch):
        from tradingagents.pro.main import build_service

        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "pro.db"))
        for var in ("PRO_MAX_PORTFOLIO_VAR_PCT", "PRO_MAX_CORRELATED_GROSS_PCT",
                    "PRO_MAX_RUNS"):
            monkeypatch.delenv(var, raising=False)
        service, state = build_service(llm=FakePipelineLLM(),
                                       data_dir=tmp_path)
        assert service.router.limits.max_portfolio_var_pct is None
        assert service.router.limits.max_correlated_gross_pct is None
        assert state.recorder.max_runs == 500

    def test_env_conformal_gate_reaches_pipeline_runs(self, tmp_path, monkeypatch):
        # P3-04: PRO_MAX_VOL_INTERVAL_WIDTH_PCT / PRO_VOL_INTERVAL_SIZE_SCALE
        # must land on the PIPELINE config (config.risk), not only on the
        # router limits — the audited gap was three prod ProConfig builds
        # omitting risk=, leaving the conformal gate permanently disabled.
        import tradingagents.pro.main as main_module
        from tests.test_pro_conformal import make_vol_bars
        from tests.test_pro_pipeline_graph import pipeline_snapshot
        from tradingagents.pro.ingestion import builder as builder_module

        class FakeBuilder:
            def build(self, symbol, asset, **kwargs):
                # enough vol history for a real conformal interval
                return pipeline_snapshot(bars=make_vol_bars(200))

        monkeypatch.setattr(builder_module, "build_gold_pipeline",
                            lambda *a, **k: FakeBuilder())
        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "pro.db"))
        monkeypatch.setenv("PRO_MAX_VOL_INTERVAL_WIDTH_PCT", "90")
        monkeypatch.setenv("PRO_VOL_INTERVAL_SIZE_SCALE", "1")
        service, state = main_module.build_service(llm=FakePipelineLLM(),
                                                   data_dir=tmp_path)
        # the service config AND the router limits carry the env-armed cap
        assert service.config.risk.max_vol_interval_width_pct == 90.0
        assert service.config.risk.vol_interval_size_scale is True
        assert service.router.limits.max_vol_interval_width_pct == 90.0

        # hermetic: the real calendar_fn reaches for the network-backed
        # intel service; no upcoming event = the event gate passes open
        service.pipeline_kwargs["calendar_fn"] = lambda: None
        summary = service.run_once()  # rotation path builds its own config
        assert summary["run_id"]
        run = state.latest_run()
        gate = run.state["gate_results"]["conformal_vol"]
        assert gate["checks"].get("vol_interval_available") is True
        assert gate["passed"] is True  # generous 90% cap

    def test_env_conformal_gate_unset_stays_disabled(self, tmp_path, monkeypatch):
        from tradingagents.pro.main import build_service

        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "pro.db"))
        for var in ("PRO_MAX_VOL_INTERVAL_WIDTH_PCT",
                    "PRO_VOL_INTERVAL_SIZE_SCALE"):
            monkeypatch.delenv(var, raising=False)
        service, state = build_service(llm=FakePipelineLLM(),
                                       data_dir=tmp_path)
        assert service.config.risk.max_vol_interval_width_pct is None
        assert service.config.risk.vol_interval_size_scale is False

    def test_prod_bundle_wrapped_with_cost_tracking(self, tmp_path, monkeypatch):
        # P2 observability: the env-configured bundle must emit
        # llm_calls_total / llm_est_cost_usd into the SAME registry the
        # service exposes at /metrics (state.metrics is service.metrics)
        import tradingagents.pro.models as models_module
        from tradingagents.pro.main import build_service
        from tradingagents.pro.models import ModelBundle
        from tradingagents.pro.observability import CostTrackingLLM

        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "pro.db"))
        fake = FakePipelineLLM()
        monkeypatch.setattr(models_module, "bundle_from_config",
                            lambda config, **kwargs: ModelBundle.single(fake))
        service, state = build_service(data_dir=tmp_path)
        assert isinstance(service.llm.quick, CostTrackingLLM)
        assert service.llm.quick.inner is fake
        # single-model bundle stays deduped: one wrapper serves both tiers
        assert service.llm.deep is service.llm.quick
        assert service.llm.quick.metrics is service.metrics
        assert state.metrics is service.metrics


class TestPipelineTrigger:
    """On-demand runs: routing, validation, single-flight, loop lock."""

    def _trigger(self):
        from tests.test_pro_pipeline_graph import CONFIG, pipeline_snapshot
        from tradingagents.pro.main import PipelineTrigger

        calls = {}

        class FakeService:
            config = CONFIG
            run_lock = None

            def run_once(self, snapshot=None, config=None, trigger="loop"):
                calls["config"] = config
                calls["trigger"] = trigger
                return {"symbol": snapshot.symbol, "run_id": "r1"}

        class FakeSnapshotTrigger(PipelineTrigger):
            def _build_snapshot(self, symbol, asset, tf):
                calls["build"] = (symbol, asset, tf)
                return pipeline_snapshot()

        return FakeSnapshotTrigger(FakeService()), calls

    def test_gold_intraday_routing(self):
        from tradingagents.contracts import AssetClass, Timeframe

        trigger, calls = self._trigger()
        out = trigger.run("XAUUSD", "4h")
        assert out["run_id"] == "r1"
        assert calls["build"] == ("XAUUSD", AssetClass.GOLD, Timeframe("4h"))
        assert calls["config"].asset is AssetClass.GOLD

    def test_bitcoin_routing(self):
        from tradingagents.contracts import AssetClass, Timeframe

        trigger, calls = self._trigger()
        trigger.run("BTC-USD", "1h")
        assert calls["build"] == ("BTC-USD", AssetClass.BITCOIN, Timeframe("1h"))
        assert calls["config"].asset is AssetClass.BITCOIN

    def test_fx_routing_carries_the_pair_symbol(self, monkeypatch):
        # AssetClass.FX spans EURUSD and USDJPY: the config must carry the
        # actual pair, never the class default (P2-10). Token set: intraday
        # FX is only supported when OANDA is configured.
        from tradingagents.contracts import AssetClass, Timeframe

        monkeypatch.setenv("OANDA_API_TOKEN", "practice-token")
        trigger, calls = self._trigger()
        trigger.run("EURUSD", "1d")
        assert calls["build"] == ("EURUSD", AssetClass.FX, Timeframe("1d"))
        assert calls["config"].asset is AssetClass.FX
        assert calls["config"].symbol == "EURUSD"

        trigger.run("USDJPY", "1h")
        assert calls["build"] == ("USDJPY", AssetClass.FX, Timeframe("1h"))
        assert calls["config"].asset is AssetClass.FX
        assert calls["config"].symbol == "USDJPY"

    def test_fx_intraday_unsupported_without_oanda(self, monkeypatch):
        # yfinance fallback serves daily bars only: FX intraday without an
        # OANDA token must be a typed refusal (422 at the API), not a crash
        from tradingagents.pro.main import TriggerUnsupported

        trigger, calls = self._trigger()
        monkeypatch.delenv("OANDA_API_TOKEN", raising=False)
        with pytest.raises(TriggerUnsupported, match="OANDA_API_TOKEN"):
            trigger.run("EURUSD", "4h")
        assert trigger.busy() is False  # refusal never leaves the lock held
        assert "build" not in calls  # rejected before any snapshot work
        trigger.run("EURUSD", "1d")  # daily stays runnable on the fallback
        assert calls["build"][0] == "EURUSD"

    def test_fx_intraday_supported_with_oanda(self, monkeypatch):
        trigger, calls = self._trigger()
        monkeypatch.setenv("OANDA_API_TOKEN", "practice-token")
        trigger.run("USDJPY", "1h")
        assert calls["build"][0] == "USDJPY"

    def test_rejects_unknown_symbol_and_timeframe(self):
        trigger, _ = self._trigger()
        with pytest.raises(ValueError):
            trigger.run("DOGE", "1h")
        with pytest.raises(ValueError):
            trigger.run("XAUUSD", "5m")
        assert trigger.busy() is False  # rejection never leaves the lock held

    def test_single_flight(self):
        from tradingagents.pro.main import TriggerBusy

        trigger, _ = self._trigger()
        assert trigger._busy.acquire(blocking=False)
        try:
            with pytest.raises(TriggerBusy):
                trigger.run("XAUUSD", "1h")
        finally:
            trigger._busy.release()
        trigger.run("XAUUSD", "1h")  # released → next run proceeds
        assert trigger.busy() is False

    def test_run_once_serializes_on_shared_lock(self):
        """The loop and the trigger share service.run_lock: while one run
        holds it, run_once for the other must hold it too (no interleaved
        pipeline+execution)."""
        import threading

        from tests.test_pro_pipeline_graph import (
            CONFIG,
            FakePipelineLLM,
            pipeline_snapshot,
        )
        from tradingagents.contracts import RiskLimits
        from tradingagents.pro.execution import (
            VENUES,
            AuditLog,
            CircuitBreaker,
            ExecutionRouter,
            KillSwitch,
            PaperVenueAdapter,
        )
        from tradingagents.pro.memory import ProMemory
        from tradingagents.pro.service import PaperTradingService

        limits = RiskLimits()
        router = ExecutionRouter(
            adapter=PaperVenueAdapter(VENUES["mt5"], starting_cash=100_000.0),
            limits=limits,
            kill_switch=KillSwitch(),
            breaker=CircuitBreaker(limits, equity_base=100_000.0),
            audit=AuditLog(),
        )
        lock = threading.Lock()
        service = PaperTradingService(
            FakePipelineLLM(), CONFIG, pipeline_snapshot,
            router=router, memory=ProMemory(), run_lock=lock,
        )
        held_during_run = []
        original = service._run_once

        def spy(*args, **kwargs):
            held_during_run.append(lock.locked())
            return original(*args, **kwargs)

        service._run_once = spy
        service.run_once()
        assert held_during_run == [True]
        assert lock.locked() is False
