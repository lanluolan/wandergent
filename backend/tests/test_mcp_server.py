"""The MCP surface, exercised through a real client session over stdio.

Not "does the module import" -- an actual MCP client spawns the server as a subprocess,
performs the protocol handshake, lists the tools and calls one. That is the only way to
know the schema the SDK generates is usable by a client that is not this test.
"""

import sys

import pytest
from mcp import ClientSession, StdioServerParameters, stdio_client

from app.mcp_server import server

SERVER = StdioServerParameters(
    command=sys.executable,
    args=["-m", "app.mcp_server"],
    env=None,
)


async def test_the_tool_is_registered_with_a_usable_schema() -> None:
    """Cheap, in-process check of what a client would discover."""
    tools = await server.list_tools()

    weather = next(tool for tool in tools if tool.name == "get_weather_forecast")
    properties = weather.input_schema["properties"]
    assert set(properties) == {"city", "start_date", "end_date"}
    # The injectable client and clock are implementation details and must not leak.
    assert "client" not in properties
    assert "today" not in properties
    assert weather.description and "ok=false" in weather.description


async def test_memory_is_not_exposed() -> None:
    """This surface has no authenticated user, so it must not offer to write one."""
    assert "remember_preference" not in {tool.name for tool in await server.list_tools()}


async def test_the_maps_tools_are_published_without_their_seams() -> None:
    """Maps are safe to publish: they read public data and carry no user identity."""
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert {"search_places", "get_travel_time"} <= set(tools)

    places = tools["search_places"].input_schema["properties"]
    assert set(places) == {"query", "near", "limit", "language"}
    assert "client" not in places

    travel = tools["get_travel_time"].input_schema["properties"]
    assert set(travel) == {"origin", "destination", "mode"}
    # Transit is unavailable upstream; the description has to say so, or a client will
    # ask for it and read the empty answer as "no route exists".
    assert "transit" in tools["get_travel_time"].description.lower()


@pytest.mark.mcp
async def test_a_real_client_can_call_the_tool() -> None:
    """Full protocol round trip against the server as a subprocess.

    Hits the network (Open-Meteo), so it is opt-in: `pytest -m mcp`.
    """
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            listed = await session.list_tools()
            assert "get_weather_forecast" in {tool.name for tool in listed.tools}

            result = await session.call_tool(
                "get_weather_forecast",
                {"city": "Chicago", "start_date": "2026-08-10", "end_date": "2026-08-11"},
            )

            assert not result.is_error
            assert result.structured_content is not None
            assert result.structured_content["city"] == "Chicago"
