"""claude-cli provider: structured-output contract over a stubbed binary.

The stub is a shell script standing in for `claude -p`; no network, no
real CLI. Covers the whole pipeline-facing surface: schema round-trip,
fenced/prose JSON extraction, CLI error propagation, and env hygiene
(the nested-session vars that break auth must not reach the child).
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from pydantic import BaseModel

from tradingagents.llm_clients.claude_cli_client import (
    ClaudeCLIChat,
    ClaudeCLIClient,
    ClaudeCLIError,
    _clean_env,
    _extract_json,
)
from tradingagents.llm_clients.factory import create_llm_client


class Draft(BaseModel):
    verdict: str
    confidence: int


def _stub(tmp_path: Path, body: str) -> str:
    """Write an executable fake `claude` and return its path."""
    path = tmp_path / "claude-stub"
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _ok_stub(tmp_path: Path, result: str) -> str:
    payload = json.dumps({"is_error": False, "result": result})
    return _stub(tmp_path, f"cat > /dev/null\nprintf '%s' '{payload}'\n")


def test_structured_output_round_trip(tmp_path):
    binary = _ok_stub(tmp_path, '{"verdict": "BUY", "confidence": 72}')
    chat = ClaudeCLIChat("haiku", binary, timeout=10)
    draft = chat.with_structured_output(Draft).invoke("analyze this")
    assert draft == Draft(verdict="BUY", confidence=72)


def test_fenced_json_is_extracted(tmp_path):
    binary = _ok_stub(
        tmp_path, 'Here you go: ```json\n{"verdict": "SELL", "confidence": 55}\n```'
    )
    chat = ClaudeCLIChat("haiku", binary, timeout=10)
    draft = chat.with_structured_output(Draft).invoke("analyze")
    assert draft.verdict == "SELL"


def test_cli_error_raises(tmp_path):
    payload = json.dumps({"is_error": True, "result": "Not logged in"})
    binary = _stub(tmp_path, f"cat > /dev/null\nprintf '%s' '{payload}'\n")
    chat = ClaudeCLIChat("haiku", binary, timeout=10)
    with pytest.raises(ClaudeCLIError, match="Not logged in"):
        chat.with_structured_output(Draft).invoke("analyze")


def test_nonzero_exit_without_output_raises(tmp_path):
    binary = _stub(tmp_path, "cat > /dev/null\necho boom >&2\nexit 3\n")
    chat = ClaudeCLIChat("haiku", binary, timeout=10)
    with pytest.raises(ClaudeCLIError, match="exited 3"):
        chat.complete("hi")


def test_extract_json_prose_fallback():
    text = 'Sure! {"verdict": "HOLD", "confidence": 40} — hope that helps.'
    assert json.loads(_extract_json(text))["verdict"] == "HOLD"


def test_repair_trailing_comma(tmp_path):
    # observed live: haiku emits {..., } — strict JSON parsing rejects it
    binary = _ok_stub(tmp_path, '{"verdict": "HOLD", "confidence": 40, }')
    chat = ClaudeCLIChat("haiku", binary, timeout=10)
    assert chat.with_structured_output(Draft).invoke("x").verdict == "HOLD"


def test_repair_control_chars_in_strings(tmp_path):
    # observed live: raw newline inside a string value
    binary = _ok_stub(
        tmp_path, '{"verdict": "BUY\ncontinued", "confidence": 70}'
    )
    chat = ClaudeCLIChat("haiku", binary, timeout=10)
    draft = chat.with_structured_output(Draft).invoke("x")
    assert draft.verdict == "BUY\ncontinued"


def test_repair_leaves_string_contents_alone():
    from tradingagents.llm_clients.claude_cli_client import _repair_json

    # a comma-before-brace INSIDE a string must survive the repair
    src = '{"verdict": "watch, }", "confidence": 1, }'
    fixed = json.loads(_repair_json(src))
    assert fixed["verdict"] == "watch, }"
    assert fixed["confidence"] == 1


def test_structured_retries_once_then_raises(tmp_path):
    # stub fails on first call, succeeds on second (state via marker file)
    marker = tmp_path / "called"
    body = (
        f"cat > /dev/null\nif [ -f {marker} ]; then "
        "printf '%s' '{\"is_error\": false, \"result\": "
        "\"{\\\"verdict\\\": \\\"BUY\\\", \\\"confidence\\\": 70}\"}'; "
        f"else touch {marker}; "
        "printf '%s' '{\"is_error\": true, \"result\": \"overloaded\"}'; fi\n"
    )
    binary = _stub(tmp_path, body)
    chat = ClaudeCLIChat("haiku", binary, timeout=10)
    draft = chat.with_structured_output(Draft).invoke("x")
    assert draft.verdict == "BUY"


def test_timeout_floor_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_BIN", _ok_stub(tmp_path, "{}"))
    llm = ClaudeCLIClient("haiku", timeout=60).get_llm()
    assert llm.timeout == 300.0


def test_clean_env_strips_session_vars(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://relay")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("SOME_OTHER_VAR", "keep")
    env = _clean_env()
    assert "ANTHROPIC_BASE_URL" not in env
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert "CLAUDECODE" not in env
    assert env["SOME_OTHER_VAR"] == "keep"


def test_factory_and_get_llm(tmp_path, monkeypatch):
    client = create_llm_client("claude-cli", "haiku")
    assert isinstance(client, ClaudeCLIClient)
    monkeypatch.setenv("CLAUDE_CLI_BIN", _ok_stub(tmp_path, "{}"))
    llm = client.get_llm()
    assert isinstance(llm, ClaudeCLIChat)
    assert llm.model == "haiku"


def test_missing_binary_raises(monkeypatch):
    monkeypatch.setenv("CLAUDE_CLI_BIN", "/nonexistent/claude")
    with pytest.raises(ClaudeCLIError, match="not found"):
        ClaudeCLIClient("haiku").get_llm()


def test_prompt_reaches_cli_via_stdin(tmp_path):
    # stub echoes stdin back inside the result so we can assert pass-through
    body = (
        "IN=$(cat)\n"
        'printf \'{"is_error": false, "result": "%s"}\' "$(printf \'%s\' "$IN" | head -c 20)"\n'
    )
    binary = _stub(tmp_path, body)
    chat = ClaudeCLIChat("haiku", binary, timeout=10)
    assert chat.complete("hello world prompt xx").startswith("hello world")
