"""Trace isolation, failure containment and content-free OTLP contract."""

import asyncio
import json

import httpx
import pytest

from app import observability as telemetry
from app.agent.events import PlanEvent
from app.agent.orchestrator import plan_trip
from app.agent.results import PlanResult
from app.agent.schemas import Itinerary
from app.config import settings
from app.tools.maps import Place, PlacesResult
from app.tools.registry import TOOL_FUNCTIONS
from scripts.inspect_trace import summarize
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call


async def test_invalid_repair_output_is_a_distinct_failure_without_content(monkeypatch):
    traces = []

    async def capture(trace):
        traces.append(trace)

    monkeypatch.setattr(telemetry, "export_trace", capture)
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    plan.budget = 1
    llm = FakeLLM(
        [
            completion(content=plan.model_dump_json()),
            completion(content='{"SECRET": "patch"}'),
            completion(content='{"SECRET": "invalid again"}'),
        ]
    )
    result = await plan_trip("Chicago", client=llm, model="test-model")
    repair = next(s for s in traces[0].spans if s.name == "graph.repair")
    assert repair.attributes["wandergent.repair.output_accepted"] is False
    assert repair.error_type == "invalid_repair_output"
    assert result.itinerary is not None and not result.validation.ok
    assert len(llm.requests) == 3
    assert "SECRET" not in json.dumps(traces[0].payload())


async def test_recovered_format_keeps_failed_output_span_and_accepted_repair(monkeypatch):
    traces = []

    async def capture(trace):
        traces.append(trace)

    monkeypatch.setattr(telemetry, "export_trace", capture)
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    bad = plan.model_copy(deep=True)
    bad.budget = 1
    result = await plan_trip(
        "Chicago",
        client=FakeLLM(
            [
                completion(content=bad.model_dump_json()),
                completion(content="invalid"),
                completion(content=plan.model_dump_json()),
            ]
        ),
        model="test-model",
    )
    assert result.validation.ok
    outputs = [s for s in traces[0].spans if s.name == "repair.output"]
    assert [s.attributes["wandergent.repair.output_accepted"] for s in outputs] == [False, True]
    assert outputs[0].error_type == "invalid_repair_output"
    assert outputs[1].attributes["wandergent.repair.format_retry"] is True
    repair = next(s for s in traces[0].spans if s.name == "graph.repair")
    assert repair.attributes["wandergent.repair.output_accepted"] is True
    assert repair.error_type is None


async def test_real_graph_traces_nodes_llm_and_versions_without_content(monkeypatch):
    traces = []

    async def capture(trace):
        traces.append(trace)

    monkeypatch.setattr(telemetry, "export_trace", capture)
    result = await plan_trip(
        "SECRET traveller request",
        client=FakeLLM([completion(content=ITINERARY_JSON)]),
        model="test-model",
    )
    assert result.trace_id == traces[0].trace_id
    payload = traces[0].payload()
    serialized = json.dumps(payload)
    assert "SECRET" not in serialized
    assert "Chicago" not in serialized
    assert "sk-test" not in serialized
    names = [s.name for s in traces[0].spans]
    assert {
        "plan",
        "graph.gather",
        "graph.parse",
        "graph.validate",
        "graph.finish",
        "chat test-model",
        "context",
    } <= set(names)
    root = next(s for s in traces[0].spans if s.name == "plan")
    ids = {s.span_id for s in traces[0].spans}
    assert all(s.parent_id in ids for s in traces[0].spans if s is not root)
    assert len(root.attributes["wandergent.implementation.sha256"]) == 64
    assert root.attributes["wandergent.cost.status"] == "unknown"
    assert summarize(payload)["models"] == {"test-model": 1}
    assert summarize(payload)["configuration_versions"][0]["wandergent.schema.sha256"]
    assert summarize(payload)["estimated_model_cost_usd"] is None
    assert telemetry.current_trace_id() is None


async def test_parallel_runs_do_not_share_context_and_early_close_is_safe(monkeypatch):
    traces = []

    async def capture(trace):
        traces.append(trace)

    monkeypatch.setattr(telemetry, "export_trace", capture)

    @telemetry.traced_stream
    async def run():
        async def task():
            with telemetry.span("child"):
                await asyncio.sleep(0)

        await asyncio.gather(task(), task())
        yield PlanEvent(type="result", result=PlanResult())

    async def consume():
        iterator = run()
        event = await anext(iterator)
        assert telemetry.current_trace_id() is None
        await iterator.aclose()
        return event.result.trace_id

    ids = await asyncio.gather(consume(), consume())
    assert len(set(ids)) == 2
    assert {t.trace_id for t in traces} == set(ids)
    for trace in traces:
        root = next(s for s in trace.spans if s.name == "plan")
        assert all(s.parent_id == root.span_id for s in trace.spans if s.name == "child")


