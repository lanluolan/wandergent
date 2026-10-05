"""Public research tools used by the App backend through MCP over stdio.

The same service can be used by external MCP clients. No memory writes or Wanderlog tools.
"""

import logging
import sys
from contextlib import asynccontextmanager

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import AwareDatetime

from app.config import settings
from app.tools.maps import DEFAULT_LANGUAGE, MAX_PLACES, PlacesResult, TravelTime
from app.tools.maps import get_travel_time as _get_travel_time
from app.tools.maps import search_places as _search_places
from app.tools.weather import FORECAST_DAYS, WeatherForecast
from app.tools.weather import get_weather_forecast as _get_weather_forecast
from app.tools.web_search import WebResearch
from app.tools.web_search import search_web as _search_web

logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: MCPServer):
    async with httpx.AsyncClient(timeout=settings.tool_timeout_seconds) as client:
        yield {"http_client": client}


server = MCPServer(
    name="wandergent",
    version="0.1.0",
    lifespan=lifespan,
    instructions=(
        "Travel-planning tools from the Wandergent project. The weather tool is keyless "
        "(Open-Meteo) and degrades gracefully instead of failing. The maps tools need the "
        "server configured with a Google Maps key; web research needs a Brave Search key. "
        "Unconfigured services return ok=false. No memory writes or Wanderlog operations."
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
    ctx: Context,
    language: str = DEFAULT_LANGUAGE,
) -> WeatherForecast:
    """City name plus an ISO date range, e.g. ("Chicago", "2026-08-10", "2026-08-12")."""
    return await _get_weather_forecast(
        city,
        start_date,
        end_date,
        language=language,
        client=ctx.request_context.lifespan_context["http_client"],
    )


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
    query: str,
    near: str,
    ctx: Context,
    limit: int = 4,
    language: str = DEFAULT_LANGUAGE,
    purpose: str = "required",
) -> PlacesResult:
    """What to look for plus where, e.g. ("deep dish pizza", "Chicago")."""
    return await _search_places(
        query,
        near,
        limit,
        language,
        purpose,
        client=ctx.request_context.lifespan_context["http_client"],
    )


@server.tool(
    name="get_travel_time",
    title="Real travel time",
    description=(
        "Travel time and distance between two places, by WALK, DRIVE or TRANSIT. Transit "
        "coverage is regional: where the upstream service has no operator data the result "
        "is ok=false with no route, so fall back to another mode. Never raises."
    ),
)
async def get_travel_time(
    origin: str,
    destination: str,
    ctx: Context,
    mode: str = "WALK",
    depart_at: AwareDatetime | None = None,
) -> TravelTime:
    """Two place names or addresses, plus WALK, DRIVE or TRANSIT."""
    return await _get_travel_time(
        origin,
        destination,
        mode,
        depart_at=depart_at,
        client=ctx.request_context.lifespan_context["http_client"],
    )


@server.tool(
    name="search_web",
    title="Destination research",
    description="Research destination facts with source URLs and dated search excerpts. "
    "Requires server-configured Brave Search. Excerpts are partial evidence; failures "
    "return ok=false. No bookings or account writes.",
)
async def search_web(query: str, ctx: Context) -> WebResearch:
    return await _search_web(query, client=ctx.request_context.lifespan_context["http_client"])


def main() -> None:
    if "--http" in sys.argv:
        server.run(transport="streamable-http", host="127.0.0.1", port=8765)
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
