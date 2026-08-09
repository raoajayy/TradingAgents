"""Observability: structured logs, metrics, and LLM cost tracking.

Dependency-free by design: JSON logs via stdlib logging, a small metrics
registry with Prometheus text exposition (scrapeable without
prometheus_client), and a cost tracker that wraps the Pro LLM interface.

Token counts are *estimates* (chars/4) because the structured-output
interface does not expose provider usage metadata; treat the cost figure
as a budget gauge, not an invoice. Wiring provider-reported usage is a
straightforward upgrade once a single provider is pinned in production.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field

from tradingagents.contracts import utc_now


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": utc_now().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            entry.update(extra)
        return json.dumps(entry, default=str)


def configure_structured_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


class MetricsRegistry:
    """Counters and gauges with Prometheus text exposition."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        rendered = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{rendered}}}"

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def counter(self, name: str, **labels: str) -> float:
        return self._counters.get(self._key(name, labels), 0.0)

    def counter_total(self, name: str) -> float:
        """Sum a counter across every label combination.

        ``llm_calls_total`` and ``llm_failures_total`` are partitioned by
        schema, so health/alerting questions ("is the provider answering at
        all?") need the family total, not one arbitrary label set.
        """
        prefix = f"{name}{{"
        with self._lock:
            return sum(value for key, value in self._counters.items()
                       if key == name or key.startswith(prefix))

    def gauge(self, name: str, **labels: str) -> float:
        return self._gauges.get(self._key(name, labels), 0.0)

    def render_prometheus(self) -> str:
        """Prometheus text exposition format (version 0.0.4).

        Zero-dep exporter (P2-08): the dashboard's open ``/metrics`` route
        serves this text for any external scraper (Managed Prometheus,
        Grafana Agent, plain Prometheus) — no google-cloud-monitoring or
        prometheus_client dependency needed. Samples are grouped per metric
        family under a ``# TYPE`` line so counters and gauges are classified
        correctly by the scraper.
        """
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)

        lines: list[str] = []

        def emit(samples: dict[str, float], kind: str) -> None:
            families: dict[str, list[str]] = {}
            for key, value in samples.items():
                family = key.split("{", 1)[0]
                families.setdefault(family, []).append(f"{key} {value}")
            for family in sorted(families):
                lines.append(f"# TYPE {family} {kind}")
                lines.extend(sorted(families[family]))

        emit(counters, "counter")
        emit(gauges, "gauge")
        return "\n".join(lines) + "\n"


@dataclass
class ModelPrice:
    input_per_mtok: float = 3.0  # USD per million tokens; override per deployment
    output_per_mtok: float = 15.0


# Approximate published list prices (USD per Mtok), for budget gauges only —
# verify against the provider's pricing page when it matters (eval finding:
# DeepSeek runs were reported at ~10x their real cost under the old
# one-size default). Unknown providers fall back to the conservative default.
PROVIDER_PRICES: dict[str, ModelPrice] = {
    "openai": ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0),
    "anthropic": ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0),
    "deepseek": ModelPrice(input_per_mtok=0.28, output_per_mtok=1.10),
    "google": ModelPrice(input_per_mtok=1.25, output_per_mtok=10.0),
    # subscription-billed via the local claude CLI: no marginal $ per token
    "claude-cli": ModelPrice(input_per_mtok=0.0, output_per_mtok=0.0),
}


def price_for(provider: str) -> ModelPrice:
    return PROVIDER_PRICES.get(provider.lower(), ModelPrice())


@dataclass
class CostReport:
    calls: int = 0
    est_input_tokens: int = 0
    est_output_tokens: int = 0
    est_cost_usd: float = 0.0
    by_schema: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "calls": self.calls,
            "est_input_tokens": self.est_input_tokens,
            "est_output_tokens": self.est_output_tokens,
            "est_cost_usd": round(self.est_cost_usd, 4),
            "by_schema": dict(self.by_schema),
        }


class _TrackedRunnable:
    def __init__(self, tracker: CostTrackingLLM, schema, inner):
        self._tracker = tracker
        self._schema = schema
        self._inner = inner

    def invoke(self, prompt: str):
        try:
            result = self._inner.invoke(prompt)
        except Exception:
            # failures must be visible: llm_calls_total only moves on
            # success, so without this a 100% structured-output failure
            # rate is indistinguishable from zero traffic on /metrics
            self._tracker._record_failure(self._schema.__name__)
            raise
        self._tracker._record(self._schema.__name__, prompt, result)
        return result


