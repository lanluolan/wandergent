"""Wandergent's tools, exposed over the Model Context Protocol.

**This is a second surface, not a replacement.** The planning agent keeps calling its
tools in-process: routing its own function calls through JSON-RPC would add a
serialisation round trip per call inside a single process, and would break the request
context (`user_id`) that the in-process registry injects -- MCP has no channel for
"who is this request for".

What this buys instead is reuse: any MCP client -- Claude Desktop, another agent, a
notebook -- can use the same weather tool, from the same implementation. One
implementation, two surfaces, no duplicated logic.

Run it:

    cd backend && uv run python -m app.mcp_server          # stdio, for desktop clients
    cd backend && uv run python -m app.mcp_server --http   # streamable HTTP on :8765

**Memory is deliberately not exposed here.** `remember_preference` writes to one user's
store, and this surface authenticates nobody -- a client could claim any id and pollute
someone else's preferences. It stays behind the agent, where identity comes from a token.
"""

import sys

from mcp.server import MCPServer

from app.tools.maps import DEFAULT_LANGUAGE, MAX_PLACES, PlacesResult, TravelTime
from app.tools.maps import get_travel_time as _get_travel_time
from app.tools.maps import search_places as _search_places
from app.tools.weather import FORECAST_DAYS, WeatherForecast
from app.tools.weather import get_weather_forecast as _get_weather_forecast

server = MCPServer(
    name="wandergent",
    version="0.1.0",
    instructions=(
        "Travel-planning tools from the Wandergent project. The weather tool is keyless "
        "(Open-Meteo) and degrades gracefully instead of failing. The maps tools need the "
        "server configured with a Google Maps key; without one they return ok=false."
    ),
)


@server.tool(
    name="get_weather_forecast",
    title="Daily weather forecast",
    description=(
        "Daily weather for a city over a date range, for deciding whether outdoor plans are "
        f"viable. Forecasts exist for {FORECAST_DAYS} days counting today; a later date is "
        "not a failure -- it returns ok=true with no days and a note saying so, so the "
        "caller falls back to seasonal norms instead of inventing weather. Never raises: "
        "timeouts, unknown cities and upstream failures all come back as ok=false."
    ),
)
async def get_weather_forecast(
    city: str,
    start_date: str,
    end_date: str,
) -> WeatherForecast:
    """City name plus an ISO date range, e.g. ("Chicago", "2026-08-10", "2026-08-12")."""
    # Straight through to the implementation the agent uses. Only the three public
    # parameters are exposed; the injectable client and clock stay internal.
    return await _get_weather_forecast(city, start_date, end_date)


@server.tool(
    name="search_places",
    title="Find real venues",
    description=(
        "Find venues that actually exist -- restaurants, hotels, museums -- with address, "
        "rating and review count, so a plan can cite a real place instead of a plausible "
        f"one. Returns at most {MAX_PLACES}. Never raises: an unconfigured key, a timeout "
        "or no matches all come back as a result with a reason."
    ),
)
async def search_places(
    query: str, near: str, limit: int = 4, language: str = DEFAULT_LANGUAGE
) -> PlacesResult:
    """What to look for plus where, e.g. ("deep dish pizza", "Chicago")."""
    return await _search_places(query, near, limit, language)


@server.tool(
    name="get_travel_time",
    title="Real travel time",
    description=(
        "Travel time and distance between two places, by WALK, DRIVE or TRANSIT. Transit "
        "coverage is regional: where the upstream service has no operator data the result "
        "is ok=false with no route, so fall back to another mode. Never raises."
    ),
)
async def get_travel_time(origin: str, destination: str, mode: str = "WALK") -> TravelTime:
    """Two place names or addresses, plus WALK, DRIVE or TRANSIT."""
    return await _get_travel_time(origin, destination, mode)


def main() -> None:
    if "--http" in sys.argv:
        server.run(transport="streamable-http", host="127.0.0.1", port=8765)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
