"""P2-01 event store: SQLite (WAL) as the single source of truth.

One database, four append-oriented tables, three thin adapters that
slot into the existing writers' protocols so recorder/memory/prefs
switch backends without changing their public APIs:

- ``runs``     — one row per pipeline run (recorder's RunRecord JSON)
- ``memory``   — append-only MemoryRecord rows (JsonlStore protocol)
- ``kv``       — whole-document values (prefs, small state blobs)
- ``users``    — P3-05 multi-tenant identities: (email PK, role, created_at)
  with role ∈ {viewer, operator}; additive CREATE TABLE IF NOT EXISTS
- ``api_tokens`` — P3-11 public read-only API tokens: (token_hash PK
  sha256-hex, label, scopes csv, created_at, revoked_at nullable,
  expires_at nullable). Only the hash is ever stored — the raw token is
  returned ONCE at creation; additive CREATE TABLE IF NOT EXISTS, and
  ``expires_at`` is retrofitted onto pre-existing databases via a
  guarded ALTER TABLE on open
- ``listings`` — P4-03 marketplace listings (strategy configs / prompt
  bundles) with the published graded record in ``calibration_json``;
  status ∈ {draft, published, delisted}; additive CREATE TABLE IF NOT
  EXISTS
- ``vintages`` — P3-02 point-in-time metric observations
  (name, value, observed_at, as_of, source); additive CREATE TABLE IF NOT
  EXISTS, so existing P2-01 databases upgrade in place on open

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
from datetime import datetime, timezone
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
CREATE TABLE IF NOT EXISTS users (
    email      TEXT PRIMARY KEY,
    role       TEXT NOT NULL CHECK (role IN ('viewer','operator')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS api_tokens (
    token_hash TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    scopes     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    revoked_at TEXT,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS listings (
    id               TEXT PRIMARY KEY,
    owner_email      TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('strategy','prompt')),
    title            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    config_json      TEXT NOT NULL,
    calibration_json TEXT,
    status           TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','published','delisted')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS listings_status ON listings (status);
CREATE TABLE IF NOT EXISTS vintages (
    name        TEXT NOT NULL,
    value       REAL NOT NULL,
    observed_at TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    source      TEXT,
    PRIMARY KEY (name, as_of, observed_at)
);
CREATE INDEX IF NOT EXISTS vintages_name_observed
    ON vintages (name, observed_at);
"""


