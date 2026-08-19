"""Bring-your-own-key: per-request LLM credentials.

Covers the two halves separately. `parse_override` is where the rules live -- what is
accepted, what is refused, and what a refusal says. The endpoint tests only assert that
those rules are reachable over HTTP and that what comes out the far side is a client
built from the caller's key rather than the server's.

`plan_trip` is patched out throughout: none of this needs a model, and a test that
reached one would be measuring the provider.
"""

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import (
    DEFAULT_BYOK_BASE_URLS,
    LlmCredentialsError,
    LlmOverride,
    allowed_byok_base_urls,
    build_client,
    parse_override,
)
from app.agent.schemas import Itinerary
from app.config import settings
from app.main import app

from .test_plan_endpoint import sample_result

client = TestClient(app)

KEY = "sk-caller-owns-this-1234"


def test_no_headers_means_the_server_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_byok_base_urls", "")
    assert parse_override(None, None, None) is None
    assert parse_override("", "  ", "") is None


def test_a_key_alone_inherits_the_server_endpoint_and_model(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "openai_model", "server-model")

    override = parse_override(KEY, None, None)

    assert override == LlmOverride(KEY, "https://api.example.com/v1", "server-model")


def test_a_model_without_a_key_is_refused_not_ignored() -> None:
    # Silently dropping it would spend the operator's money on a request that asked not
    # to, and would look from the client like a setting that never takes effect.
    with pytest.raises(LlmCredentialsError, match="without an API key"):
        parse_override(None, None, "gpt-4o")
    with pytest.raises(LlmCredentialsError, match="without an API key"):
        parse_override(None, "https://api.openai.com/v1", None)


def test_an_unlisted_endpoint_is_refused(monkeypatch) -> None:
    # The point is not that the address is malformed -- it is that this server would be
    # the one making the request. See `allowed_byok_base_urls`.
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "llm_byok_base_urls", "")

    for hostile in (
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://127.0.0.1:8000/v1",  # this server, or something behind it
        "http://192.168.1.10:11434/v1",  # anything on the operator's LAN
        "https://evil.example.com/v1",
    ):
        with pytest.raises(LlmCredentialsError, match="does not allow"):
            parse_override(KEY, hostile, None)


def test_the_well_known_providers_need_no_configuration(monkeypatch) -> None:
    # "Use whatever model you want" is the product answer, so the default list has to
    # make it true. It is still closed: these hosts are fixed, not caller-chosen.
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "llm_byok_base_urls", "")

    for provider in DEFAULT_BYOK_BASE_URLS:
        assert parse_override(KEY, provider, "some-model") is not None


def test_setting_the_allowlist_narrows_it_rather_than_adding(monkeypatch) -> None:
    # The way an operator locks this down. Their own endpoint stays reachable regardless,
    # so narrowing cannot lock the server out of itself.
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "llm_byok_base_urls", "https://api.deepseek.com/v1")

    assert allowed_byok_base_urls() == {
        "https://api.example.com/v1",
        "https://api.deepseek.com/v1",
    }
    with pytest.raises(LlmCredentialsError, match="does not allow"):
        parse_override(KEY, "https://api.openai.com/v1", None)


