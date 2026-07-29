"""P2-01 event store: WAL SQLite core, adapters, migration, export."""

from __future__ import annotations

import json
import threading

import pytest

from tradingagents.pro.memory.records import MemoryKind, MemoryRecord
from tradingagents.pro.store import (
    EventStore,
    SqliteMemoryStore,
    export_jsonl,
    migrate_legacy,
)


@pytest.fixture
def store(tmp_path):
    s = EventStore(tmp_path / "pro.db")
    yield s
    s.close()


def _run(run_id: str, started_at: str, symbol: str = "XAUUSD") -> dict:
    return {"run_id": run_id, "started_at": started_at, "symbol": symbol,
            "trigger": "loop", "state": {"regime": "ranging"}}


class TestRuns:
    def test_upsert_load_ordered_and_limited(self, store):
        for i in (3, 1, 2):
            r = _run(f"r{i}", f"2026-07-0{i}T00:00:00Z")
            store.upsert_run(r["run_id"], r["started_at"], r["symbol"],
                             r["trigger"], json.dumps(r))
        loaded = [json.loads(x)["run_id"] for x in store.load_runs()]
        assert loaded == ["r1", "r2", "r3"]
        assert [json.loads(x)["run_id"] for x in store.load_runs(limit=2)] == ["r2", "r3"]

    def test_upsert_replaces_same_run_id(self, store):
        r = _run("r1", "2026-07-01T00:00:00Z")
        store.upsert_run("r1", r["started_at"], r["symbol"], "loop", json.dumps(r))
        r["trigger"] = "operator"
        store.upsert_run("r1", r["started_at"], r["symbol"], "operator", json.dumps(r))
        assert store.count_runs() == 1
        assert json.loads(store.load_runs()[0])["trigger"] == "operator"

    def test_prune_keeps_newest(self, store):
        for i in range(1, 6):
            r = _run(f"r{i}", f"2026-07-0{i}T00:00:00Z")
            store.upsert_run(r["run_id"], r["started_at"], None, None, json.dumps(r))
        deleted = store.prune_runs(keep=2)
        assert deleted == 3
        assert [json.loads(x)["run_id"] for x in store.load_runs()] == ["r4", "r5"]

    def test_wal_mode_active(self, store):
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


class TestMemoryAdapter:
    def test_round_trip_matches_jsonl_protocol(self, store):
        adapter = SqliteMemoryStore(store)
        rec = MemoryRecord(kind=MemoryKind.TRADE, text="bought gold",
                           symbol="XAUUSD", payload={"action": "BUY"})
        adapter.append(rec)
        loaded = adapter.load()
        assert loaded == [rec]

    def test_append_is_idempotent_per_id(self, store):
        adapter = SqliteMemoryStore(store)
        rec = MemoryRecord(kind=MemoryKind.OUTCOME, text="won", payload={})
        adapter.append(rec)
        adapter.append(rec)  # duplicate id ignored (append-only, replay-safe)
        assert len(adapter.load()) == 1

    def test_ordering_by_created_at(self, store):
        adapter = SqliteMemoryStore(store)
        a = MemoryRecord(kind=MemoryKind.TRADE, text="first", payload={})
        b = MemoryRecord(kind=MemoryKind.TRADE, text="second", payload={})
        adapter.append(b)
        adapter.append(a)
        texts = [r.text for r in adapter.load()]
        assert sorted(texts) == ["first", "second"]  # stable, no loss


