"""LLM provider backed by the local `claude` CLI (Claude Code print mode).

Routes pipeline calls through the operator's Claude subscription instead of
a metered API key: each call shells out to `claude -p --output-format json`
with tools and project settings disabled, so it behaves as a pure model
call. No API key env is required — auth is the CLI's own login (run
`claude /login` in a terminal if calls fail with an auth error).

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

from .base_client import BaseLLMClient

# session/relay vars inherited when running INSIDE a Claude Code session —
# they point the nested CLI at the host session's auth relay (whose token
# is not valid for standalone use) instead of the user's own login
_STRIP_ENV_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_")
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


def _clean_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k not in _STRIP_ENV and not k.startswith(_STRIP_ENV_PREFIXES)
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
            f"fences) that validates against this JSON schema:\n{schema_json}"
        )
        text = self._chat.complete(full)
        return self._schema.model_validate_json(_extract_json(text))


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
                f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:300]}"
            )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ClaudeCLIError(
                f"claude CLI returned non-JSON output: {proc.stdout[:300]}"
            ) from exc
        if payload.get("is_error"):
            raise ClaudeCLIError(
                f"claude CLI error: {str(payload.get('result'))[:300]}"
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
        timeout = float(self.kwargs.get("timeout", 180.0))
        return ClaudeCLIChat(self.model, binary, timeout=timeout)

    def validate_model(self) -> bool:
        # the CLI accepts aliases (haiku/sonnet/opus) and full model IDs and
        # errors clearly on its own for anything unknown
        return True
