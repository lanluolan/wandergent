import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import anyio
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from app.agent import orchestrator
from app.mcp_server import server
from app.tools import mcp_client, registry
from app.tools.base import BAD_REQUEST, NOT_CONFIGURED, TIMED_OUT, UNAVAILABLE, ToolOutcome
from app.tools.maps import PlacesResult, TravelTime
from app.tools.weather import WeatherForecast
from app.tools.web_search import WebResearch
from tests.fakes import FakeLLM, completion, tool_call


@pytest.fixture
def transport(request, monkeypatch):
    if request.param == "memory":

        @asynccontextmanager
        async def memory_transport(parameters):
            async with create_client_server_memory_streams() as (client_streams, server_streams):
                async with anyio.create_task_group() as group:
                    lowlevel = server._lowlevel_server
                    group.start_soon(
                        lowlevel.run, *server_streams, lowlevel.create_initialization_options()
                    )
                    try:
                        yield client_streams
                    finally:
                        group.cancel_scope.cancel()

        monkeypatch.setattr(mcp_client, "stdio_client", memory_transport)


@pytest.mark.parametrize("transport", ["stdio", "memory"], indirect=True)
async def test_real_protocol_discovery_concurrent_calls_and_session_reuse(transport):
    async with mcp_client.research_session() as session:
        assert isinstance(session, ClientSession)
        listed = await session.list_tools()
        assert {tool.name for tool in listed.tools} == {
            "get_weather_forecast",
            "search_places",
            "get_travel_time",
            "search_web",
        }
        async with mcp_client.research_session() as nested:
            assert nested is session
        results = await asyncio.gather(
            mcp_client.call_mcp_tool("search_places", {"query": "museum", "near": "Chicago"}),
            mcp_client.call_mcp_tool("search_web", {"query": "Chicago museum tickets"}),
            mcp_client.call_mcp_tool(
                "get_travel_time", {"origin": "A", "destination": "B", "mode": "WALK"}
            ),
            mcp_client.call_mcp_tool(
                "get_weather_forecast",
                {"city": "Chicago", "start_date": "invalid", "end_date": "invalid"},
            ),
        )
        assert [type(result) for result in results] == [
            PlacesResult,
            WebResearch,
            TravelTime,
            WeatherForecast,
        ]
        assert [result.code for result in results] == [
            NOT_CONFIGURED,
            NOT_CONFIGURED,
            NOT_CONFIGURED,
            BAD_REQUEST,
        ]
        rejected = await mcp_client.call_mcp_tool("search_web", {})
        assert rejected.code == BAD_REQUEST
        route = {"origin": "A", "destination": "B", "mode": "TRANSIT"}
        aware = await mcp_client.call_mcp_tool(
            "get_travel_time", {**route, "depart_at": "2026-10-06T09:30:00+09:00"}
        )
        assert isinstance(aware, TravelTime)
        assert aware.code == NOT_CONFIGURED
        naive = await mcp_client.call_mcp_tool(
            "get_travel_time", {**route, "depart_at": "2026-10-06T09:30:00"}
        )
        assert naive.code == BAD_REQUEST
    assert mcp_client._SESSION.get() is None


