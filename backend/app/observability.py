"""Bounded, content-free traces with optional OTLP/HTTP JSON export.

Uses the OTLP wire format, not an OpenTelemetry SDK. ContextVar tokens keep
concurrent graph tasks isolated. No prompts, arguments, responses or exception
messages are retained. Export failure must never turn a valid plan into a failure.
"""

import json
import logging
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cache, wraps
from hashlib import sha256
from pathlib import Path
from time import time_ns
from uuid import uuid4

import httpx

from app.config import settings

logger = logging.getLogger(__name__)
MAX_SPANS = 512
TOOL_NAMES = {"search_places", "get_weather_forecast", "get_travel_time", "search_web"}
_trace: ContextVar["RunTrace | None"] = ContextVar("run_trace", default=None)
_parent: ContextVar[str] = ContextVar("span_parent", default="")


def fingerprint(value: str) -> str:
    return sha256(value.encode()).hexdigest()


@cache
def implementation_version() -> str:
    root = Path(__file__).resolve().parent
    return sha256(
        b"".join(
            path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes()
            for path in sorted(root.rglob("*.py"))
        )
    ).hexdigest()


@dataclass
class Span:
    name: str
    span_id: str = field(default_factory=lambda: uuid4().hex[:16])
    parent_id: str = ""
    start_ns: int = field(default_factory=time_ns)
    end_ns: int = 0
    attributes: dict = field(default_factory=dict)
    error_type: str | None = None

    def otlp(self, trace_id: str) -> dict:
        def attribute(key, value):
            kind = (
                "boolValue"
                if isinstance(value, bool)
                else (
                    "intValue"
                    if isinstance(value, int)
                    else ("doubleValue" if isinstance(value, float) else "stringValue")
                )
            )
            return {"key": key, "value": {kind: str(value) if kind == "intValue" else value}}

        return {
            "traceId": trace_id,
            "spanId": self.span_id,
            **({"parentSpanId": self.parent_id} if self.parent_id else {}),
            "name": self.name,
            "kind": 3 if self.attributes.get("openinference.span.kind") == "LLM" else 1,
            "startTimeUnixNano": str(self.start_ns),
            "endTimeUnixNano": str(self.end_ns or time_ns()),
            "attributes": [attribute(k, v) for k, v in self.attributes.items() if v is not None],
            "status": {"code": 2 if self.error_type else 1},
        }


@dataclass
class RunTrace:
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    spans: list[Span] = field(default_factory=list)
    dropped: int = 0
    route_facts: list[dict] = field(default_factory=list)

    def payload(self) -> dict:
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "wandergent"}},
                            {
                                "key": "openinference.project.name",
                                "value": {"stringValue": "wandergent"},
                            },
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "wandergent", "version": "1"},
                            "spans": [s.otlp(self.trace_id) for s in self.spans],
                        }
                    ],
                }
            ]
        }

    def summary(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "spans": len(self.spans),
            "dropped_spans": self.dropped,
            "failures": dict(Counter(s.error_type for s in self.spans if s.error_type)),
            "nodes": dict(Counter(s.name for s in self.spans if s.name.startswith("graph."))),
        }


@contextmanager
def span(name: str, **attributes):
    trace = _trace.get()
    record = Span(name, parent_id=_parent.get(), attributes=attributes)
    token = _parent.set(record.span_id)
    try:
        yield record
    except BaseException as exc:
        # Includes cancellation. Never serialize exc, its cause or traceback.
        record.error_type = type(exc).__name__
        record.attributes["error.type"] = record.error_type
        raise
    finally:
        record.end_ns = time_ns()
        _parent.reset(token)
        if trace is not None:
            if len(trace.spans) < MAX_SPANS:
                trace.spans.append(record)
            else:
                trace.dropped += 1


def current_trace_id() -> str | None:
    trace = _trace.get()
    return trace.trace_id if trace else None


def tool_name(value: str) -> str:
    return value if value in TOOL_NAMES else "unknown"


def route_facts() -> list[dict]:
    trace = _trace.get()
    return trace.route_facts if trace else []


def traced_node(function: Callable) -> Callable:
    @wraps(function)
    async def wrapped(state):
        with span(f"graph.{function.__name__}", **{"openinference.span.kind": "CHAIN"}) as record:
            result = await function(state)
            if function.__name__ == "repair":
                accepted = result.get("itinerary") is not None
                record.attributes["wandergent.repair.output_accepted"] = accepted
                if not accepted:
                    record.error_type = "invalid_repair_output"
                    record.attributes["error.type"] = record.error_type
            if result.get("report") is not None:
                codes = Counter(v.code for v in result["report"].violations)
                record.attributes.update({f"validation.{k}": v for k, v in codes.items()})
                record.attributes["validation.ok"] = result["report"].ok
            return result

    return wrapped


