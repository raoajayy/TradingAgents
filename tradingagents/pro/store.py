"""P2-01 event store: SQLite (WAL) as the single source of truth.

One database, three append-oriented tables, three thin adapters that
slot into the existing writers' protocols so recorder/memory/prefs
switch backends without changing their public APIs:

- ``runs``    — one row per pipeline run (recorder's RunRecord JSON)
- ``memory``  — append-only MemoryRecord rows (JsonlStore protocol)
- ``kv``      — whole-document values (prefs, small state blobs)

Durability model: SQLite in WAL mode on a REAL local disk. In prod the
GCS FUSE mount at /data is fine for the old whole-file writers but is
NOT safe for SQLite locking — the DB lives on container-local disk and
Litestream replicates it to the bucket (restore on boot). Locally the
default ``<data_dir>/pro.db`` is already a real disk. JSONL remains the
EXPORT format (``python -m tradingagents.pro.store export``).

Writers stay single-process (Cloud Run --max-instances=1, now advisory:
WAL serializes writers within the process via one connection + lock,
and Litestream replication is single-writer by construction).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

from tradingagents.pro.memory.records import MemoryRecord

logger = logging.getLogger(__name__)

DB_ENV = "TRADINGAGENTS_PRO_DB"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    symbol     TEXT,
    trigger    TEXT,
    record     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_started_at ON runs (started_at);
CREATE TABLE IF NOT EXISTS memory (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    symbol     TEXT,
    created_at TEXT NOT NULL,
    ref_id     TEXT,
    record     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memory_created_at ON memory (created_at);
CREATE TABLE IF NOT EXISTS kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


def default_db_path() -> Path:
    """DB location: $TRADINGAGENTS_PRO_DB, else <data_dir>/pro.db.

    Prod MUST point this at local disk (e.g. /tmp/pro.db) — the GCS FUSE
    mount cannot hold SQLite locks; Litestream owns bucket replication.
    """
    env = os.environ.get(DB_ENV)
    if env:
        return Path(env)
    from tradingagents.pro.dashboard.prefs import default_data_dir

    return default_data_dir() / "pro.db"


class EventStore:
    """Process-wide SQLite handle: WAL, one writer connection + lock.

    SQLite serializes writes anyway; the explicit lock keeps our
    transactions (read-modify-write on kv) atomic across threads.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- runs -----------------------------------------------------------
    def upsert_run(self, run_id: str, started_at: str, symbol: str | None,
                   trigger: str | None, record_json: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO runs (run_id, started_at, symbol, trigger, record) "
                "VALUES (?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
                "record=excluded.record, started_at=excluded.started_at, "
                "symbol=excluded.symbol, trigger=excluded.trigger",
                (run_id, started_at, symbol, trigger, record_json),
            )

    def load_runs(self, limit: int | None = None) -> list[str]:
        """Newest-last record JSONs (recorder sorts again by started_at)."""
        sql = "SELECT record FROM runs ORDER BY started_at"
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        if limit is not None and limit >= 0:
            rows = rows[-limit:] if limit else []
        return [r[0] for r in rows]

    def prune_runs(self, keep: int) -> int:
        """Delete all but the newest ``keep`` runs (by started_at)."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM runs WHERE run_id NOT IN "
                "(SELECT run_id FROM runs ORDER BY started_at DESC LIMIT ?)",
                (keep,),
            )
            return cur.rowcount

    def count_runs(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    # --- memory ---------------------------------------------------------
    def append_memory(self, record: MemoryRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO memory "
                "(id, kind, symbol, created_at, ref_id, record) "
                "VALUES (?,?,?,?,?,?)",
                (record.id, record.kind.value, record.symbol,
                 record.created_at.isoformat(), record.ref_id,
                 record.model_dump_json()),
            )

    def load_memory(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT record FROM memory ORDER BY created_at, id"
            ).fetchall()
        return [r[0] for r in rows]

    # --- kv (prefs and small documents) ----------------------------------
    def put_kv(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO kv (key, value) VALUES (?,?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                (key, value),
            )

    def get_kv(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM kv WHERE key=?", (key,)
            ).fetchone()
        return row[0] if row else None


class SqliteMemoryStore:
    """Drop-in for JsonlStore: append(MemoryRecord) / load() -> records."""

    def __init__(self, store: EventStore):
        self._store = store

    def append(self, record: MemoryRecord) -> None:
        self._store.append_memory(record)

    def load(self) -> list[MemoryRecord]:
        records: list[MemoryRecord] = []
        for raw in self._store.load_memory():
            try:
                records.append(MemoryRecord.model_validate_json(raw))
            except Exception:  # noqa: BLE001 — mirror JsonlStore tolerance
                logger.warning("skipping corrupt memory row", exc_info=True)
        return records


def migrate_legacy(store: EventStore, data_dir: Path) -> dict[str, int]:
    """Idempotent one-shot import of the legacy JSON/JSONL files.

    Runs only when the corresponding table is empty, so booting a
    migrated container is a no-op and re-running never duplicates.
    Legacy files are left in place (they are now the export/backup of
    record until the operator deletes them).
    """
    imported = {"runs": 0, "memory": 0, "prefs": 0}

    if store.count_runs() == 0:
        runs_dir = data_dir / "runs"
        if runs_dir.is_dir():
            for path in sorted(runs_dir.glob("*.json")):
                try:
                    payload = json.loads(path.read_text())
                    store.upsert_run(
                        payload["run_id"], payload.get("started_at", ""),
                        payload.get("symbol"), payload.get("trigger"),
                        json.dumps(payload),
                    )
                    imported["runs"] += 1
                except Exception:  # noqa: BLE001 — corrupt file, skip like recorder
                    logger.warning("migrate: skipping %s", path, exc_info=True)

    if not store.load_memory():
        legacy = data_dir / "memory.jsonl"
        if legacy.is_file():
            for line in legacy.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    store.append_memory(MemoryRecord.model_validate_json(line))
                    imported["memory"] += 1
                except Exception:  # noqa: BLE001
                    logger.warning("migrate: skipping memory line", exc_info=True)

    if store.get_kv("dashboard_prefs") is None:
        legacy = data_dir / "dashboard_prefs.json"
        if legacy.is_file():
            try:
                json.loads(legacy.read_text())  # validate before importing
                store.put_kv("dashboard_prefs", legacy.read_text())
                imported["prefs"] = 1
            except Exception:  # noqa: BLE001
                logger.warning("migrate: prefs file corrupt; skipped",
                               exc_info=True)

    if any(imported.values()):
        logger.info("event-store migration imported %s", imported)
    return imported


def export_jsonl(store: EventStore, out_dir: Path) -> dict[str, int]:
    """JSONL export — the roadmap's 'JSONL becomes export format'."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {"runs": 0, "memory": 0}
    with (out_dir / "runs.jsonl").open("w") as fh:
        for record in store.load_runs():
            fh.write(json.dumps(json.loads(record), separators=(",", ":")) + "\n")
            counts["runs"] += 1
    with (out_dir / "memory.jsonl").open("w") as fh:
        for record in store.load_memory():
            fh.write(record + "\n")
            counts["memory"] += 1
    prefs = store.get_kv("dashboard_prefs")
    if prefs is not None:
        (out_dir / "dashboard_prefs.json").write_text(prefs)
    return counts


def main(argv: list[str] | None = None) -> int:
    """CLI: `python -m tradingagents.pro.store migrate|export [--out DIR]`."""
    import argparse

    from tradingagents.pro.dashboard.prefs import default_data_dir

    parser = argparse.ArgumentParser(prog="tradingagents.pro.store")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="import legacy JSON/JSONL into the DB")
    exp = sub.add_parser("export", help="dump the DB back to JSONL")
    exp.add_argument("--out", default=None, help="output dir (default <data>/export)")
    args = parser.parse_args(argv)

    data_dir = default_data_dir()
    store = EventStore()
    try:
        if args.cmd == "migrate":
            print(migrate_legacy(store, data_dir))
        else:
            out = Path(args.out) if args.out else data_dir / "export"
            print(export_jsonl(store, out), "->", out)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
