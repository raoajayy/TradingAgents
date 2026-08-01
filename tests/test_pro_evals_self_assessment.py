"""P3-08 quarterly self-assessment generator — deterministic, zero-LLM."""

from datetime import date, datetime, timedelta, timezone

import pytest

from tests.test_pro_pipeline_graph import CONFIG, FakePipelineLLM, pipeline_snapshot
from tradingagents.pro.dashboard.recorder import PipelineRecorder, RunRecord
from tradingagents.pro.evals.self_assessment import generate_self_assessment
from tradingagents.pro.execution import AuditLog
from tradingagents.pro.memory import ProMemory

TODAY = datetime.now(timezone.utc).date()
START = TODAY - timedelta(days=7)
END = TODAY + timedelta(days=1)

SECTIONS = (
    "## 1. Algorithms in operation",
    "## 2. Limits fired",
    "## 3. Kill-switch, circuit-breaker and reconciliation events",
    "## 4. Incidents",
    "## 5. Changes",
    "## 6. Open risks",
)


def _synthetic_run(started_at: datetime, versions: dict | None = None,
                   state: dict | None = None) -> RunRecord:
    return RunRecord(
        run_id=f"synthetic-{started_at.isoformat()}",
        started_at=started_at,
        symbol="XAUUSD",
        asset="XAU",
        state={**({"versions": versions} if versions else {}),
               **(state or {})},
    )


@pytest.fixture()
def scripted(tmp_path):
    """Two scripted pipeline runs + one rejected synthetic + audit lines."""
    memory = ProMemory()
    recorder = PipelineRecorder()
    for _ in range(2):
        recorder.record_run(FakePipelineLLM(), CONFIG, pipeline_snapshot(),
                            memory=memory)
    now = datetime.now(timezone.utc)
    recorder.runs.append(_synthetic_run(
        now + timedelta(minutes=1),
        versions={"git_sha": "abc1234", "prompt_hash": "p" * 64,
                  "model_ids": ["m1"], "config_hash": "c" * 64},
        state={"rejection": {"stage": "risk_gate", "reasons": ["VAR breach"]},
               "execution_status": "rejected:risk_gate"},
    ))
    audit_path = tmp_path / "audit.jsonl"
    audit = AuditLog(audit_path)
    audit.append("kill_switch", {"recommendation_id": "r1",
                                 "reason": "kill switch engaged"})
    audit.append("circuit_breaker_tripped", {"reason": "daily loss limit"})
    audit.append("reconciliation", {"in_sync": True})
    audit.append("order_result", {"status": "filled"})  # not a safety event
    return recorder, memory, audit_path


def test_sections_and_counts(scripted):
    recorder, memory, audit_path = scripted
    doc = generate_self_assessment(recorder.runs, memory, audit_path,
                                   metrics=None, start=START, end=END)
    for section in SECTIONS:
        assert section in doc
    # algorithms: 3 runs across 2 distinct stamps (2 scripted share one)
    assert "3 pipeline run(s) across 2 distinct version stamp(s)" in doc
    assert "git=abc1234" in doc
    # limits fired: the rejection by stage and the execution refusal
    assert "- risk_gate: 1" in doc
    assert "- rejected:risk_gate: 1" in doc
    # safety events, counted; the plain order_result is NOT one
    assert "- kill_switch: 1" in doc
    assert "- circuit_breaker_tripped: 1" in doc
    assert "- reconciliation: 1" in doc
    assert "order_result" not in doc
    # changes: the synthetic run's stamp differs from the scripted runs'
    assert "## 5. Changes" in doc
    assert "git " in doc and "→ abc1234" in doc


def test_incidents_from_critical_alerts():
    memory = ProMemory()
    recorder = PipelineRecorder()
    recorder.record_run(
        FakePipelineLLM(), CONFIG,
        pipeline_snapshot(missing_feeds=("news:quarantined:reuters",)),
        memory=memory,
    )
    doc = generate_self_assessment(recorder.runs, memory, None,
                                   metrics=None, start=START, end=END)
    assert "suspected prompt injection quarantined" in doc


