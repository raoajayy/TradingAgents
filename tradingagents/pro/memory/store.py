"""Append-only JSONL persistence for memory records, hash-chained.

One JSON object per line; writes are append+flush so a crash loses at most
the record being written. Mirrors the base framework's philosophy of a
human-inspectable memory file, in a machine-friendly shape.

Tamper-evidence (CI-5): every append also commits the record to a sidecar
hash chain (``<file>.hashes``): ``h_i = sha256(h_{i-1} + line_i)``. Any edit,
deletion, reordering, or truncation of past records breaks verification.
This is the track record real capital is judged on — a losing trade must not
be silently editable or droppable. ``load`` is STRICT by default: a corrupt
line or a broken chain raises rather than quietly vanishing (a dropped loss
would flatter the win rate). Lenient recovery is opt-in.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from tradingagents.pro.memory.records import MemoryRecord

logger = logging.getLogger(__name__)

GENESIS = "0" * 64


class MemoryIntegrityError(Exception):
    """A memory line is corrupt or its hash chain does not verify."""


def _digest(prev_hash: str, line: str) -> str:
    return hashlib.sha256((prev_hash + line).encode("utf-8")).hexdigest()


class JsonlStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._hash_path = self.path.with_name(self.path.name + ".hashes")
        self._reconcile_hashes()
        hashes = self._load_hashes()
        self._last_hash = hashes[-1] if hashes else GENESIS

    def _load_hashes(self) -> list[str]:
        if not self._hash_path.exists():
            return []
        with self._hash_path.open(encoding="utf-8") as handle:
            return [h.strip() for h in handle if h.strip()]

    def _reconcile_hashes(self) -> None:
        """Trust-on-first-use migration: a legacy file with data but no (or a
        short) sidecar gets its chain backfilled from current content, so the
        next append extends a valid chain instead of misaligning it. Cannot
        detect tampering that predates the sidecar — only from here forward."""
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            lines = [ln.strip() for ln in handle if ln.strip()]
        hashes = self._load_hashes()
        if len(hashes) >= len(lines):
            return  # chain already covers every record (or file is empty)
        prev = hashes[-1] if hashes else GENESIS
        backfilled = list(hashes)
        for line in lines[len(hashes):]:
            prev = _digest(prev, line)
            backfilled.append(prev)
        self._hash_path.write_text("\n".join(backfilled) + "\n", encoding="utf-8")

    def append(self, record: MemoryRecord) -> None:
        line = record.model_dump_json()
        new_hash = _digest(self._last_hash, line)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
        with self._hash_path.open("a", encoding="utf-8") as handle:
            handle.write(new_hash + "\n")
            handle.flush()
        self._last_hash = new_hash

    def load(self, strict: bool = True) -> list[MemoryRecord]:
        """Load every record. Strict (default): a corrupt line or a broken
        hash chain raises MemoryIntegrityError — a money record must fail loud,
        not disappear. ``strict=False`` is the explicit recovery path: it logs
        and skips bad lines (use only to salvage a damaged file)."""
        if not self.path.exists():
            return []
        hashes = self._load_hashes()
        have_chain = bool(hashes)
        records: list[MemoryRecord] = []
        prev_hash = GENESIS
        with self.path.open(encoding="utf-8") as handle:
            idx = 0
            for line_no, raw in enumerate(handle, 1):
                line = raw.strip()
                if not line:
                    continue
                # tamper-evidence: verify the line against the sidecar chain
                if have_chain:
                    expected = _digest(prev_hash, line)
                    on_chain = hashes[idx] if idx < len(hashes) else None
                    if on_chain != expected:
                        msg = (f"memory line {line_no} in {self.path} fails hash "
                               f"chain (tampered, reordered, or truncated)")
                        if strict:
                            raise MemoryIntegrityError(msg)
                        logger.warning("%s; skipping", msg)
                        idx += 1
                        continue
                    prev_hash = expected
                try:
                    records.append(MemoryRecord.model_validate(json.loads(line)))
                except Exception as exc:
                    msg = f"corrupt memory line {line_no} in {self.path}: {exc}"
                    if strict:
                        raise MemoryIntegrityError(msg) from exc
                    logger.warning("%s; skipping", msg)
                idx += 1
        return records

    def verify(self) -> bool:
        """True when every record line matches the sidecar hash chain."""
        try:
            self.load(strict=True)
            return True
        except MemoryIntegrityError:
            return False