def _iso_utc(value: datetime | str) -> str:
    """Normalize timestamps to sortable UTC ISO-8601 text.

    The vintages table compares timestamps lexicographically, so every
    write and read must funnel through one canonical rendering. Date-only
    strings (FRED observation dates) become midnight UTC.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


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
            # additive column upgrades for pre-existing databases: CREATE
            # TABLE IF NOT EXISTS never touches an existing table, so new
            # columns need a guarded ALTER (SQLite has no ADD COLUMN IF
            # NOT EXISTS — a duplicate column raises OperationalError,
            # which is exactly the "already upgraded" signal)
            import contextlib

            with contextlib.suppress(sqlite3.OperationalError):
                # duplicate column = already upgraded (or fresh schema)
                self._conn.execute(
                    "ALTER TABLE api_tokens ADD COLUMN expires_at TEXT")
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
        """Newest-last record JSONs (recorder sorts again by started_at).

        The LIMIT is applied by SQLite, not in Python. Fetching every row
        and slicing afterwards defeated the point of ``max_runs``: the boot
        path materialised the WHOLE runs table (~100 KB of JSON per run)
        just to keep the newest few hundred, and that transient peak — on
        top of the models being built from it — is the moment a 1 GiB
        container is most likely to be OOM-killed.
        """
        if limit is not None and limit >= 0:
            if limit == 0:
                return []
            # DESC + LIMIT so SQLite stops after `limit` rows, then flip
            # back to newest-last for the caller's contract
            sql = "SELECT record FROM runs ORDER BY started_at DESC LIMIT ?"
            with self._lock:
                rows = self._conn.execute(sql, (limit,)).fetchall()
            return [r[0] for r in reversed(rows)]
        with self._lock:
            rows = self._conn.execute(
                "SELECT record FROM runs ORDER BY started_at").fetchall()
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

    # --- users (P3-05 multi-tenant roles) ---------------------------------
    USER_ROLES = ("viewer", "operator")

    def put_user(self, email: str, role: str) -> dict:
        """Upsert one user. Emails are folded to lowercase (the same
        canonicalization the allowlist and Google claims use); role must
        be one of USER_ROLES (mirrors the table's CHECK constraint so the
        caller gets a ValueError, not an sqlite3.IntegrityError)."""
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError(f"invalid email {email!r}")
        if role not in self.USER_ROLES:
            raise ValueError(
                f"invalid role {role!r}; use one of {list(self.USER_ROLES)}")
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO users (email, role) VALUES (?,?) "
                "ON CONFLICT(email) DO UPDATE SET role=excluded.role",
                (email, role),
            )
            row = self._conn.execute(
                "SELECT email, role, created_at FROM users WHERE email=?",
                (email,),
            ).fetchone()
        return {"email": row[0], "role": row[1], "created_at": row[2]}

    def get_user_role(self, email: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT role FROM users WHERE email=?",
                (email.strip().lower(),),
            ).fetchone()
        return row[0] if row else None

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT email, role, created_at FROM users ORDER BY email"
            ).fetchall()
        return [{"email": r[0], "role": r[1], "created_at": r[2]}
                for r in rows]

    def count_users(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM users").fetchone()[0]

    # --- api tokens (P3-11 public read-only API) ---------------------------
    # Scopes are a comma-separated allowlist; the raw token is generated
    # here, hashed with sha256, and returned exactly once — the table never
    # sees it again (a leaked DB leaks no usable credentials).
    PUBLIC_API_SCOPES = ("read:decisions", "read:calibration")

    @staticmethod
    def _hash_token(raw_token: str) -> str:
        import hashlib

        return hashlib.sha256(raw_token.encode()).hexdigest()

    def create_api_token(self, label: str, scopes,
                         expires_days: float | int | None = None) -> dict:
        """Mint one public-API token. ``scopes`` is an iterable (or csv
        string) drawn from PUBLIC_API_SCOPES; ``expires_days`` (optional,
        > 0) sets an ``expires_at`` timestamp after which the token is
        rejected — None keeps the pre-expiry behavior (lives until
        revoked). Returns the record INCLUDING the raw ``token`` — the
        only time it is ever visible."""
        import secrets as _secrets

        label = (label or "").strip()
        if not label:
            raise ValueError("label is required")
        if isinstance(scopes, str):
            scopes = [s for s in scopes.split(",") if s.strip()]
        cleaned = sorted({s.strip() for s in scopes if s.strip()})
        invalid = [s for s in cleaned if s not in self.PUBLIC_API_SCOPES]
        if not cleaned or invalid:
            raise ValueError(
                f"scopes must be a non-empty subset of "
                f"{list(self.PUBLIC_API_SCOPES)}; got {invalid or cleaned}")
        expires_at = None
        if expires_days is not None:
            try:
                days = float(expires_days)
            except (TypeError, ValueError):
                raise ValueError(
                    f"expires_days must be a positive number; "
                    f"got {expires_days!r}") from None
            if days <= 0:
                raise ValueError(
                    f"expires_days must be a positive number; got {days}")
            from datetime import timedelta

            expires_at = _iso_utc(datetime.now(timezone.utc)
                                  + timedelta(days=days))
        raw = _secrets.token_urlsafe(32)
        token_hash = self._hash_token(raw)
        scopes_csv = ",".join(cleaned)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO api_tokens (token_hash, label, scopes, "
                "expires_at) VALUES (?,?,?,?)",
                (token_hash, label, scopes_csv, expires_at),
            )
            row = self._conn.execute(
                "SELECT created_at FROM api_tokens WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
        return {"token": raw, "token_hash": token_hash, "label": label,
                "scopes": cleaned, "created_at": row[0],
                "expires_at": expires_at}

    def list_api_tokens(self) -> list[dict]:
        """Every token's metadata (hash, never the raw token)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT token_hash, label, scopes, created_at, revoked_at, "
                "expires_at FROM api_tokens ORDER BY created_at, token_hash"
            ).fetchall()
        return [{"token_hash": r[0], "label": r[1],
                 "scopes": [s for s in r[2].split(",") if s],
                 "created_at": r[3], "revoked_at": r[4],
                 "expires_at": r[5]} for r in rows]

    def revoke_api_token(self, token_hash: str) -> bool:
        """Revoke by hash (idempotent once revoked). False = unknown hash."""
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM api_tokens WHERE token_hash=?", (token_hash,)
            ).fetchone()
            if exists is None:
                return False
            self._conn.execute(
                "UPDATE api_tokens SET "
                "revoked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE token_hash=? AND revoked_at IS NULL",
                (token_hash,),
            )
        return True

    def resolve_api_token(self, raw_token: str) -> dict | None:
        """The live (non-revoked) token record matching a presented raw
        token, or None — the public API's auth check. An EXPIRED token is
        rejected too, but distinguishably: the returned record carries
        ``expired: True`` and an empty scope list (so a caller that
        forgets to check still cannot pass any scope gate), letting the
        API answer 401 "token expired" instead of "invalid"."""
        with self._lock:
            row = self._conn.execute(
                "SELECT token_hash, label, scopes, expires_at FROM api_tokens "
                "WHERE token_hash=? AND revoked_at IS NULL",
                (self._hash_token(raw_token),),
            ).fetchone()
        if row is None:
            return None
        expires_at = row[3]
        if expires_at is not None:
            try:
                expired = (datetime.fromisoformat(expires_at)
                           <= datetime.now(timezone.utc))
            except ValueError:
                expired = True  # unparseable expiry: fail closed
            if expired:
                return {"token_hash": row[0], "label": row[1],
                        "scopes": [], "expires_at": expires_at,
                        "expired": True}
        return {"token_hash": row[0], "label": row[1],
                "scopes": [s for s in row[2].split(",") if s],
                "expires_at": expires_at}

    # --- listings (P4-03 calibration-gated marketplace) --------------------
    # config_json is the paid artifact (the strategy config or prompt
    # bundle) and NEVER leaves the operator surface; calibration_json is
    # the published graded record (n_graded, win_rate, avg_r, brier,
    # corpus/versions stamp) that the publish gate in
    # dashboard.service.listing_gate validates. Status transitions are the
    # app's job — the store only enforces the vocabulary.
    LISTING_KINDS = ("strategy", "prompt")
    LISTING_STATUSES = ("draft", "published", "delisted")

    _LISTING_COLUMNS = ("id, owner_email, kind, title, description, "
                        "config_json, calibration_json, status, "
                        "created_at, updated_at")

    @staticmethod
    def _listing_row(row: tuple) -> dict:
        return {
            "id": row[0],
            "owner_email": row[1],
            "kind": row[2],
            "title": row[3],
            "description": row[4],
            "config": json.loads(row[5]),
            "calibration": json.loads(row[6]) if row[6] is not None else None,
            "status": row[7],
            "created_at": row[8],
            "updated_at": row[9],
        }

    def create_listing(self, owner_email: str, kind: str, title: str,
                       description: str = "", config: dict | None = None,
                       calibration: dict | None = None) -> dict:
        """Insert one draft listing; returns the stored row. Validation
        mirrors the CHECK constraints so callers get ValueError, not
        sqlite3.IntegrityError."""
        import uuid as _uuid

        owner_email = (owner_email or "").strip().lower()
        if not owner_email:
            raise ValueError("owner_email is required")
        if kind not in self.LISTING_KINDS:
            raise ValueError(
                f"invalid kind {kind!r}; use one of {list(self.LISTING_KINDS)}")
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        if calibration is not None and not isinstance(calibration, dict):
            raise ValueError("calibration must be an object or null")
        listing_id = _uuid.uuid4().hex
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO listings (id, owner_email, kind, title, "
                "description, config_json, calibration_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (listing_id, owner_email, kind, title, description or "",
                 json.dumps(config or {}),
                 json.dumps(calibration) if calibration is not None else None),
            )
            row = self._conn.execute(
                f"SELECT {self._LISTING_COLUMNS} FROM listings WHERE id=?",
                (listing_id,),
            ).fetchone()
        return self._listing_row(row)

    def get_listing(self, listing_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self._LISTING_COLUMNS} FROM listings WHERE id=?",
                (listing_id,),
            ).fetchone()
        return self._listing_row(row) if row else None

    def list_listings(self, status: str | None = None) -> list[dict]:
        sql = f"SELECT {self._LISTING_COLUMNS} FROM listings"
        args: tuple = ()
        if status is not None:
            if status not in self.LISTING_STATUSES:
                raise ValueError(
                    f"invalid status {status!r}; use one of "
                    f"{list(self.LISTING_STATUSES)}")
            sql += " WHERE status=?"
            args = (status,)
        sql += " ORDER BY created_at, id"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [self._listing_row(r) for r in rows]

    def update_listing(self, listing_id: str, *, title: str | None = None,
                       description: str | None = None,
                       config: dict | None = None,
                       calibration: dict | None = None) -> dict | None:
        """Patch the mutable fields (None = leave unchanged); returns the
        updated row or None for an unknown id. Changing ``config`` or
        ``calibration`` on a PUBLISHED listing demotes it back to draft —
        the published graded record and artifact must be exactly what the
        publish gate approved, so any edit re-enters the gate."""
        sets, args = ["updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')"], []
        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError("title cannot be blank")
            sets.append("title=?")
            args.append(title)
        if description is not None:
            sets.append("description=?")
            args.append(description)
        if config is not None:
            if not isinstance(config, dict):
                raise ValueError("config must be an object")
            sets.append("config_json=?")
            args.append(json.dumps(config))
        if calibration is not None:
            if not isinstance(calibration, dict):
                raise ValueError("calibration must be an object")
            sets.append("calibration_json=?")
            args.append(json.dumps(calibration))
        if config is not None or calibration is not None:
            sets.append("status=CASE WHEN status='published' "
                        "THEN 'draft' ELSE status END")
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"UPDATE listings SET {', '.join(sets)} WHERE id=?",
                (*args, listing_id),
            )
            if cur.rowcount == 0:
                return None
            row = self._conn.execute(
                f"SELECT {self._LISTING_COLUMNS} FROM listings WHERE id=?",
                (listing_id,),
            ).fetchone()
        return self._listing_row(row)

    def set_listing_status(self, listing_id: str, status: str) -> dict | None:
        """Move a listing to ``status`` (the caller — the app's publish
        gate — owns the transition rules). None for an unknown id."""
        if status not in self.LISTING_STATUSES:
            raise ValueError(
                f"invalid status {status!r}; use one of "
                f"{list(self.LISTING_STATUSES)}")
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE listings SET status=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (status, listing_id),
            )
            if cur.rowcount == 0:
                return None
            row = self._conn.execute(
                f"SELECT {self._LISTING_COLUMNS} FROM listings WHERE id=?",
                (listing_id,),
            ).fetchone()
        return self._listing_row(row)

    def delete_listing(self, listing_id: str) -> bool:
        """Hard delete (drafts/mistakes). The app's DELETE endpoint
        delists instead — once published, the record soft-retires."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "DELETE FROM listings WHERE id=?", (listing_id,))
            return cur.rowcount > 0

    # --- vintages (P3-02 point-in-time metric history) --------------------
    # Every metric observation is appended as (name, value, observed_at,
    # as_of): as_of is the period the value describes (the FRED observation
    # date), observed_at is when the world learned that value (ALFRED's
    # realtime_start). Revisions append new rows — nothing is ever
    # overwritten, so a past decision's recorded inputs stay reproducible.

    def record_vintage(
        self,
        name: str,
        value: float,
        observed_at: datetime | str,
        as_of: datetime | str,
        source: str | None = None,
    ) -> None:
        """Append one vintage; idempotent on (name, as_of, observed_at)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO vintages "
                "(name, value, observed_at, as_of, source) VALUES (?,?,?,?,?)",
                (name, float(value), _iso_utc(observed_at), _iso_utc(as_of),
                 source),
            )

    def latest_as_known(
        self, name: str, at: datetime | str
    ) -> dict | None:
        """Newest observation of ``name`` knowable at ``at``.

        "Newest" is by as_of (latest period), then observed_at (latest
        revision of that period), among rows with observed_at <= at — the
        value a decision made at ``at`` would have seen. None when nothing
        had been observed yet.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT name, value, observed_at, as_of, source FROM vintages "
                "WHERE name=? AND observed_at<=? "
                "ORDER BY as_of DESC, observed_at DESC LIMIT 1",
                (name, _iso_utc(at)),
            ).fetchone()
        return self._vintage_row(row) if row else None

    def has_vintages(self, name: str) -> bool:
        """True when ``name`` is tracked in the vintage store at all —
        lets readers distinguish "not yet knowable" from "never vintaged"."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM vintages WHERE name=? LIMIT 1", (name,)
            ).fetchone()
        return row is not None

    def load_vintages(self, name: str | None = None) -> list[dict]:
        """Full vintage history (oldest first), optionally for one metric."""
        sql = ("SELECT name, value, observed_at, as_of, source FROM vintages "
               "{} ORDER BY name, as_of, observed_at")
        args: tuple = ()
        where = ""
        if name is not None:
            where, args = "WHERE name=?", (name,)
        with self._lock:
            rows = self._conn.execute(sql.format(where), args).fetchall()
        return [self._vintage_row(r) for r in rows]

    @staticmethod
    def _vintage_row(row: tuple) -> dict:
        return {
            "name": row[0],
            "value": row[1],
            "observed_at": datetime.fromisoformat(row[2]),
            "as_of": datetime.fromisoformat(row[3]),
            "source": row[4],
        }


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


def seed_users(store: EventStore, emails: list[str] | set[str]) -> int:
    """P3-05 boot seed, mirroring migrate_legacy's guard: ONLY when the
    users table is empty, every PRO_ALLOWED_EMAILS entry becomes an
    operator (the pre-roles world was single-operator, so existing
    allowlisted accounts must not lose capabilities on upgrade).
    Idempotent: once any user row exists the table is the source of
    truth and re-running is a no-op. Returns rows inserted."""
    if store.count_users() != 0:
        return 0
    seeded = 0
    for email in sorted({e.strip().lower() for e in emails if e.strip()}):
        try:
            store.put_user(email, "operator")
            seeded += 1
        except ValueError:  # malformed allowlist entry — skip, don't boot-fail
            logger.warning("seed_users: skipping invalid email %r", email)
    if seeded:
        logger.info("seeded %d allowlisted user(s) as operator", seeded)
    return seeded


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