# Provider status codes that will NEVER succeed on retry: the call is
# refused for account/auth reasons, not load. Retrying one burns the whole
# budget plus its backoff sleeps on every call — in the 402 outage that was
# ~55 agents x retries x 0.5s+ per run, for a result that could not change.
_TERMINAL_STATUS_CODES = frozenset({401, 402, 403, 404})


def is_retryable_llm_error(exc: BaseException) -> bool:
    """False for provider refusals that cannot be fixed by trying again.

    Duck-typed on ``status_code`` (openai.APIStatusError, httpx.HTTPStatusError
    via ``response``) so it needs no provider SDK import and degrades to
    "retryable" for anything it cannot classify — an unknown error keeps the
    old behavior rather than being silently dropped.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status not in _TERMINAL_STATUS_CODES
    return True


def supports_streaming(llm) -> bool:
    """Can this model (through any stack of Pro wrappers) stream tokens?

    The wrappers below expose ``stream`` only when their inner does, so a
    plain ``hasattr`` walks the whole stack. Callers use this to fall back
    to the structured path instead of discovering it via AttributeError
    mid-response (which the dashboard's ask endpoint used to do).
    """
    return callable(getattr(llm, "stream", None))


class CostTrackingLLM:
    """Transparent wrapper over the Pro LLM interface; stacks under/over
    CachingLLM freely (both speak with_structured_output)."""

    def __init__(self, inner, price: ModelPrice | None = None,
                 metrics: MetricsRegistry | None = None):
        self.inner = inner
        self.price = price or ModelPrice()
        self.metrics = metrics
        self.report = CostReport()
        self._lock = threading.Lock()

    def with_structured_output(self, schema):
        return _TrackedRunnable(self, schema, self.inner.with_structured_output(schema))

    def __getattr__(self, name: str):
        # only reached for attributes this class does not define. Streaming
        # is the one that matters (the dashboard's ask endpoint); anything
        # else the inner model offers passes through the same way rather
        # than surfacing as an AttributeError from inside a response body.
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            inner = object.__getattribute__(self, "inner")
        except AttributeError:  # pre-__init__ / unpickling
            raise AttributeError(name) from None
        attr = getattr(inner, name)
        if name == "stream":
            return self._tracked_stream(attr)
        return attr

    def _tracked_stream(self, inner_stream):
        """Wrap inner.stream so token cost still lands in the report."""

        def stream(prompt, *args, **kwargs):
            text: list[str] = []
            ok = True
            try:
                for chunk in inner_stream(prompt, *args, **kwargs):
                    piece = getattr(chunk, "content", None)
                    if isinstance(piece, str):
                        text.append(piece)
                    yield chunk
            except Exception:
                ok = False
                self._record_failure("stream")
                raise
            finally:
                # cost is charged for whatever streamed, including a
                # partial that then failed — but the CALL counter stays
                # successes-only, so llm_calls_total keeps meaning what
                # the structured path makes it mean
                self._account("stream", len(str(prompt)),
                              len("".join(text)), count_call=ok)

        return stream

    def _record_failure(self, schema_name: str) -> None:
        if self.metrics is not None:
            self.metrics.inc("llm_failures_total", schema=schema_name)

    def _record(self, schema_name: str, prompt: str, result) -> None:
        output_chars = len(result.model_dump_json()) if result is not None else 0
        self._account(schema_name, len(prompt), output_chars)

    def _account(self, schema_name: str, prompt_chars: int, output_chars: int,
                 count_call: bool = True) -> None:
        input_tokens = max(1, prompt_chars // 4)
        output_tokens = max(0, output_chars // 4)
        cost = (
            input_tokens * self.price.input_per_mtok
            + output_tokens * self.price.output_per_mtok
        ) / 1_000_000
        with self._lock:
            self.report.est_input_tokens += input_tokens
            self.report.est_output_tokens += output_tokens
            self.report.est_cost_usd += cost
            if count_call:
                self.report.calls += 1
                self.report.by_schema[schema_name] = (
                    self.report.by_schema.get(schema_name, 0) + 1
                )
        if self.metrics is not None:
            if count_call:
                self.metrics.inc("llm_calls_total", schema=schema_name)
            self.metrics.set_gauge("llm_est_cost_usd", self.report.est_cost_usd)
