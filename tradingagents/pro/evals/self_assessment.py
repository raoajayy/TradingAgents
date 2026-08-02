"""P3-08 quarterly self-assessment generator (RTS-6 flavored).

``generate_self_assessment`` renders a markdown compliance document for a
date range, entirely from the system's own records — run records (P2-01
event store / recorder), the trade memory, and the hash-chained audit JSONL.
Sections mirror what an RTS-6 annual self-assessment asks an algorithmic
trading firm to evidence: which algorithms ran (P3-07 version stamps),
which limits fired, kill-switch / circuit-breaker events, incidents,
changes, and open risks.

Deterministic and zero-LLM by design: every number is a count over stored
events. Where the record is empty the document says so — an honest "no
activity" beats an invented narrative.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# audit `event` names that evidence a safety intervention; substring match
# so venue-refusal stages ("kill_switch", "circuit_breaker") and richer
# events ("circuit_breaker_tripped", "reconcile_accept_venue",
# "drift_resolved_flatten", "deadman_tripped", "emergency_flatten") all land
_SAFETY_EVENT_MARKERS = (
    "kill_switch", "circuit_breaker", "reconciliation", "reconcile",
    "drift_resolved", "emergency_flatten", "deadman",
)


def _in_range(when: datetime, start: date, end: date) -> bool:
    return start <= when.date() <= end


def _load_audit_entries(audit_path: str | Path | None,
                        start: date, end: date) -> list[dict]:
    """Audit JSONL lines whose ``ts`` falls in [start, end]; a missing or
    partially corrupt file degrades to fewer entries, never a crash."""
    if audit_path is None:
        return []
    path = Path(audit_path)
    if not path.is_file():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry["ts"])
        except (ValueError, KeyError, TypeError):
            logger.warning("skipping unparseable audit line")
            continue
        if _in_range(ts, start, end):
            entries.append(entry)
    return entries


def _stamp_key(versions: dict | None) -> str:
    if not versions:
        return "(unstamped — recorded before P3-07)"
    models = ",".join(versions.get("model_ids") or []) or "-"
    return (f"git={versions.get('git_sha', '?')} "
            f"prompts={str(versions.get('prompt_hash', '?'))[:12]} "
            f"config={str(versions.get('config_hash', '?'))[:12]} "
            f"models={models}")


def generate_self_assessment(recorder_runs, memory, audit_path,
                             metrics, start: date, end: date) -> str:
    """Render the self-assessment markdown for runs/audit in [start, end].

    ``recorder_runs``: list[RunRecord] (the recorder's runs). ``memory``:
    ProMemory (graded outcomes; currently informational). ``audit_path``:
    the audit JSONL path or None. ``metrics``: a MetricsRegistry or None
    (a live registry adds nothing a quarterly doc needs; accepted so a
    running service can pass its own). Pure function of stored events.
    """
    if end < start:
        raise ValueError(f"end {end} precedes start {start}")
    runs = [r for r in recorder_runs if _in_range(r.started_at, start, end)]
    runs.sort(key=lambda r: r.started_at)
    audit_entries = _load_audit_entries(audit_path, start, end)
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")

    lines: list[str] = [
        f"# Algorithmic trading self-assessment — {start} to {end}",
        "",
        f"Generated {generated} from the event store (runs, memory, audit "
        "chain). Deterministic; no model calls.",
        "",
    ]
    if not runs and not audit_entries:
        lines += [
            "**No activity recorded in this period** — no pipeline runs "
            "and no audit entries between the given dates. The sections "
            "below are therefore empty; this document evidences the "
            "absence of activity, not an omission.",
            "",
        ]

    # --- 1. algorithms run --------------------------------------------------
    lines += ["## 1. Algorithms in operation", ""]
    stamp_counts = Counter(_stamp_key(r.versions) for r in runs)
    if stamp_counts:
        lines.append(f"{len(runs)} pipeline run(s) across "
                     f"{len(stamp_counts)} distinct version stamp(s) "
                     "(P3-07 {git_sha, prompt_hash, model_ids, config_hash}):")
        lines.append("")
        for stamp, count in stamp_counts.most_common():
            lines.append(f"- `{stamp}` — {count} run(s)")
    else:
        lines.append("No pipeline runs recorded in this period.")
    if memory is not None:
        from tradingagents.pro.memory import MemoryKind

        outcomes = [o for o in memory.records(MemoryKind.OUTCOME)
                    if _in_range(o.created_at, start, end)]
        if outcomes:
            wins = sum(1 for o in outcomes if o.payload.get("won"))
            lines.append("")
            lines.append(f"Graded outcomes closed in the period: "
                         f"{len(outcomes)} ({wins} won).")
    lines.append("")

    # --- 2. limits fired ------------------------------------------------------
    lines += ["## 2. Limits fired", ""]
    rejection_stages = Counter(
        r.rejection.get("stage", "unknown") for r in runs if r.rejection)
    blocked = Counter(
        (r.state.get("execution_status") or "")
        for r in runs
        if (r.state.get("execution_status") or "").startswith(("rejected:",
                                                               "blocked:"))
    )
    if rejection_stages:
        lines.append("Pipeline-stage rejections (deterministic gates and "
                     "reviewers refusing a trade):")
        lines.append("")
        for stage, count in rejection_stages.most_common():
            lines.append(f"- {stage}: {count}")
        lines.append("")
    if blocked:
        lines.append("Execution-layer refusals (run execution_status):")
        lines.append("")
        for status, count in blocked.most_common():
            lines.append(f"- {status}: {count}")
        lines.append("")
    if not rejection_stages and not blocked:
        lines += ["No limit rejections recorded in this period.", ""]

    # --- 3. kill switch / circuit breaker -------------------------------------
    lines += ["## 3. Kill-switch, circuit-breaker and reconciliation events", ""]
    safety = [e for e in audit_entries
              if any(m in str(e.get("event", "")) for m in _SAFETY_EVENT_MARKERS)]
    if safety:
        safety_counts = Counter(str(e.get("event")) for e in safety)
        for event, count in safety_counts.most_common():
            lines.append(f"- {event}: {count}")
        lines.append("")
        for entry in safety:
            payload = entry.get("payload") or {}
            reason = payload.get("reason") or ""
            lines.append(f"  - {entry.get('ts')} `{entry.get('event')}`"
                         + (f" — {reason}" if reason else ""))
    else:
        lines.append("No kill-switch, circuit-breaker or reconciliation "
                     "audit entries in this period.")
    lines.append("")

    # --- 4. incidents ---------------------------------------------------------
    lines += ["## 4. Incidents", ""]
    from tradingagents.pro.dashboard.service import alert_feed

    critical = [a for a in alert_feed(runs, limit=10_000)["alerts"]
                if a["severity"] == "critical"]
    if critical:
        for alert in critical:
            lines.append(f"- {alert['time']} [{alert['run_id']}] "
                         f"{alert['text']} (x{alert['count']})")
    else:
        lines.append("No critical alerts derived from the run records in "
                     "this period.")
    lines.append("")

    # --- 5. changes -----------------------------------------------------------
    lines += ["## 5. Changes", ""]
    transitions: list[str] = []
    prev: tuple | None = None
    for run in runs:
        versions = run.versions or {}
        current = (versions.get("git_sha"), versions.get("prompt_hash"))
        if prev is not None and current != prev:
            transitions.append(
                f"- {run.started_at.isoformat()}: "
                f"git {prev[0]} → {current[0]}, "
                f"prompts {str(prev[1])[:12]} → {str(current[1])[:12]}")
        prev = current
    if transitions:
        lines.append("Version-stamp transitions observed across runs "
                     "(code and/or prompt changes deployed mid-period):")
        lines.append("")
        lines += transitions
    elif runs:
        lines.append("No git_sha/prompt_hash transitions — every run in "
                     "the period carried the same code and prompts.")
    else:
        lines.append("No runs, therefore no observable changes.")
    lines.append("")

    # --- 6. open risks ---------------------------------------------------------
    lines += ["## 6. Open risks", ""]
    risks: list[str] = []
    unstamped = sum(1 for r in runs if not r.versions)
    if unstamped:
        risks.append(f"- {unstamped} run(s) in the period carry no P3-07 "
                     "version stamp (recorded before tagging existed); "
                     "their provenance cannot be established.")
    if audit_path is not None and not Path(audit_path).is_file():
        risks.append(f"- audit log {audit_path} does not exist — safety "
                     "events in this period, if any, were not captured.")
    # audit-chain verification evidence: the service loop runs a daily
    # in-process verify() (CONTROLS §2) that maintains a
    # last_audit_verify_ts gauge and an audit_verify_failures_total
    # counter — cite the real evidence rather than assuming
    if metrics is not None:
        failures = metrics.counter("audit_verify_failures_total")
        verified_ts = metrics.gauge("last_audit_verify_ts")
        if failures:
            risks.append(
                f"- audit chain integrity verification FAILED "
                f"{int(failures)} time(s) (audit_verify_failures_total) — "
                "the hash chain did not verify; treat audit-derived "
                "evidence in this document as suspect.")
        elif verified_ts:
            verified_at = datetime.fromtimestamp(
                verified_ts, tz=timezone.utc).isoformat(timespec="seconds")
            lines.append(
                f"Audit chain integrity: last in-process verify() passed at "
                f"{verified_at} (last_audit_verify_ts gauge; "
                "audit_verify_failures_total=0).")
            lines.append("")
        else:
            risks.append(
                "- audit chain integrity has not been verified by this "
                "process yet (last_audit_verify_ts gauge unset) — the "
                "scheduled daily check has not completed a pass.")
    elif audit_path is not None and Path(audit_path).is_file():
        risks.append(
            "- audit chain verification evidence unavailable (no metrics "
            "registry attached): check last_audit_verify_ts / "
            "audit_verify_failures_total on the live /metrics endpoint.")
    if not safety and runs:
        risks.append("- no kill-switch or circuit-breaker activity was "
                     "recorded this period: confirm the kill switch was "
                     "TESTED (an untested control is an open risk, not "
                     "evidence of safety).")
    if risks:
        lines += risks
    else:
        lines.append("None identified from the event store for this period.")
    lines.append("")

    return "\n".join(lines)