def traced_tool(function: Callable) -> Callable:
    @wraps(function)
    async def wrapped(call, context):
        name = tool_name(call.name)
        with span(
            f"tool.{name}",
            **{
                "openinference.span.kind": "TOOL",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": name,
            },
        ) as record:
            result = await function(call, context)
            outcome = result[0]
            record.attributes.update(
                {
                    "wandergent.tool.ok": outcome.ok,
                    "wandergent.tool.attempts": outcome.attempts,
                    "wandergent.tool.priority": "optional"
                    if outcome.arguments.get("purpose") == "optional"
                    else "required",
                }
            )
            if not outcome.ok:
                record.error_type = outcome.code or "tool_error"
                record.attributes["error.type"] = record.error_type
            return result

    return wrapped


def traced_stream(function: Callable) -> Callable:
    @wraps(function)
    async def wrapped(*args, **kwargs) -> AsyncIterator:
        trace = RunTrace()
        root = Span("plan", attributes={"openinference.span.kind": "CHAIN"})
        root.attributes["wandergent.implementation.sha256"] = implementation_version()
        try:
            async with aclosing(function(*args, **kwargs)) as iterator:
                while True:
                    trace_token = _trace.set(trace)
                    parent_token = _parent.set(root.span_id)
                    try:
                        event = await anext(iterator)
                    except StopAsyncIteration:
                        break
                    finally:
                        _parent.reset(parent_token)
                        _trace.reset(trace_token)
                    if event.type == "result" and event.result is not None:
                        result = event.result
                        result.trace_id = trace.trace_id
                        root.attributes.update(
                            {
                                "wandergent.run_id": result.run_id,
                                "gen_ai.usage.input_tokens": result.usage.prompt_tokens,
                                "gen_ai.usage.output_tokens": result.usage.completion_tokens,
                                "wandergent.tool.calls": result.tool_usage.executed_calls,
                                "wandergent.tool.cache_hits": result.tool_usage.cache_hits,
                                "wandergent.repair.count": sum(
                                    s.name == "graph.repair" for s in trace.spans
                                ),
                                "wandergent.outcome": "no_itinerary"
                                if result.itinerary is None
                                else (
                                    "infeasible"
                                    if result.validation and not result.validation.ok
                                    else "ok"
                                ),
                            }
                        )
                        if result.itinerary is None or (
                            result.validation and not result.validation.ok
                        ):
                            root.error_type = (
                                "no_itinerary" if result.itinerary is None else "hard_constraint"
                            )
                            root.attributes["error.type"] = root.error_type
                    yield event
        except BaseException as exc:
            root.error_type = type(exc).__name__
            root.attributes["error.type"] = root.error_type
            raise
        finally:
            root.end_ns = time_ns()
            llm_spans = [
                s for s in trace.spans if s.attributes.get("openinference.span.kind") == "LLM"
            ]
            cost = 0.0
            known = bool(llm_spans)
            for llm in llm_spans:
                attrs = llm.attributes
                prices = settings.trace_model_prices.get(attrs.get("gen_ai.request.model", ""))
                if not prices or len(prices) != 3 or not attrs.get("gen_ai.usage.input_tokens"):
                    known = False
                    break
                cached = attrs.get("gen_ai.usage.cache_read.input_tokens", 0)
                cost += (
                    (attrs["gen_ai.usage.input_tokens"] - cached) * prices[0]
                    + cached * prices[1]
                    + attrs.get("gen_ai.usage.output_tokens", 0) * prices[2]
                ) / 1e6
            root.attributes["wandergent.cost.status"] = "estimated" if known else "unknown"
            if known:
                root.attributes["wandergent.cost.usd"] = round(cost, 8)
            trace.spans.append(root)
            logger.info("plan_trace %s", json.dumps(trace.summary(), sort_keys=True))
            save_trace(trace)
            await export_trace(trace)

    return wrapped


def save_trace(trace: RunTrace) -> None:
    """Optional workspace-local artifacts. Stop at the cap rather than delete history."""
    if not settings.trace_directory:
        return
    try:
        workspace = Path(__file__).resolve().parents[2]
        directory = (workspace / settings.trace_directory).resolve()
        if directory == workspace or not directory.is_relative_to(workspace):
            logger.warning("trace_store invalid_directory")
            return
        directory.mkdir(parents=True, exist_ok=True)
        if len(list(directory.glob("trace-*.json"))) >= 100:
            logger.warning("trace_store capacity_reached")
            return
        target = directory / f"trace-{trace.trace_id}.json"
        # uuid-generated filenames, never derived from request data.
        target.write_text(json.dumps(trace.payload()), encoding="utf-8")
    except OSError:
        logger.warning("trace_store failed trace_id=%s", trace.trace_id)


async def export_trace(trace: RunTrace) -> bool:
    endpoint = settings.otel_exporter_otlp_traces_endpoint
    if not endpoint:
        return False
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(endpoint, json=trace.payload())
            response.raise_for_status()
            body = response.json() if response.content else {}
            if not isinstance(body, dict) or not isinstance(body.get("partialSuccess", {}), dict):
                logger.warning("trace_export invalid_response trace_id=%s", trace.trace_id)
                return False
            if body.get("partialSuccess", {}).get("rejectedSpans", "0") not in (0, "0"):
                logger.warning("trace_export rejected trace_id=%s", trace.trace_id)
                return False
        return True
    except (httpx.HTTPError, ValueError):
        logger.warning("trace_export failed trace_id=%s", trace.trace_id)
        return False