def test_out_of_range_excluded(scripted):
    recorder, memory, audit_path = scripted
    ancient = _synthetic_run(
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        state={"rejection": {"stage": "quality_gate", "reasons": ["old"]}},
    )
    recorder.runs.insert(0, ancient)
    # an audit line outside the window (raw append; the generator does not
    # verify the hash chain, only parses ts/event)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2020-01-01T00:00:00+00:00", '
                     '"event": "kill_switch", "payload": {}}\n')
    doc = generate_self_assessment(recorder.runs, memory, audit_path,
                                   metrics=None, start=START, end=END)
    assert "quality_gate" not in doc          # old rejection excluded
    assert "3 pipeline run(s)" in doc          # old run not counted
    assert "- kill_switch: 1" in doc           # old audit line not counted


def test_empty_range_is_an_honest_no_activity_doc(tmp_path):
    doc = generate_self_assessment([], ProMemory(), tmp_path / "audit.jsonl",
                                   metrics=None,
                                   start=date(2019, 1, 1), end=date(2019, 3, 31))
    assert "No activity recorded in this period" in doc
    for section in SECTIONS:
        assert section in doc
    assert "No pipeline runs recorded in this period." in doc
    assert "does not exist" in doc  # missing audit log flagged as open risk


def test_end_before_start_rejected():
    with pytest.raises(ValueError, match="precedes"):
        generate_self_assessment([], ProMemory(), None, metrics=None,
                                 start=date(2026, 3, 31), end=date(2026, 1, 1))


def test_cli_writes_the_doc(tmp_path, monkeypatch):
    """python -m tradingagents.pro.evals --self-assessment --start --end
    reads the shared data dir and writes docs/evals/self_assessment_*.md —
    with no provider credentials required."""
    import sys

    from tradingagents.pro.evals.__main__ import main

    data = tmp_path / "data"
    memory = ProMemory()
    PipelineRecorder(store_dir=data / "runs").record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=memory)
    monkeypatch.setenv("TRADINGAGENTS_PRO_DATA", str(data))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "tradingagents.pro.evals", "--self-assessment",
        "--start", str(START), "--end", str(END)])
    assert main() == 0
    out = tmp_path / "docs" / "evals" / f"self_assessment_{START}_{END}.md"
    assert out.is_file()
    doc = out.read_text(encoding="utf-8")
    assert "1 pipeline run(s)" in doc
    for section in SECTIONS:
        assert section in doc

    # missing dates: a clean usage error, not a traceback
    monkeypatch.setattr(sys, "argv",
                        ["tradingagents.pro.evals", "--self-assessment"])
    assert main() == 2


def test_cli_reads_the_event_store_when_the_db_exists(tmp_path, monkeypatch):
    """P3-08 store read path: when the P2-01 SQLite DB exists (prod, or a
    Litestream-restored replica pointed at via TRADINGAGENTS_PRO_DB), the
    CLI reads runs + memory from IT — not the legacy file layout, which is
    absent on store-backed deployments and would yield an empty doc."""
    import sys

    from tradingagents.pro.evals.__main__ import main
    from tradingagents.pro.store import EventStore, SqliteMemoryStore

    data = tmp_path / "data"
    db_path = data / "pro.db"
    monkeypatch.setenv("TRADINGAGENTS_PRO_DATA", str(data))
    monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(db_path))
    store = EventStore(db_path)
    memory = ProMemory(store=SqliteMemoryStore(store))
    PipelineRecorder(store=store).record_run(
        FakePipelineLLM(), CONFIG, pipeline_snapshot(), memory=memory)
    store.close()
    # deliberately NO data/runs directory and NO memory.jsonl: only the
    # event store holds the run, so a populated doc proves the store path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "tradingagents.pro.evals", "--self-assessment",
        "--start", str(START), "--end", str(END)])
    assert main() == 0
    out = tmp_path / "docs" / "evals" / f"self_assessment_{START}_{END}.md"
    doc = out.read_text(encoding="utf-8")
    assert "1 pipeline run(s)" in doc
    assert "No pipeline runs recorded in this period." not in doc
    for section in SECTIONS:
        assert section in doc
