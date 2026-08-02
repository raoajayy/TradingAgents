"""P4-01 corpus tooling: the graded-outcome corpus that gates the RLVR
fine-tune. Reward is the signed R-multiple (pnl over the ticket's own
declared risk); journal closes beat retro simulation; rejected runs are
abstention examples behind a flag; grading is read-only and zero-LLM."""

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.pro_fakes import BASE_TS
from tests.test_pro_memory_facade import make_recommendation
from tradingagents.contracts import OHLCVBar, Timeframe, TradeAction
from tradingagents.pro.evals.corpus import (
    GRADED_THRESHOLD,
    build_training_corpus,
    corpus_summary,
    gate_verdict,
    write_corpus_jsonl,
)
from tradingagents.pro.memory import MemoryKind, ProMemory


def bar(i: int, low: float, high: float) -> OHLCVBar:
    mid = (low + high) / 2
    return OHLCVBar(
        timeframe=Timeframe.H1, start=BASE_TS + timedelta(hours=i),
        open=mid, high=high, low=low, close=mid, volume=1_000.0,
    )


def make_run(rec, run_id="run-1", rejection=None, symbol=None):
    return SimpleNamespace(
        run_id=run_id, recommendation=rec, rejection=rejection,
        symbol=symbol or (rec.symbol if rec is not None else "XAUUSD"),
        started_at=BASE_TS, timeframe="1h",
    )


class TestRewardMath:
    # make_recommendation: BUY, entry 2400, stop 2380, TP 2440, qty 1 —
    # declared risk = |2400 - 2380| * 1 = 20

    def test_take_profit_exit_reward_is_hand_computed_r_multiple(self):
        rec = make_recommendation()
        runs = [make_run(rec)]
        # TP touch: pnl = 2440 - 2400 = +40 → R = 40 / 20 = +2.0
        examples = build_training_corpus(
            runs, ProMemory(), lambda run: [bar(1, 2405, 2445)])
        assert len(examples) == 1
        ex = examples[0]
        assert ex["graded"] is True
        assert ex["outcome"]["exit_reason"] == "take_profit"
        assert ex["outcome"]["source"] == "retro"
        assert ex["outcome"]["pnl"] == pytest.approx(40.0)
        assert ex["reward"] == pytest.approx(2.0)
        assert ex["verdict"]["action"] == "BUY"
        assert ex["verdict"]["entry_price"] == 2400.0
        assert ex["verdict"]["stop_loss"] == 2380.0
        assert ex["verdict"]["take_profits"] == [2440.0]

    def test_stop_exit_reward_is_exactly_minus_one(self):
        rec = make_recommendation()
        runs = [make_run(rec)]
        # stop touch: pnl = 2380 - 2400 = -20 → R = -20 / 20 = -1.0
        examples = build_training_corpus(
            runs, ProMemory(), lambda run: [bar(1, 2375, 2395)])
        ex = examples[0]
        assert ex["graded"] is True
        assert ex["outcome"]["exit_reason"] == "stop"
        assert ex["reward"] == pytest.approx(-1.0)

    def test_sell_take_profit_reward(self):
        # SELL: entry 2400, stop 2420, TP 2360 → pnl = +40, risk = 20 → +2.0
        rec = make_recommendation(action=TradeAction.SELL)
        examples = build_training_corpus(
            [make_run(rec)], ProMemory(), lambda run: [bar(1, 2355, 2405)])
        ex = examples[0]
        assert ex["graded"] is True and ex["reward"] == pytest.approx(2.0)

    def test_unresolved_ticket_is_ungraded_not_guessed(self):
        rec = make_recommendation()
        examples = build_training_corpus(
            [make_run(rec)], ProMemory(), lambda run: [bar(1, 2395, 2410)])
        ex = examples[0]
        assert ex["graded"] is False
        assert ex["outcome"] is None
        assert ex["reward"] == 0.0


class TestJournalPreferred:
    def test_closed_journal_outcome_beats_retro(self):
        # the journal says the lived trade closed at +10 (partial exit,
        # slippage, whatever actually happened); the retro simulation of the
        # same bars would say +40 — the lived close must win
        memory = ProMemory()
        rec = make_recommendation()
        trade = memory.record_trade(rec)
        memory.close_trade(trade.id, pnl=10.0, write_lesson=False)
        examples = build_training_corpus(
            [make_run(rec)], memory, lambda run: [bar(1, 2405, 2445)])
        ex = examples[0]
        assert ex["outcome"]["source"] == "journal"
        assert ex["outcome"]["pnl"] == pytest.approx(10.0)
        assert ex["reward"] == pytest.approx(10.0 / 20.0)
        assert ex["graded"] is True

    def test_prior_retro_backfill_outcome_is_reused_and_tagged(self):
        # an OUTCOME written by analytics.retro.backfill_outcomes carries
        # mode=retro; the corpus reuses it (no re-simulation) and labels it
        memory = ProMemory()
        rec = make_recommendation()
        trade = memory.record_trade(rec)
        memory.close_trade(trade.id, pnl=-20.0, write_lesson=False,
                           details={"mode": "retro", "exit_reason": "stop"})
        examples = build_training_corpus(
            [make_run(rec)], memory, lambda run: [])
        ex = examples[0]
        assert ex["outcome"]["source"] == "retro_backfill"
        assert ex["reward"] == pytest.approx(-1.0)
        assert ex["graded"] is True

    def test_grading_is_read_only(self):
        # building the corpus must never write outcomes into memory —
        # that is backfill_outcomes' job, with its own idempotency rules
        memory = ProMemory()
        rec = make_recommendation()
        memory.record_trade(rec)
        build_training_corpus([make_run(rec)], memory,
                              lambda run: [bar(1, 2405, 2445)])
        assert memory.records(MemoryKind.OUTCOME) == []


