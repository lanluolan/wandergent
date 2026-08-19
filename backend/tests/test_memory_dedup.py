"""Near-duplicate handling in the preference store.

The store already made verbatim repeats a no-op via the primary key. These cover the
repeats a model actually produces -- same fact, different words -- and, more
importantly, the cases that must **not** be merged: an update that contradicts what was
learned before shares words with it, and losing that is worse than storing it twice.
"""

import pytest

from app.memory.store import MAX_PREFERENCES_STORED, PreferenceStore, is_restatement


@pytest.fixture
def store(tmp_path) -> PreferenceStore:
    return PreferenceStore(tmp_path / "memory.db")


@pytest.mark.parametrize(
    ("candidate", "known"),
    [
        ("loves museum", "loves museums"),
        ("avoids hiking.", "avoids hiking"),
        ("Avoids Hiking", "avoids hiking"),
        ("travels  with a toddler", "travels with a toddler"),
    ],
)
def test_a_restatement_is_recognised(candidate: str, known: str) -> None:
    assert is_restatement(candidate, [known])


@pytest.mark.parametrize(
    ("candidate", "known"),
    [
        # The pair that makes a loose threshold dangerous: one word apart, opposite
        # meaning. Merging this silently throws away the traveller changing their mind.
        ("loves hiking", "avoids hiking"),
        ("vegetarian", "vegan"),
        ("travels with a toddler", "travels with a dog"),
        ("prefers morning starts", "prefers late starts"),
        ("budget-conscious", "happy to splurge"),
    ],
)
def test_a_different_fact_is_not_merged(candidate: str, known: str) -> None:
    assert not is_restatement(candidate, [known])


def test_nothing_known_yet_is_never_a_restatement() -> None:
    assert not is_restatement("loves museums", [])


async def test_remembering_the_same_fact_twice_stores_it_once(store: PreferenceStore) -> None:
    await store.remember("u1", ["loves museums"])
    added = await store.remember("u1", ["loves museum"])

    assert added == []
    assert [p.text for p in await store.recall("u1")] == ["loves museums"]


async def test_duplicates_inside_one_call_are_caught(store: PreferenceStore) -> None:
    """A model can restate itself within a single tool call."""
    added = await store.remember("u1", ["loves museums", "loves museum", "avoids hiking"])

    assert added == ["loves museums", "avoids hiking"]


async def test_a_contradicting_update_is_kept(store: PreferenceStore) -> None:
    """Both survive; recall orders newest first so the change is visible."""
    await store.remember("u1", ["avoids hiking"])
    added = await store.remember("u1", ["loves hiking"])

    assert added == ["loves hiking"]
    assert {p.text for p in await store.recall("u1")} == {"avoids hiking", "loves hiking"}


async def test_storage_is_capped_and_drops_the_oldest(store: PreferenceStore) -> None:
    """Recall was already capped; without this the table grew for the life of an account."""
    for index in range(MAX_PREFERENCES_STORED + 5):
        await store.remember("u1", [f"preference number {index}"])

    kept = {p.text for p in await store.recall("u1", limit=999)}

    assert len(kept) == MAX_PREFERENCES_STORED
    assert "preference number 0" not in kept
    assert f"preference number {MAX_PREFERENCES_STORED + 4}" in kept


async def test_one_users_preferences_do_not_dedup_against_anothers(
    store: PreferenceStore,
) -> None:
    await store.remember("u1", ["loves museums"])
    added = await store.remember("u2", ["loves museums"])

    assert added == ["loves museums"]
