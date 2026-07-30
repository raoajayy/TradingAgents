"""LLM provider backed by the local `claude` CLI (Claude Code print mode).

Routes pipeline calls through the operator's Claude subscription instead of
a metered API key: each call shells out to `claude -p --output-format json`
with tools and project settings disabled, so it behaves as a pure model
call. Auth is the CLI's own login (run `claude /login` in a terminal if
calls fail with an auth error) or, headless (Cloud Run), a long-lived
`CLAUDE_CODE_OAUTH_TOKEN` minted via `claude setup-token`.

The pipeline's whole LLM contract is `with_structured_output(schema)` →
`.invoke(prompt)` → a Pydantic instance (see FakePipelineLLM), so this
client implements that surface directly rather than subclassing a LangChain
chat model (whose structured-output path requires tool-calling plumbing the
CLI doesn't expose).

ponytail: a CLI spawn costs ~1-3s over a raw HTTP call; fine for pipeline
agents (minutes-long runs, parallelized via agent_workers), wrong for
anything latency-critical.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from pydantic import ValidationError

from .base_client import BaseLLMClient

# session/relay vars inherited when running INSIDE a Claude Code session —
# they point the nested CLI at the host session's auth relay (whose token
# is not valid for standalone use) instead of the user's own login
_STRIP_ENV_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_")
# ...but the operator-issued long-lived OAuth token (`claude setup-token`)
# is exactly how the nested CLI authenticates HEADLESSLY (Cloud Run has no
# interactive `claude /login`), so it must survive the prefix strip
_KEEP_ENV = {"CLAUDE_CODE_OAUTH_TOKEN"}
_STRIP_ENV = {
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_PREVIEW_CLASSIFIER_FLOOR",
}

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
# credential shapes that must never reach logs: the CLI echoes header
# values into its error text (observed: a bad token logged verbatim)
_REDACT = re.compile(r"sk-ant-[A-Za-z0-9_-]+|Bearer\s+\S+")


def _redact(text: str) -> str:
    return _REDACT.sub("[REDACTED]", text)


def _clean_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k in _KEEP_ENV
        or (k not in _STRIP_ENV and not k.startswith(_STRIP_ENV_PREFIXES))
    }


def _extract_json(text: str) -> str:
    """Best-effort: fenced block first, then the outermost {...} span."""
    fenced = _JSON_FENCE.search(text)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def _repair_json(text: str) -> str:
    """Fix the two almost-JSON habits observed from CLI models: literal
    control characters inside strings (raw newlines in a rationale) and
    trailing commas before a closer. Walks the string tracking in-string
    state so legal whitespace between tokens is untouched."""
    out: list[str] = []
    in_str = False
    escaped = False
    for ch in text:
        if in_str:
            if escaped:
                out.append(ch)
                escaped = False
            elif ch == "\\":
                out.append(ch)
                escaped = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\r":
                out.append("\\r")
            elif ord(ch) < 0x20:
                out.append(f"\\u{ord(ch):04x}")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    # second pass, same in-string tracking: drop commas whose next
    # non-whitespace char is a closer (never legal JSON outside a string)
    repaired = "".join(out)
    out = []
    in_str = False
    escaped = False
    pending_comma = False
    for ch in repaired:
        if in_str:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if pending_comma:
            if ch.isspace():
                continue
            if ch not in "}]":
                out.append(",")
            pending_comma = False
        if ch == ",":
            pending_comma = True
            continue
        if ch == '"':
            in_str = True
        out.append(ch)
    return "".join(out)


class ClaudeCLIError(RuntimeError):
    """CLI-level failure (auth, spawn, timeout, malformed output)."""


class _StructuredCLIRunnable:
    def __init__(self, chat: ClaudeCLIChat, schema):
        self._chat = chat
        self._schema = schema

    def invoke(self, prompt: str, config: Any = None, **kwargs):
        schema_json = json.dumps(self._schema.model_json_schema())
        full = (
            f"{prompt}\n\n"
            "Respond with ONLY a single JSON object (no prose, no markdown "
            "fences). String values must be single-line (escape newlines as "
            "\\n); no trailing commas. The object must validate against "
            f"this JSON schema:\n{schema_json}"
        )
        # one in-client retry: a CLI spawn is flakier than an HTTP call
        # (timeouts, almost-JSON), and several call sites (eval judge arms)
        # have no retry of their own — a 2-hour series shouldn't die on a
        # single transient miss. Callers' retry->abstain still applies.
        last_err: Exception | None = None
        for _ in range(2):
            try:
                text = self._chat.complete(full)
                blob = _extract_json(text)
                try:
                    return self._schema.model_validate_json(blob)
                except ValidationError:
                    return self._schema.model_validate_json(_repair_json(blob))
            except (ClaudeCLIError, ValidationError) as err:
                last_err = err
        raise last_err  # type: ignore[misc]


class ClaudeCLIChat:
    """Minimal pipeline-facing model object over `claude -p`."""

    def __init__(self, model: str, binary: str, timeout: float = 180.0):
        self.model = model
        self.binary = binary
        self.timeout = timeout
        # neutral cwd so print mode never picks up a project's CLAUDE.md
        self._cwd = tempfile.mkdtemp(prefix="claude-cli-llm-")

    def with_structured_output(self, schema):
        return _StructuredCLIRunnable(self, schema)

    def complete(self, prompt: str) -> str:
        cmd = [
            self.binary, "-p",
            "--model", self.model,
            "--output-format", "json",
            "--tools", "",
            "--setting-sources", "",
            "--no-session-persistence",
        ]
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=self.timeout, env=_clean_env(), cwd=self._cwd,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCLIError(
                f"claude CLI timed out after {self.timeout}s"
            ) from exc
        if proc.returncode != 0 and not proc.stdout.strip():
            raise ClaudeCLIError(
                f"claude CLI exited {proc.returncode}: "
                f"{_redact(proc.stderr.strip())[:300]}"
            )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeCLIError(
                f"claude CLI returned non-JSON output: {proc.stdout[:300]}"
            ) from exc
        if payload.get("is_error"):
            raise ClaudeCLIError(
                f"claude CLI error: {_redact(str(payload.get('result')))[:300]}"
            )
        result = payload.get("result")
        if not isinstance(result, str):
            raise ClaudeCLIError("claude CLI JSON had no string 'result'")
        return result


class ClaudeCLIClient(BaseLLMClient):
    """Factory-facing client for provider 'claude-cli'."""

    provider = "claude-cli"

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        binary = (
            os.environ.get("CLAUDE_CLI_BIN")
            or shutil.which("claude")
            or os.path.expanduser("~/.local/bin/claude")
        )
        if not os.path.exists(binary):
            raise ClaudeCLIError(
                "claude CLI not found — install Claude Code or set CLAUDE_CLI_BIN"
            )
        # floor the per-call budget: a CLI spawn adds startup + queueing on
        # top of inference, so the pipeline's 60s quick-tier timeout (sized
        # for raw HTTP) starves real completions (observed in the P1 evals)
        timeout = max(float(self.kwargs.get("timeout", 300.0)), 300.0)
        return ClaudeCLIChat(self.model, binary, timeout=timeout)

    def validate_model(self) -> bool:
        # the CLI accepts aliases (haiku/sonnet/opus) and full model IDs and
        # errors clearly on its own for anything unknown
        return True