class TestRejections:
    def test_rejected_runs_excluded_by_default(self):
        rejected = make_run(None, run_id="rej-1",
                            rejection={"stage": "risk_gate",
                                       "reasons": ["VAR breach"]})
        assert build_training_corpus([rejected], ProMemory(),
                                     lambda run: []) == []

    def test_rejected_runs_included_by_flag_as_abstention_examples(self):
        rejected = make_run(None, run_id="rej-1",
                            rejection={"stage": "risk_gate",
                                       "reasons": ["VAR breach"]})
        examples = build_training_corpus([rejected], ProMemory(),
                                         lambda run: [],
                                         include_rejections=True)
        assert len(examples) == 1
        ex = examples[0]
        assert ex["verdict"]["action"] == "REJECTED"
        assert ex["verdict"]["rejection"]["stage"] == "risk_gate"
        assert ex["outcome"] is None
        assert ex["reward"] == 0.0
        assert ex["graded"] is False  # abstentions never count as graded

    def test_run_with_neither_verdict_nor_rejection_is_skipped(self):
        empty = make_run(None, run_id="empty-1")
        assert build_training_corpus([empty], ProMemory(), lambda run: [],
                                     include_rejections=True) == []


class TestDedupeAndSummary:
    def test_dedupe_by_run_id(self):
        rec = make_recommendation()
        run = make_run(rec)
        examples = build_training_corpus([run, run, make_run(rec)],
                                         ProMemory(),
                                         lambda run: [bar(1, 2405, 2445)])
        assert len(examples) == 1

    def test_summary_counts_and_gate_verdict(self):
        rec_win = make_recommendation()
        rec_open = make_recommendation(symbol="BTCUSD")
        rejected = make_run(None, run_id="rej-1",
                            rejection={"stage": "risk_gate"})
        def bars_for(run):
            return [bar(1, 2405, 2445)] if run.symbol == "XAUUSD" else []
        examples = build_training_corpus(
            [make_run(rec_win, run_id="a"),
             make_run(rec_open, run_id="b"), rejected],
            ProMemory(), bars_for, include_rejections=True)
        summary = corpus_summary(examples)
        assert summary["total"] == 3
        assert summary["graded"] == 1 and summary["ungraded"] == 2
        assert summary["by_symbol"] == {"XAUUSD": 2, "BTCUSD": 1}
        assert summary["by_outcome"] == {"win": 1, "ungraded": 1,
                                         "rejected": 1}
        assert gate_verdict(summary["graded"]) == \
            "1 graded examples (threshold 200): NOT READY"
        assert gate_verdict(GRADED_THRESHOLD) == \
            "200 graded examples (threshold 200): READY"


class TestJsonl:
    def test_round_trip(self, tmp_path):
        rec = make_recommendation()
        examples = build_training_corpus(
            [make_run(rec)], ProMemory(), lambda run: [bar(1, 2405, 2445)])
        out = write_corpus_jsonl(examples, tmp_path / "sub" / "corpus.jsonl")
        assert out.is_file()
        lines = out.read_text(encoding="utf-8").splitlines()
        assert [json.loads(line) for line in lines] == examples


def test_cli_builds_corpus_from_the_event_store(tmp_path, monkeypatch, capsys):
    """python -m tradingagents.pro.evals --build-corpus reads the P2-01
    SQLite event store (like --self-assessment), writes the JSONL, and
    prints the P4-01 gate verdict — no provider credentials required."""
    import sys

    from tests.test_pro_pipeline_graph import (
        CONFIG,
        FakePipelineLLM,
        pipeline_snapshot,
    )
    from tradingagents.pro.dashboard.recorder import PipelineRecorder
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
    # deliberately NO data/runs directory: only the event store holds the
    # run, so a populated corpus proves the store read path

    monkeypatch.chdir(tmp_path)
    out = tmp_path / "corpus.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "tradingagents.pro.evals", "--build-corpus", "--out", str(out)])
    assert main() == 0
    printed = capsys.readouterr().out
    assert "graded examples (threshold 200): NOT READY" in printed
    rows = [json.loads(line)
            for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "XAUUSD"
    # the prompt context is re-rendered from the recorder state via the
    # pipeline's own helpers — evidence and debate as the model saw them
    assert row["prompt_context"].startswith("Evidence:")
    assert "Debate:" in row["prompt_context"]
    assert "(no evidence produced)" not in row["prompt_context"]
    # scripted run carries the P3-07 stamp
    assert row["versions"] and "git_sha" in row["versions"]


def test_cli_default_out_path(tmp_path, monkeypatch, capsys):
    """Without --out, the corpus lands in docs/evals/corpus_<stamp>.jsonl."""
    import sys

    from tradingagents.pro.evals.__main__ import main

    monkeypatch.setenv("TRADINGAGENTS_PRO_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "none.db"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv",
                        ["tradingagents.pro.evals", "--build-corpus"])
    assert main() == 0
    files = list((tmp_path / "docs" / "evals").glob("corpus_*.jsonl"))
    assert len(files) == 1
    assert "0 graded examples" in capsys.readouterr().out
