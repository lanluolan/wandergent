"""Memory: the store, the tool that writes to it, and the recall that reads it back."""

from datetime import date

import pytest

from app.agent.orchestrator import stream_plan
from app.memory.store import MAX_PREFERENCE_CHARS, PreferenceStore
from app.tools.memory import remember_preference
from app.tools.registry import TOOL_FUNCTIONS, call_tool
from app.tools.weather import WeatherForecast
from tests.fakes import ITINERARY_JSON, FakeLLM, completion, tool_call

TODAY = date(2026, 8, 5)


@pytest.fixture
def store(tmp_path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "memory.db")


@pytest.fixture(autouse=True)
def stub_weather(monkeypatch):
    async def fake(city: str, **kwargs) -> WeatherForecast:
        return WeatherForecast(ok=True, city=city)

    monkeypatch.setitem(TOOL_FUNCTIONS, "get_weather_forecast", fake)


# --- store -------------------------------------------------------------------------


async def test_remembers_and_recalls(store) -> None:
    await store.remember("alice", ["avoids hiking", "loves regional food"])

    recalled = [preference.text for preference in await store.recall("alice")]

    assert set(recalled) == {"avoids hiking", "loves regional food"}


async def test_remembering_twice_is_a_no_op(store) -> None:
    first = await store.remember("alice", ["avoids hiking"])
    second = await store.remember("alice", ["avoids hiking"])

    assert first == ["avoids hiking"]
    # The primary key does the deduplication, so this is not a check-then-insert race.
    assert second == []
    assert len(await store.recall("alice")) == 1


async def test_users_cannot_see_each_other(store) -> None:
    await store.remember("alice", ["avoids hiking"])
    await store.remember("bob", ["vegetarian"])

    assert [p.text for p in await store.recall("bob")] == ["vegetarian"]


async def test_blank_and_oversized_entries_are_dropped(store) -> None:
    stored = await store.remember("alice", ["", "   ", "x" * (MAX_PREFERENCE_CHARS + 50)])

    assert stored == ["x" * MAX_PREFERENCE_CHARS]
    assert len(await store.recall("alice")) == 1


async def test_forget_clears_a_user(store) -> None:
    await store.remember("alice", ["a", "b"])

    assert await store.forget("alice") == 2
    assert await store.recall("alice") == []


async def test_unknown_user_has_no_memory(store) -> None:
    assert await store.recall("nobody") == []


# --- the tool ----------------------------------------------------------------------


async def test_the_tool_writes_to_the_user_in_context(store) -> None:
    outcome = await remember_preference(
        ["avoids hiking"], context={"user_id": "alice"}, store=store
    )

    assert outcome.ok
    assert outcome.stored == ["avoids hiking"]
    assert [p.text for p in await store.recall("alice")] == ["avoids hiking"]


async def test_the_tool_is_a_no_op_without_a_user(store) -> None:
    """Anonymous requests are legitimate, so this must not look like a failure."""
    outcome = await remember_preference(["avoids hiking"], context={}, store=store)

    assert outcome.ok
    assert outcome.stored == []


async def test_the_registry_injects_context_but_the_model_cannot(monkeypatch, store) -> None:
    """Identity comes from the request, never from arguments the model chose."""
    seen: dict = {}

    async def spy(preferences, *, context=None, **kwargs):
        seen["preferences"] = preferences
        seen["context"] = context
        return await remember_preference(preferences, context=context, store=store)

    monkeypatch.setitem(TOOL_FUNCTIONS, "remember_preference", spy)

    # The model tries to name a different user in its arguments; it is simply ignored,
    # because user_id is not part of the tool schema.
    await call_tool(
        "remember_preference",
        {"preferences": ["likes museums"], "user_id": "mallory"},
        context={"user_id": "alice"},
    )

    assert seen["context"] == {"user_id": "alice"}
    assert [p.text for p in await store.recall("alice")] == ["likes museums"]
    assert await store.recall("mallory") == []


async def test_tools_that_do_not_want_context_do_not_get_it(monkeypatch) -> None:
    """The weather tool has no **kwargs; passing context would be a TypeError."""
    outcome = await call_tool(
        "get_weather_forecast",
        {"city": "Chicago"},
        context={"user_id": "alice"},
    )

    assert outcome.ok


# --- the loop ----------------------------------------------------------------------


async def test_known_preferences_reach_the_system_prompt(store) -> None:
    await store.remember("alice", ["avoids hiking"])
    llm = FakeLLM([completion(content=ITINERARY_JSON)])

    async for _ in stream_plan(
        "2 days in Chicago",
        user_id="alice",
        client=llm,
        model="test-model",
        today=TODAY,
        memory=store,
    ):
        pass

    system_prompt = llm.requests[0]["messages"][0]["content"]
    assert "avoids hiking" in system_prompt
    assert "You already know this traveller" in system_prompt


async def test_an_anonymous_run_reads_no_memory(store) -> None:
    await store.remember("alice", ["avoids hiking"])
    llm = FakeLLM([completion(content=ITINERARY_JSON)])

    async for _ in stream_plan(
        "2 days in Chicago", client=llm, model="test-model", today=TODAY, memory=store
    ):
        pass

    assert "avoids hiking" not in llm.requests[0]["messages"][0]["content"]


async def test_the_agent_can_write_memory_mid_run(store, monkeypatch) -> None:
    """The whole point: remembering rides the existing tool loop, no extra LLM call."""

    async def bound(preferences, *, context=None, **kwargs):
        return await remember_preference(preferences, context=context, store=store)

    monkeypatch.setitem(TOOL_FUNCTIONS, "remember_preference", bound)

    llm = FakeLLM(
        [
            completion(
                tool_calls=[tool_call("remember_preference", {"preferences": ["avoids hiking"]})]
            ),
            completion(content=ITINERARY_JSON),
        ]
    )

    events = [
        event
        async for event in stream_plan(
            "2 days in Chicago, no hiking",
            user_id="alice",
            client=llm,
            model="test-model",
            today=TODAY,
            memory=store,
        )
    ]

    assert [p.text for p in await store.recall("alice")] == ["avoids hiking"]
    # It shows up as an ordinary tool call, so the client already renders it.
    assert any(e.type == "tool_call" and e.name == "remember_preference" for e in events)
    assert events[-1].result.itinerary is not None
