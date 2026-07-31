"""P3-07 algo/version tagging: runs and orders carry {git_sha, prompt_hash,
model_ids, config_hash}; any prompt/config change moves the stamp."""

import json

import pytest

from tradingagents.contracts import AssetClass, ProConfig, TradingMode
from tradingagents.pro import versioning

CONFIG = ProConfig(asset=AssetClass.GOLD, max_debate_rounds=1)

STAMP_KEYS = {"git_sha", "prompt_hash", "model_ids", "config_hash"}


@pytest.fixture(autouse=True)
def _fresh_caches():
    """Per-process caches must not leak between tests (or from other
    modules' earlier record_run calls)."""
    versioning.reset_cache()
    yield
    versioning.reset_cache()


class TestBuildVersionStamp:
    def test_stamp_shape(self):
        stamp = versioning.build_version_stamp(CONFIG)
        assert set(stamp) == STAMP_KEYS
        assert isinstance(stamp["git_sha"], str) and stamp["git_sha"]
        assert len(stamp["prompt_hash"]) == 64
        assert len(stamp["config_hash"]) == 64
        assert stamp["model_ids"] == [CONFIG.models.quick_think_llm,
                                      CONFIG.models.deep_think_llm]
        json.dumps(stamp)  # must ride along in run/audit JSON payloads

    def test_git_sha_prefers_env(self, monkeypatch):
        monkeypatch.setenv("GIT_SHA", "abc1234")
        assert versioning.git_sha() == "abc1234"

    def test_git_sha_unknown_when_git_fails(self, monkeypatch):
        monkeypatch.delenv("GIT_SHA", raising=False)
        monkeypatch.setattr(versioning.subprocess, "run",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        assert versioning.git_sha() == "unknown"

    def test_git_sha_cached_per_process(self, monkeypatch):
        monkeypatch.setenv("GIT_SHA", "abc1234")
        assert versioning.git_sha() == "abc1234"
        monkeypatch.setenv("GIT_SHA", "changed")
        assert versioning.git_sha() == "abc1234"  # cache holds

    def test_prompt_change_changes_prompt_hash(self, monkeypatch):
        from tradingagents.pro.pipeline import nodes

        baseline = versioning.prompt_hash()
        original = nodes.load_pipeline_prompt
        monkeypatch.setattr(
            nodes, "load_pipeline_prompt",
            lambda name: original(name) + ("\nBe extra careful."
                                           if name == "judge" else ""),
        )
        versioning.reset_cache()
        assert versioning.prompt_hash() != baseline

    def test_config_change_changes_config_hash(self):
        baseline = versioning.config_hash(CONFIG)
        changed = CONFIG.model_copy(update={"max_debate_rounds": 3})
        assert versioning.config_hash(changed) != baseline
        model_change = ProConfig(
            asset=AssetClass.GOLD, max_debate_rounds=1,
            models={"deep_think_llm": "gpt-5.5-2026-01-01"},
        )
        assert versioning.config_hash(model_change) != baseline

    def test_volatile_fields_do_not_change_config_hash(self):
        # promoting the same algo paper->live is not an algo change
        armed = ProConfig(asset=AssetClass.GOLD, max_debate_rounds=1,
                          mode=TradingMode.PAPER, live_trading_enabled=True)
        assert versioning.config_hash(armed) == versioning.config_hash(CONFIG)


class TestRunStamping:
    def test_recorded_run_carries_versions(self):
        from tests.test_pro_pipeline_graph import (
            CONFIG as PIPE_CONFIG,
            FakePipelineLLM,
            pipeline_snapshot,
        )
        from tradingagents.pro.dashboard.recorder import PipelineRecorder
        from tradingagents.pro.memory import ProMemory

        recorder = PipelineRecorder()
        run = recorder.record_run(FakePipelineLLM(), PIPE_CONFIG,
                                  pipeline_snapshot(), memory=ProMemory())
        assert set(run.versions) == STAMP_KEYS
        assert run.versions == versioning.build_version_stamp(PIPE_CONFIG)

    def test_api_runs_rows_carry_versions(self):
        fastapi = pytest.importorskip("fastapi")  # noqa: F841
        from fastapi.testclient import TestClient

        from tests.test_pro_pipeline_graph import (
            CONFIG as PIPE_CONFIG,
            FakePipelineLLM,
            pipeline_snapshot,
        )
        from tradingagents.pro.dashboard.app import DashboardState, create_app
        from tradingagents.pro.memory import ProMemory

        state = DashboardState(memory=ProMemory())
        state.recorder.record_run(FakePipelineLLM(), PIPE_CONFIG,
                                  pipeline_snapshot(), memory=state.memory)
        rows = TestClient(create_app(state)).get("/api/runs").json()
        assert set(rows[0]["versions"]) == STAMP_KEYS

    def test_versions_persist_and_old_runs_still_parse(self, tmp_path):
        """Round-trips to disk; a pre-stamp file (no versions key) loads
        fine with versions None — same backward compat as trigger."""
        from tests.test_pro_pipeline_graph import (
            CONFIG as PIPE_CONFIG,
            FakePipelineLLM,
            pipeline_snapshot,
        )
        from tradingagents.pro.dashboard.recorder import PipelineRecorder
        from tradingagents.pro.memory import ProMemory

        recorder = PipelineRecorder(store_dir=tmp_path)
        run = recorder.record_run(FakePipelineLLM(), PIPE_CONFIG,
                                  pipeline_snapshot(), memory=ProMemory())
        reloaded = PipelineRecorder(store_dir=tmp_path)
        assert reloaded.runs[0].versions == run.versions
        # simulate a run persisted before P3-07 existed
        path = tmp_path / f"{run.run_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        del raw["state"]["versions"]
        path.write_text(json.dumps(raw), encoding="utf-8")
        legacy = PipelineRecorder(store_dir=tmp_path).runs[0]
        assert legacy.versions is None
        assert legacy.run_id == run.run_id


class TestOrderStamping:
    def test_order_audit_entries_carry_versions(self):
        from tests.test_pro_execution_router import make_router, sized_rec

        router = make_router()
        router.versions = versioning.build_version_stamp(CONFIG)
        router.submit_recommendation(sized_rec(), equity=100_000)
        events = {e["event"]: e for e in router.audit.entries}
        assert set(events["order_received"]["payload"]["versions"]) == STAMP_KEYS
        assert set(events["order_result"]["payload"]["versions"]) == STAMP_KEYS

    def test_unstamped_router_omits_the_field(self):
        from tests.test_pro_execution_router import make_router, sized_rec

        router = make_router()  # no service wired -> versions is None
        router.submit_recommendation(sized_rec(), equity=100_000)
        for entry in router.audit.entries:
            assert "versions" not in entry["payload"]