class TestKv:
    def test_put_get_and_replace(self, store):
        assert store.get_kv("prefs") is None
        store.put_kv("prefs", '{"theme": "dark"}')
        store.put_kv("prefs", '{"theme": "light"}')
        assert json.loads(store.get_kv("prefs"))["theme"] == "light"

    def test_threaded_writers_end_consistent(self, store):
        def spam(n):
            for i in range(25):
                store.put_kv("k", f'{{"n": {n}, "i": {i}}}')

        threads = [threading.Thread(target=spam, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert json.loads(store.get_kv("k"))  # valid JSON survived


class TestMigration:
    def _legacy_tree(self, tmp_path):
        data = tmp_path / "data"
        runs = data / "runs"
        runs.mkdir(parents=True)
        for i in (1, 2):
            r = _run(f"r{i}", f"2026-07-0{i}T00:00:00Z")
            (runs / f"r{i}.json").write_text(json.dumps(r))
        (runs / "corrupt.json").write_text("{nope")
        rec = MemoryRecord(kind=MemoryKind.TRADE, text="legacy", payload={})
        (data / "memory.jsonl").write_text(rec.model_dump_json() + "\n{bad}\n")
        (data / "dashboard_prefs.json").write_text('{"prefs": {"theme": "dark"}}')
        return data, rec

    def test_imports_everything_and_skips_corrupt(self, store, tmp_path):
        data, rec = self._legacy_tree(tmp_path)
        imported = migrate_legacy(store, data)
        assert imported == {"runs": 2, "memory": 1, "prefs": 1}
        assert store.count_runs() == 2
        assert SqliteMemoryStore(store).load() == [rec]
        assert json.loads(store.get_kv("dashboard_prefs"))["prefs"]["theme"] == "dark"

    def test_idempotent_on_populated_store(self, store, tmp_path):
        data, _ = self._legacy_tree(tmp_path)
        migrate_legacy(store, data)
        again = migrate_legacy(store, data)
        assert again == {"runs": 0, "memory": 0, "prefs": 0}
        assert store.count_runs() == 2

    def test_missing_legacy_files_is_fine(self, store, tmp_path):
        assert migrate_legacy(store, tmp_path / "empty") == {
            "runs": 0, "memory": 0, "prefs": 0}


class TestExport:
    def test_export_round_trips_through_migration(self, store, tmp_path):
        data, rec = TestMigration()._legacy_tree(tmp_path)
        migrate_legacy(store, data)
        out = tmp_path / "export"
        counts = export_jsonl(store, out)
        assert counts == {"runs": 2, "memory": 1}
        # exported JSONL re-imports into a fresh store identically
        fresh_data = tmp_path / "fresh"
        fresh_data.mkdir()
        runs_dir = fresh_data / "runs"
        runs_dir.mkdir()
        for i, line in enumerate((out / "runs.jsonl").read_text().splitlines()):
            (runs_dir / f"{i}.json").write_text(line)
        (fresh_data / "memory.jsonl").write_text((out / "memory.jsonl").read_text())
        (fresh_data / "dashboard_prefs.json").write_text(
            (out / "dashboard_prefs.json").read_text())
        fresh = EventStore(tmp_path / "fresh.db")
        try:
            migrate_legacy(fresh, fresh_data)
            assert fresh.count_runs() == store.count_runs()
            assert fresh.load_memory() == store.load_memory()
            assert fresh.get_kv("dashboard_prefs") == store.get_kv("dashboard_prefs")
        finally:
            fresh.close()


class TestDbPathEnv:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        from tradingagents.pro.store import default_db_path

        monkeypatch.setenv("TRADINGAGENTS_PRO_DB", str(tmp_path / "custom.db"))
        assert default_db_path() == tmp_path / "custom.db"


class TestBackendIntegration:
    """The three writers on the sqlite backend, incl. reload parity."""

    def test_prefs_kv_round_trip_and_reload(self, store):
        from tradingagents.pro.dashboard.prefs import PrefsStore

        p1 = PrefsStore(store=store)
        p1.put_prefs({"theme": "dark"})
        p1.upsert_watchlist({"name": "core", "symbols": ["XAUUSD"]})
        p2 = PrefsStore(store=store)  # fresh instance = restart
        assert p2.get_prefs()["theme"] == "dark"
        assert p2.watchlists()[0]["name"] == "core"

    def test_prefs_corrupt_row_recovers_to_defaults(self, store):
        from tradingagents.pro.dashboard.prefs import PREFS_KV_KEY, PrefsStore

        store.put_kv(PREFS_KV_KEY, "{nope")
        assert PrefsStore(store=store).get_prefs()["theme"]  # defaults, no raise

    def test_memory_facade_reload_via_sqlite(self, store):
        from tradingagents.pro.memory.memory import ProMemory

        m1 = ProMemory(store=SqliteMemoryStore(store))
        m1.record_strategy("XAUUSD", "fade the breakout",
                           payload={"regime": "ranging"})
        m2 = ProMemory(store=SqliteMemoryStore(store))
        assert [r.text for r in m2.records()] == ["fade the breakout"]

    def test_recorder_sqlite_reload_parity_with_file_mode(self, store, tmp_path):
        import json as _json

        from tradingagents.pro.dashboard.recorder import PipelineRecorder

        raw = _run("r1", "2026-07-01T00:00:00Z")
        raw["asset"] = "gold"
        raw["node_sequence"] = ["prepare"]
        raw["node_times"] = [{"node": "prepare", "elapsed_s": 0.1}]
        raw["state"] = {"timeframe": "1d"}
        # seed both backends with the identical raw payload
        store.upsert_run("r1", raw["started_at"], raw["symbol"], "loop",
                         _json.dumps(raw))
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        (runs_dir / "r1.json").write_text(_json.dumps(raw))
        via_store = PipelineRecorder(store=store).runs
        via_files = PipelineRecorder(store_dir=runs_dir).runs
        assert len(via_store) == len(via_files) == 1
        a, b = via_store[0], via_files[0]
        assert (a.run_id, a.started_at, a.symbol, a.trigger, a.state) == (
            b.run_id, b.started_at, b.symbol, b.trigger, b.state)

    def test_recorder_rejects_both_backends(self, store, tmp_path):
        from tradingagents.pro.dashboard.recorder import PipelineRecorder

        with pytest.raises(ValueError):
            PipelineRecorder(store=store, store_dir=tmp_path)