def test_the_servers_own_endpoint_is_always_allowed(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(settings, "llm_byok_base_urls", "")

    # Same provider, different account: the case BYOK exists for, and it needs no config.
    override = parse_override(KEY, "https://api.example.com/v1", None)
    assert override is not None
    assert override.base_url == "https://api.example.com/v1"
    # A trailing slash is the same endpoint, not an unlisted one.
    assert parse_override(KEY, "https://api.example.com/v1/", None) is not None


def test_the_operator_can_name_an_endpoint_the_defaults_miss(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.com/v1")
    monkeypatch.setattr(
        settings,
        "llm_byok_base_urls",
        "https://api.openai.com/v1, https://llm.internal.corp/v1/",
    )

    assert allowed_byok_base_urls() == {
        "https://api.example.com/v1",
        "https://api.openai.com/v1",
        "https://llm.internal.corp/v1",
    }
    assert parse_override(KEY, "https://llm.internal.corp/v1", None) is not None


def test_the_key_stays_out_of_reprs_and_logs() -> None:
    # This object lands in exception messages and debugger frames, both of which get
    # pasted into issues. A dataclass' generated repr would print the key in full.
    text = repr(parse_override(KEY, None, "some-model"))

    assert KEY not in text
    assert "***1234" in text
    assert "some-model" in text


def test_a_caller_key_is_used_even_when_the_server_has_none(monkeypatch) -> None:
    # The deployment this is for: an operator who runs the backend for other people
    # without putting their own key on it.
    monkeypatch.setattr(settings, "openai_api_key", "")

    built = build_client(LlmOverride(KEY, "https://api.example.com/v1", "m"))

    assert built.api_key == KEY
    assert str(built.base_url).rstrip("/") == "https://api.example.com/v1"


def _patch_plan(monkeypatch) -> dict:
    """Capture what the endpoint hands the orchestrator."""
    seen: dict = {}

    async def fake(request: str, **kwargs):
        seen.update(kwargs)
        return sample_result()

    monkeypatch.setattr("app.main.plan_trip", fake)
    return seen


def test_the_headers_reach_the_orchestrator(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_base_url", "https://api.example.com/v1")
    seen = _patch_plan(monkeypatch)

    response = client.post(
        "/plan",
        json={"message": "3 days in Los Angeles"},
        headers={"X-LLM-Api-Key": KEY, "X-LLM-Model": "caller-model"},
    )

    assert response.status_code == 200
    assert seen["model"] == "caller-model"
    assert seen["client"] is not None
    assert seen["client"].api_key == KEY


def test_byok_turns_off_fast_model_routing(monkeypatch) -> None:
    # FAST_MODEL is a name from *this* server's provider. Sent to the caller's endpoint
    # it fails on the first turn of every run -- and routing is silent, so the failure
    # would look like the caller's key being rejected.
    monkeypatch.setattr(settings, "fast_model", "server-cheap-model")
    seen = _patch_plan(monkeypatch)

    client.post(
        "/plan",
        json={"message": "3 days in Los Angeles"},
        headers={"X-LLM-Api-Key": KEY},
    )

    assert seen["fast_model"] == ""


def test_without_headers_nothing_is_overridden(monkeypatch) -> None:
    seen = _patch_plan(monkeypatch)

    client.post("/plan", json={"message": "3 days in Los Angeles"})

    assert seen["client"] is None
    assert seen["model"] is None
    assert seen["fast_model"] is None


def test_a_bad_endpoint_is_a_400_not_a_500(monkeypatch) -> None:
    # 4xx because the request is wrong, not the service. A 5xx would have the client
    # retrying forever against a server that will never accept what it is sending.
    monkeypatch.setattr(settings, "llm_byok_base_urls", "")
    _patch_plan(monkeypatch)

    response = client.post(
        "/plan",
        json={"message": "3 days in Los Angeles"},
        headers={"X-LLM-Api-Key": KEY, "X-LLM-Base-Url": "http://169.254.169.254/"},
    )

    assert response.status_code == 400
    assert KEY not in response.text


def test_the_stream_honours_the_same_headers(monkeypatch) -> None:
    seen: dict = {}

    async def fake(request: str, **kwargs):
        seen.update(kwargs)
        return
        yield  # pragma: no cover -- this is what makes it an async generator

    monkeypatch.setattr("app.main.stream_plan", fake)

    with client.stream(
        "POST",
        "/plan/stream",
        json={"message": "3 days in Los Angeles"},
        headers={"X-LLM-Api-Key": KEY, "X-LLM-Model": "caller-model"},
    ) as response:
        response.read()

    assert seen["model"] == "caller-model"
    assert seen["client"].api_key == KEY
    assert seen["fast_model"] == ""


def test_bringing_a_key_does_not_buy_a_bigger_allowance(monkeypatch) -> None:
    # Their key pays for tokens; a run still spends this server's Places and Routes
    # quota. And "I brought a key" is a header anyone can send.
    monkeypatch.setattr(settings, "max_plans_per_day", 1)
    _patch_plan(monkeypatch)
    headers = {"X-LLM-Api-Key": KEY}
    body = {"message": "3 days in Los Angeles"}

    assert client.post("/plan", json=body, headers=headers).status_code == 200
    assert client.post("/plan", json=body, headers=headers).status_code == 503


def test_an_itinerary_still_comes_back_unchanged(monkeypatch) -> None:
    _patch_plan(monkeypatch)

    response = client.post(
        "/plan",
        json={"message": "3 days"},
        headers={"X-LLM-Api-Key": KEY},
    )

    assert Itinerary.model_validate(response.json()["itinerary"]).destination == "Chicago"


def test_a_keyless_server_asks_the_traveller_for_a_key(monkeypatch) -> None:
    # The deployment this feature exists for: the operator pays for no tokens at all.
    # The person who hits this is a traveller, so the sentence has to point at the screen
    # they can act on, and 400 rather than 500 because retrying cannot fix it.
    monkeypatch.setattr(settings, "openai_api_key", "")
    _patch_plan(monkeypatch)

    response = client.post("/plan", json={"message": "3 days in Los Angeles"})

    assert response.status_code == 400
    assert "You -> AI model" in response.json()["detail"]
    assert ".env" not in response.json()["detail"]


def test_a_keyless_server_still_serves_a_caller_who_brings_one(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    seen = _patch_plan(monkeypatch)

    response = client.post(
        "/plan",
        json={"message": "3 days in Los Angeles"},
        headers={"X-LLM-Api-Key": KEY},
    )

    assert response.status_code == 200
    assert seen["client"].api_key == KEY


def test_the_stream_refuses_a_keyless_server_before_it_starts(monkeypatch) -> None:
    # Before, not during: once a stream has begun the status line is gone, and this
    # failure needs to arrive as a status code the client can branch on.
    monkeypatch.setattr(settings, "openai_api_key", "")

    response = client.post("/plan/stream", json={"message": "3 days in Los Angeles"})

    assert response.status_code == 400