@pytest.mark.parametrize("transport", ["stdio", "memory"], indirect=True)
async def test_planner_uses_real_mcp_service(monkeypatch, transport):
    monkeypatch.setattr(registry, "call_mcp_tool", mcp_client.call_mcp_tool)
    monkeypatch.setattr(orchestrator, "research_session", mcp_client.research_session)

    async def unexpected_local(**kwargs):
        pytest.fail("The planner bypassed MCP")

    monkeypatch.setitem(registry.TOOL_FUNCTIONS, "search_web", unexpected_local)
    llm = FakeLLM(
        [
            completion(tool_calls=[tool_call("search_web", {"query": "Chicago travel"})]),
            completion(
                content=json.dumps(
                    {"clarification": {"questions": ["What dates?"], "reason": "inputs"}}
                )
            ),
        ]
    )
    result = await orchestrator.plan_trip("Chicago trip", client=llm, model="test")
    assert result.clarification.questions == ["What dates?"]
    tool_messages = [m for m in llm.requests[-1]["messages"] if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    payload = json.loads(tool_messages[0]["content"])
    assert payload["code"] == NOT_CONFIGURED


async def test_departure_time_is_serialized_without_identity_context():
    received = []

    class Session:
        async def call_tool(self, name, arguments, **kwargs):
            received.append(arguments)
            return SimpleNamespace(
                is_error=False,
                structured_content=TravelTime(
                    ok=True, origin="A", destination="B", mode="TRANSIT", seconds=600
                ).model_dump(),
            )

    departure = datetime.fromisoformat("2026-10-06T09:30:00+09:00")
    token = mcp_client._SESSION.set(Session())
    try:
        result = await mcp_client.call_mcp_tool(
            "get_travel_time",
            {"origin": "A", "destination": "B", "mode": "TRANSIT", "depart_at": departure},
            {"user_id": "private-user"},
        )
    finally:
        mcp_client._SESSION.reset(token)
    assert result.seconds == 600
    assert received == [
        {
            "origin": "A",
            "destination": "B",
            "mode": "TRANSIT",
            "depart_at": "2026-10-06T09:30:00+09:00",
        }
    ]


@pytest.mark.parametrize(
    "failure, expected",
    [
        (TimeoutError("secret upstream URL"), TIMED_OUT),
        (RuntimeError("secret upstream URL"), UNAVAILABLE),
    ],
)
async def test_transport_failures_are_safe_and_typed(failure, expected):
    class Session:
        async def call_tool(self, *args, **kwargs):
            raise failure

    token = mcp_client._SESSION.set(Session())
    try:
        result = await mcp_client.call_mcp_tool("search_web", {"query": "Chicago"})
    finally:
        mcp_client._SESSION.reset(token)
    assert result.code == expected
    assert "secret" not in result.model_dump_json()


async def test_failed_session_retries_without_direct_fallback(monkeypatch):
    monkeypatch.setattr(registry, "call_mcp_tool", mcp_client.call_mcp_tool)

    async def unexpected_local(**kwargs):
        pytest.fail("MCP failure silently bypassed the service")

    monkeypatch.setitem(registry.TOOL_FUNCTIONS, "search_web", unexpected_local)
    token = mcp_client._SESSION.set(ToolOutcome(ok=False, code=UNAVAILABLE, error="failed"))
    try:
        result = await registry.call_tool("search_web", {"query": "Chicago"})
    finally:
        mcp_client._SESSION.reset(token)
    assert result.code == UNAVAILABLE
    assert result.attempts == registry.MAX_TOOL_ATTEMPTS


async def test_malformed_server_result_is_unavailable():
    class Session:
        async def call_tool(self, *args, **kwargs):
            return SimpleNamespace(is_error=False, structured_content={"ok": True})

    token = mcp_client._SESSION.set(Session())
    try:
        result = await mcp_client.call_mcp_tool("get_travel_time", {})
    finally:
        mcp_client._SESSION.reset(token)
    assert result.code == UNAVAILABLE


@pytest.mark.parametrize("body_fails", [False, True])
async def test_cleanup_failure_preserves_result_or_original_exception(
    monkeypatch, caplog, body_fails
):
    class Session:
        def __init__(self, *args, **kwargs):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            pass

        async def call_tool(self, *args, **kwargs):
            return SimpleNamespace(
                is_error=False,
                structured_content=WebResearch(
                    ok=False, query="tickets", code=NOT_CONFIGURED
                ).model_dump(),
            )

    @asynccontextmanager
    async def broken_transport(parameters):
        try:
            yield (None, None)
        finally:
            raise RuntimeError("secret cleanup details")

    @asynccontextmanager
    async def client_session(*args, **kwargs):
        yield Session()

    monkeypatch.setattr(mcp_client, "stdio_client", broken_transport)
    monkeypatch.setattr(mcp_client, "ClientSession", client_session)

    async def run():
        async with mcp_client.research_session():
            if body_fails:
                raise ValueError("original planning failure")
            return await mcp_client.call_mcp_tool("search_web", {"query": "tickets"})

    if body_fails:
        with pytest.raises(ValueError, match="original planning failure"):
            await run()
    else:
        result = await run()
        assert result.code == NOT_CONFIGURED
    assert mcp_client._SESSION.get() is None
    assert "MCP session cleanup failed" in caplog.text
    assert "secret" not in caplog.text
