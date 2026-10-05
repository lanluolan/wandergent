import httpx
import pytest

from app.config import settings
from app.tools.registry import call_tool, shared_cache_ttl
from app.tools.web_search import search_web


async def test_unconfigured_search_does_not_make_a_request(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "")

    def unexpected(request):
        raise AssertionError("No search should run without configuration")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        result = await search_web("museum tickets", client=client)
    assert not result.ok and result.code == "not_configured"
    assert result.sources == [] and result.collected_at is None


async def test_web_tool_preserves_source_and_date_without_exposing_credentials(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "server-test-token")

    def respond(request):
        assert request.headers["X-Subscription-Token"] == "server-test-token"
        assert request.url.params["q"] == "museum tickets October 2026"
        assert request.url.params["extra_snippets"] == "true"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "<b>Museum</b>",
                            "url": "https://museum.example/tickets",
                            "description": "Admission &amp; reservations",
                            "extra_snippets": ["Closed Mondays"],
                            "page_age": "2026-10-01",
                        },
                        {"url": "javascript:alert(1)", "description": "bad link"},
                        {"url": "https://user:secret@museum.example", "description": "credentials"},
                    ]
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await call_tool(
            "search_web",
            {"query": "museum tickets October 2026"},
            context={"http_client": client},
        )
    assert result.ok and len(result.sources) == 1
    assert result.sources[0].title == "Museum"
    assert result.sources[0].excerpts == ["Admission & reservations", "Closed Mondays"]
    assert result.sources[0].page_date == "2026-10-01"
    assert result.collected_at is not None
    assert "server-test-token" not in result.model_dump_json()
    assert shared_cache_ttl("search_web") == 300


@pytest.mark.parametrize(("status", "code"), [(429, "rate_limited"), (500, "unavailable")])
async def test_upstream_failure_is_typed_and_does_not_echo_body(monkeypatch, status, code):
    monkeypatch.setattr(settings, "brave_search_api_key", "test-token")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="secret upstream details")
        )
    ) as client:
        result = await search_web("tickets", client=client)
    assert not result.ok and result.code == code
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize("payload", [{}, [], {"web": {"results": "invalid"}}])
async def test_missing_or_malformed_results_never_become_verified_facts(monkeypatch, payload):
    monkeypatch.setattr(settings, "brave_search_api_key", "test-token")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        result = await search_web("tickets", client=client)
    assert not result.ok
    assert result.sources == []


@pytest.mark.parametrize("query", ["", " " * 3, "x" * 601, "word " * 76])
async def test_query_limits_are_checked_before_network_access(query):
    result = await search_web(query)
    assert not result.ok and result.code == "bad_request"


async def test_timeout_is_a_degraded_tool_result(monkeypatch):
    monkeypatch.setattr(settings, "brave_search_api_key", "test-token")

    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        result = await search_web("tickets", client=client)
    assert not result.ok and result.code == "timed_out"


@pytest.mark.parametrize("extra", ["wrong type", {"snippet": "wrong type"}, 42])
async def test_malformed_extra_snippets_do_not_create_fake_excerpts(monkeypatch, extra):
    monkeypatch.setattr(settings, "brave_search_api_key", "test-token")
    payload = {
        "web": {
            "results": [
                {
                    "url": "https://museum.example/tickets",
                    "description": "Official admission",
                    "extra_snippets": extra,
                }
            ]
        }
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    ) as client:
        result = await search_web("tickets", client=client)
    assert result.ok
    assert result.sources[0].excerpts == ["Official admission"]