async def test_cached_facts_reach_plan_with_source_time_but_not_trace_content(monkeypatch):
    traces = []
    plan = Itinerary.model_validate_json(ITINERARY_JSON)
    name = plan.days[0].activities[0].location

    async def capture(trace):
        traces.append(trace)

    async def places(query, **kwargs):
        return PlacesResult(
            ok=True, query=query, places=[Place(ok=True, name=name, address="SECRET address")]
        )

    monkeypatch.setattr(telemetry, "export_trace", capture)
    monkeypatch.setitem(TOOL_FUNCTIONS, "search_places", places)
    results = []
    for _ in range(2):
        results.append(
            await plan_trip(
                "SECRET request",
                client=FakeLLM(
                    [
                        completion(
                            tool_calls=[tool_call("search_places", {"query": "SECRET query"})]
                        ),
                        completion(content=ITINERARY_JSON),
                    ]
                ),
                model="test-model",
            )
        )
    assert results[1].tool_usage.shared_cache_hits == 1
    assert results[1].activity_evidence[0].venue_verified
    assert (
        results[1].activity_evidence[0].collected_at == results[0].activity_evidence[0].collected_at
    )
    assert "tool.cache" in [s.name for s in traces[1].spans]
    assert all("SECRET" not in json.dumps(t.payload()) for t in traces)


async def test_export_rejection_and_transport_failure_are_contained(monkeypatch):
    monkeypatch.setattr(
        settings, "otel_exporter_otlp_traces_endpoint", "https://example.test/v1/traces"
    )
    real_client = httpx.AsyncClient
    requests = []

    def serve(request):
        requests.append(request)
        return httpx.Response(200, json={"partialSuccess": {"rejectedSpans": "1"}})

    monkeypatch.setattr(
        telemetry.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(serve), **kw),
    )
    trace = telemetry.RunTrace(spans=[telemetry.Span("plan", end_ns=1)])
    assert await telemetry.export_trace(trace) is False
    body = json.loads(requests[0].content)
    assert body["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["traceId"] == trace.trace_id
    assert requests[0].headers["content-type"] == "application/json"

    def fail(request):
        raise httpx.ReadTimeout("SECRET URL")

    monkeypatch.setattr(
        telemetry.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(fail), **kw),
    )
    assert await telemetry.export_trace(trace) is False


async def test_export_acceptance_and_estimated_cost(monkeypatch):
    traces = []
    export = telemetry.export_trace

    async def capture(trace):
        traces.append(trace)

    monkeypatch.setattr(telemetry, "export_trace", capture)
    monkeypatch.setattr(settings, "trace_model_prices", {"test-model": [2.0, 0.5, 8.0]})

    @telemetry.traced_stream
    async def run():
        with telemetry.span(
            "chat test-model",
            **{
                "openinference.span.kind": "LLM",
                "gen_ai.request.model": "test-model",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.cache_read.input_tokens": 40,
                "gen_ai.usage.output_tokens": 10,
            },
        ):
            pass
        yield PlanEvent(type="result", result=PlanResult())

    async for _ in run():
        pass
    root = traces[0].spans[-1]
    assert root.attributes["wandergent.cost.status"] == "estimated"
    assert root.attributes["wandergent.cost.usd"] == pytest.approx(0.00022)
    replay = summarize(traces[0].payload())
    assert replay["model_metrics"]["test-model"]["input_tokens"] == 100
    assert replay["model_metrics"]["test-model"]["cached_tokens"] == 40
    assert replay["estimated_model_cost_usd"] == pytest.approx(0.00022)
    monkeypatch.setattr(telemetry, "export_trace", export)
    monkeypatch.setattr(
        settings, "otel_exporter_otlp_traces_endpoint", "http://localhost/v1/traces"
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        telemetry.httpx,
        "AsyncClient",
        lambda **kw: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})), **kw
        ),
    )
    assert await telemetry.export_trace(traces[0]) is True


async def test_failure_trace_keeps_type_and_exports_without_message(monkeypatch):
    traces = []

    async def capture(trace):
        traces.append(trace)

    monkeypatch.setattr(telemetry, "export_trace", capture)

    @telemetry.traced_stream
    async def run():
        with telemetry.span("failed"):
            raise RuntimeError("SECRET bearer key")
        yield PlanEvent(type="stage")

    with pytest.raises(RuntimeError):
        await anext(run())
    assert "SECRET" not in json.dumps(traces[0].payload())
    assert traces[0].summary()["failures"] == {"RuntimeError": 2}


def test_span_cap_and_local_directory_boundary(tmp_path, monkeypatch):
    trace = telemetry.RunTrace()
    token = telemetry._trace.set(trace)
    try:
        for _ in range(telemetry.MAX_SPANS + 2):
            with telemetry.span("node"):
                pass
    finally:
        telemetry._trace.reset(token)
    assert len(trace.spans) == telemetry.MAX_SPANS
    assert trace.dropped == 2
    monkeypatch.setattr(settings, "trace_directory", str(tmp_path))
    telemetry.save_trace(trace)
    saved = list(tmp_path.glob("trace-*.json"))
    assert len(saved) == 1
    assert summarize(json.loads(saved[0].read_text()))["span_count"] == telemetry.MAX_SPANS
    monkeypatch.setattr(settings, "trace_directory", "../outside-workspace")
    telemetry.save_trace(trace)
