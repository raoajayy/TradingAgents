"""P3-07 algo/version tagging: stamp every run and order with provenance.

A recorded decision (or an audit line for an order) is only reviewable if
you know exactly WHAT produced it: the code (git sha), the reasoning
templates (prompt hash), the models that did the thinking (model ids), and
the knobs they ran under (config hash). ``build_version_stamp`` returns

    {"git_sha", "prompt_hash", "model_ids", "config_hash"}

and the recorder / execution router attach it to run payloads and order
audit entries. Changing any prompt template, any model id, or any
behavior-shaping config field changes the stamp — so two runs with equal
stamps are comparable, and a drifting metric can be pinned to the exact
change that caused it.

git_sha and prompt_hash are cached per process (they cannot change without
a redeploy); config_hash is recomputed per call since configs vary per run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path

from tradingagents.contracts import ProConfig

logger = logging.getLogger(__name__)

# the pipeline's reasoning templates (tradingagents/pro/pipeline/prompts/);
# keep in sync with PipelineNodes._prompts. Sorted so the hash is stable
# regardless of load order.
_PROMPT_NAMES = ("critic", "debate", "judge", "reflection", "sentiment")

# deployment context, not algo behavior: promoting the same algo from
# paper to live (or arming the flag) must not read as an algo change —
# that is exactly the comparison the stamp exists to support.
_VOLATILE_CONFIG_FIELDS = frozenset({"mode", "live_trading_enabled"})

_git_sha_cache: str | None = None
_prompt_hash_cache: str | None = None


#: written by deploy/Dockerfile.pro at build time from the GIT_SHA build arg
BUILD_SHA_FILE = Path("/etc/tradingagents-build-sha")


def _baked_sha() -> str:
    try:
        return BUILD_SHA_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _compute_git_sha() -> str:
    # Cloud Run images have no .git directory. Two sources, and the BAKED
    # one wins: `--update-env-vars` MERGES, so a GIT_SHA left over from an
    # earlier deploy survives a new image and the order-audit stamp then
    # attributes trades to code that is not running. Observed in prod —
    # env GIT_SHA=071fd14 while the image contained later commits.
    baked = _baked_sha()
    env_sha = (os.environ.get("GIT_SHA") or "").strip()
    if baked and baked != "unknown":
        if env_sha and env_sha != baked:
            logger.warning(
                "GIT_SHA env (%s) disagrees with the image's baked build sha "
                "(%s); trusting the image. A stale env var means the deploy "
                "did not refresh it — audit provenance would be wrong.",
                env_sha, baked,
            )
        return baked
    if env_sha:
        return env_sha
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
            cwd=Path(__file__).resolve().parent,
        ).stdout.strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def git_sha() -> str:
    """Short git sha of the running code; "unknown" when undeterminable."""
    global _git_sha_cache
    if _git_sha_cache is None:
        _git_sha_cache = _compute_git_sha()
    return _git_sha_cache


def _compute_prompt_hash() -> str:
    # go through nodes.load_pipeline_prompt (module attribute, not a bound
    # import) so the hash covers exactly what the pipeline loads — and so
    # tests can monkeypatch a template and watch the hash move.
    from tradingagents.pro.pipeline import nodes

    digest = hashlib.sha256()
    for name in sorted(_PROMPT_NAMES):
        digest.update(name.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(nodes.load_pipeline_prompt(name).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def prompt_hash() -> str:
    """sha256 over the pipeline's prompt template sources (deterministic)."""
    global _prompt_hash_cache
    if _prompt_hash_cache is None:
        _prompt_hash_cache = _compute_prompt_hash()
    return _prompt_hash_cache


def config_hash(config: ProConfig) -> str:
    """sha256 of the ProConfig JSON minus volatile (deployment) fields."""
    payload = config.model_dump(mode="json")
    for name in _VOLATILE_CONFIG_FIELDS:
        payload.pop(name, None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_version_stamp(config: ProConfig) -> dict:
    """The P3-07 provenance stamp attached to runs and order audit lines."""
    # order-preserving dedupe: quick/deep defaults often coincide
    model_ids = list(dict.fromkeys(config.models.all_model_ids()))
    return {
        "git_sha": git_sha(),
        "prompt_hash": prompt_hash(),
        "model_ids": model_ids,
        "config_hash": config_hash(config),
    }


def reset_cache() -> None:
    """Test hook: forget the per-process git_sha / prompt_hash caches."""
    global _git_sha_cache, _prompt_hash_cache
    _git_sha_cache = None
    _prompt_hash_cache = None
