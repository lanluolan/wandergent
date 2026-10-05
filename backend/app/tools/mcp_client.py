import json
import logging
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from app.config import settings
from app.tools.base import BAD_REQUEST, TIMED_OUT, UNAVAILABLE, ToolOutcome
from app.tools.maps import PlacesResult, TravelTime
from app.tools.weather import WeatherForecast
from app.tools.web_search import WebResearch

logger = logging.getLogger(__name__)
_SESSION: ContextVar[ClientSession | ToolOutcome | None] = ContextVar("research_mcp", default=None)
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_RESULT_MODELS = {
    "get_weather_forecast": WeatherForecast,
    "search_places": PlacesResult,
    "get_travel_time": TravelTime,
    "search_web": WebResearch,
}


def _failure(exc: Exception) -> ToolOutcome:
    code = UNAVAILABLE
    if isinstance(exc, TimeoutError):
        code = TIMED_OUT
    elif isinstance(exc, MCPError):
        if exc.code == -32602:
            code = BAD_REQUEST
        elif "timed out" in exc.message.lower():
            code = TIMED_OUT
    return ToolOutcome(ok=False, code=code, error="MCP research service request failed")


@asynccontextmanager
async def research_session():
    existing = _SESSION.get()
    if existing is not None:
        yield existing
        return
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-B", "-m", "app.mcp_server"],
        cwd=_BACKEND_ROOT,
        env={
            "PYTHONDONTWRITEBYTECODE": "1",
            "DEBUG": "false",
            "GOOGLE_MAPS_API_KEY": settings.google_maps_api_key,
            "BRAVE_SEARCH_API_KEY": settings.brave_search_api_key,
            "TOOL_TIMEOUT_SECONDS": str(settings.tool_timeout_seconds),
        },
    )
    stack = AsyncExitStack()
    try:
        try:
            streams = await stack.enter_async_context(stdio_client(parameters))
            session = await stack.enter_async_context(
                ClientSession(
                    *streams, read_timeout_seconds=max(10, settings.tool_timeout_seconds * 3)
                )
            )
            await session.initialize()
            await session.list_tools()
            active: ClientSession | ToolOutcome = session
        except Exception as exc:
            active = _failure(exc)
        token = _SESSION.set(active)
        try:
            yield active
        finally:
            _SESSION.reset(token)
    finally:
        try:
            await stack.aclose()
        except Exception as exc:
            logger.warning("MCP session cleanup failed type=%s", type(exc).__name__)


def _json_value(value):
    if isinstance(value, date | datetime):
        return value.isoformat()
    raise TypeError("Unsupported MCP argument type")


async def call_mcp_tool(name: str, arguments: dict, context: dict | None = None) -> ToolOutcome:
    active = _SESSION.get()
    if active is None:
        async with research_session():
            return await call_mcp_tool(name, arguments, context)
    if isinstance(active, ToolOutcome):
        return active
    try:
        serialized = json.loads(json.dumps(arguments, default=_json_value))
        result = await active.call_tool(
            name, serialized, read_timeout_seconds=max(10, settings.tool_timeout_seconds * 3)
        )
        if result.is_error:
            return ToolOutcome(ok=False, code=BAD_REQUEST, error="MCP tool rejected the request")
        if not isinstance(result.structured_content, dict):
            return ToolOutcome(
                ok=False, code=UNAVAILABLE, error="MCP tool returned no structured data"
            )
        model = _RESULT_MODELS.get(name, ToolOutcome)
        return model.model_validate(result.structured_content)
    except ValidationError:
        return ToolOutcome(ok=False, code=UNAVAILABLE, error="Invalid MCP result")
    except (ValueError, TypeError):
        return ToolOutcome(ok=False, code=BAD_REQUEST, error="Invalid MCP arguments")
    except Exception as exc:
        return _failure(exc)
