"""Tests for the community feed: publishing, browsing, saving, withdrawing.

Thin CRUD, so the tests concentrate on the places it is *not* thin: what the server refuses
to take on trust from the client, what has to survive a retry, and what an absent viewer
means.

Identity is not among the things taken on trust any more -- see `test_auth.py` for the
token itself, and the HTTP section below for the endpoints refusing to act without one.
"""

import pytest
from fastapi.testclient import TestClient

from app.agent.schemas import Itinerary
from app.auth.store import AuthStore
from app.community.store import MAX_SHARED_PER_AUTHOR, CommunityStore, PublishRequest
from app.main import app

AUTHOR = "account-1"
AUTHOR_NAME = "Yu"

PLAN = {
    "destination": "Chicago",
    "start_date": "2026-09-07",
    "end_date": "2026-09-08",
    "budget": 500,
    "currency": "USD",
    "days": [
        {
            "date": "2026-09-07",
            "summary": "museums",
            "activities": [
                {
                    "start_time": "10:00",
                    "end_time": "12:00",
                    "title": "Art Institute of Chicago",
                    "location": "Art Institute of Chicago",
                    "estimated_cost": 35.0,
                }
            ],
        },
        {
            "date": "2026-09-08",
            "summary": "food",
            "activities": [
                {
                    "start_time": "12:00",
                    "end_time": "13:30",
                    "title": "Lunch at Lou Malnati's",
                    "location": "Lou Malnati's Pizzeria",
                    "estimated_cost": 25.0,
                }
            ],
        },
    ],
}


@pytest.fixture
def store(tmp_path) -> CommunityStore:
    return CommunityStore(tmp_path / "community.db")


def publication(**overrides) -> PublishRequest:
    payload = {
        "request": "2 days in Chicago",
        "note": "Rainy-day friendly",
        "itinerary": Itinerary.model_validate(PLAN),
        **overrides,
    }
    return PublishRequest(**payload)


# --- what the server will not take on trust ----------------------------------------


async def test_the_card_is_derived_from_the_itinerary_not_supplied(store) -> None:
    """A caller that could set the destination and total independently could advertise a
    trip as somewhere it is not. Those fields are not in `PublishRequest` at all."""
    shared = await store.publish(publication(), AUTHOR, AUTHOR_NAME)

    assert shared.destination == "Chicago"
    assert shared.day_count == 2
    assert shared.total_cost == 60.0
    assert "destination" not in PublishRequest.model_fields
    assert "total_cost" not in PublishRequest.model_fields


async def test_the_author_cannot_be_stated_by_the_client_at_all() -> None:
    """The point of the whole auth change: while this field existed, every check on it was
    a formality. Its absence is the fix, so its absence is what gets tested."""
    assert "author_id" not in PublishRequest.model_fields
    assert "author_name" not in PublishRequest.model_fields


async def test_an_anonymous_author_still_gets_a_name(store) -> None:
    """An empty byline renders as a blank line in the feed, which reads as a bug."""
    shared = await store.publish(publication(), AUTHOR, "  ")

    assert shared.author_name == "Traveller"


# --- the feed ----------------------------------------------------------------------


async def test_the_feed_is_newest_first(store) -> None:
    first = await store.publish(publication(note="first"), AUTHOR, AUTHOR_NAME)
    second = await store.publish(publication(note="second"), AUTHOR, AUTHOR_NAME)

    feed = await store.feed()

    assert [item.id for item in feed.items] == [second.id, first.id]


async def test_the_feed_leaves_the_itinerary_out(store) -> None:
    """Thirty cards should not carry thirty itineraries over the wire."""
    await store.publish(publication(), AUTHOR, AUTHOR_NAME)
    (card,) = (await store.feed()).items

    assert not hasattr(card, "itinerary")


async def test_an_unknown_viewer_is_not_the_same_as_having_saved_nothing(store) -> None:
    """False draws an empty heart, which is a claim about this person. Without a viewer
    there is nobody to make that claim about."""
    await store.publish(publication(), AUTHOR, AUTHOR_NAME)

    anonymous = (await store.feed()).items[0]
    known = (await store.feed(viewer_id="reader-1")).items[0]

    assert anonymous.saved_by_viewer is None
    assert known.saved_by_viewer is False


async def test_one_author_cannot_crowd_out_the_feed(store) -> None:
    for index in range(MAX_SHARED_PER_AUTHOR + 3):
        await store.publish(publication(note=f"trip {index}"), AUTHOR, AUTHOR_NAME)

    feed = (await store.feed(limit=100)).items

    assert len(feed) == MAX_SHARED_PER_AUTHOR
    # The newest survive: trimming the post someone just made would be the worst choice.
    assert feed[0].note == f"trip {MAX_SHARED_PER_AUTHOR + 2}"


async def test_the_feed_limit_is_clamped_not_trusted(store) -> None:
    await store.publish(publication(), AUTHOR, AUTHOR_NAME)

    assert len((await store.feed(limit=10_000)).items) == 1
    assert len((await store.feed(limit=0)).items) == 1


# --- saving ------------------------------------------------------------------------


async def test_saving_twice_counts_once(store) -> None:
    """The client is a phone with a flaky connection; a retry must not double-count."""
    shared = await store.publish(publication(), AUTHOR, AUTHOR_NAME)

    await store.set_saved(shared.id, "reader-1", True)
    saved, count = await store.set_saved(shared.id, "reader-1", True)

    assert saved is True
    assert count == 1


async def test_unsaving_something_never_saved_is_not_an_error(store) -> None:
    shared = await store.publish(publication(), AUTHOR, AUTHOR_NAME)

    saved, count = await store.set_saved(shared.id, "reader-1", False)

    assert saved is False
    assert count == 0


async def test_the_count_is_across_people_and_the_flag_is_about_you(store) -> None:
    shared = await store.publish(publication(), AUTHOR, AUTHOR_NAME)
    await store.set_saved(shared.id, "reader-1", True)
    await store.set_saved(shared.id, "reader-2", True)

    seen = await store.get(shared.id, viewer_id="reader-3")

    assert seen is not None
    assert seen.save_count == 2
    assert seen.saved_by_viewer is False


async def test_saving_a_plan_that_is_gone_is_reported_not_invented(store) -> None:
    assert await store.set_saved("no-such-plan", "reader-1", True) is None


# --- withdrawing -------------------------------------------------------------------


async def test_an_author_can_take_their_own_plan_down(store) -> None:
    shared = await store.publish(publication(), AUTHOR, AUTHOR_NAME)

    assert await store.withdraw(shared.id, AUTHOR) is True
    assert await store.get(shared.id) is None
    assert (await store.feed()).items == []


async def test_someone_else_cannot(store) -> None:
    shared = await store.publish(publication(), AUTHOR, AUTHOR_NAME)

    assert await store.withdraw(shared.id, "account-2") is False
    assert await store.get(shared.id) is not None


async def test_withdrawing_takes_the_saves_with_it(store) -> None:
    """Otherwise a later plan reusing the id would inherit strangers' saves, and the rows
    would accumulate for the life of the database with nothing pointing at them."""
    shared = await store.publish(publication(), AUTHOR, AUTHOR_NAME)
    await store.set_saved(shared.id, "reader-1", True)
    await store.withdraw(shared.id, AUTHOR)

    again = await store.publish(publication(), AUTHOR, AUTHOR_NAME)
    seen = await store.get(again.id)

    assert seen is not None
    assert seen.save_count == 0


# --- over HTTP, with real tokens ---------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """A real app with the feed and the accounts pointed at scratch databases."""
    from app import auth, main

    monkeypatch.setattr(main, "community", CommunityStore(tmp_path / "http.db"))
    accounts = AuthStore(tmp_path / "auth.db")
    monkeypatch.setattr(main, "auth_store", accounts)
    # `viewer` closes over the module-level store, so both names have to move.
    monkeypatch.setattr(auth, "store", accounts)
    return TestClient(app)


def sign_up(client: TestClient, username: str) -> dict[str, str]:
    created = client.post(
        "/auth/register",
        json={"username": username, "password": "correct horse battery", "display_name": username},
    )
    assert created.status_code == 201, created.text
    return {"Authorization": f"Bearer {created.json()['token']}"}


def test_publish_then_read_it_back(client) -> None:
    headers = sign_up(client, "yuxia")
    created = client.post("/community/plans", json={"itinerary": PLAN}, headers=headers)
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]

    # Readable without signing in at all.
    feed = client.get("/community/plans")
    assert feed.status_code == 200
    assert [item["id"] for item in feed.json()["items"]] == [plan_id]

    detail = client.get(f"/community/plans/{plan_id}")
    assert detail.status_code == 200
    assert detail.json()["itinerary"]["destination"] == "Chicago"


def test_the_byline_is_the_account_not_whatever_was_sent(client) -> None:
    """Sending an author is not merely ignored -- the field does not exist, so a client
    that tries is told its request is malformed rather than quietly attributed."""
    headers = sign_up(client, "yuxia")

    created = client.post(
        "/community/plans",
        json={"itinerary": PLAN, "author_id": "somebody-else", "author_name": "Mei"},
        headers=headers,
    )

    assert created.status_code == 201, created.text
    assert created.json()["author_name"] == "yuxia"
    assert created.json()["author_id"] != "somebody-else"


def test_publishing_without_a_token_is_refused(client) -> None:
    refused = client.post("/community/plans", json={"itinerary": PLAN})

    assert refused.status_code == 401
    assert refused.headers.get("WWW-Authenticate") == "Bearer"


def test_a_bad_token_is_refused_like_no_token(client) -> None:
    for header in ({"Authorization": "Bearer nonsense"}, {"Authorization": "Basic abc"}):
        assert (
            client.post("/community/plans", json={"itinerary": PLAN}, headers=header).status_code
            == 401
        )


def test_saving_requires_a_token_and_counts_for_that_account(client) -> None:
    author = sign_up(client, "yuxia")
    plan_id = client.post("/community/plans", json={"itinerary": PLAN}, headers=author).json()["id"]

    assert client.post(f"/community/plans/{plan_id}/save", json={}).status_code == 401

    reader = sign_up(client, "meilin")
    saved = client.post(f"/community/plans/{plan_id}/save", json={}, headers=reader)

    assert saved.status_code == 200
    assert saved.json() == {"saved": True, "save_count": 1}


def test_one_reader_cannot_pad_the_count(client) -> None:
    """The whole reason the save body has no user_id: with one, an author could inflate
    their own post by inventing readers."""
    author = sign_up(client, "yuxia")
    plan_id = client.post("/community/plans", json={"itinerary": PLAN}, headers=author).json()["id"]

    for invented in ("reader-a", "reader-b", "reader-c"):
        client.post(
            f"/community/plans/{plan_id}/save",
            json={"user_id": invented},
            headers=author,
        )

    assert client.get(f"/community/plans/{plan_id}").json()["save_count"] == 1


def test_saved_by_viewer_follows_the_token(client) -> None:
    author = sign_up(client, "yuxia")
    plan_id = client.post("/community/plans", json={"itinerary": PLAN}, headers=author).json()["id"]
    reader = sign_up(client, "meilin")
    client.post(f"/community/plans/{plan_id}/save", json={}, headers=reader)

    def first(headers=None) -> dict:
        return client.get("/community/plans", headers=headers).json()["items"][0]

    assert first(reader)["saved_by_viewer"] is True
    assert first(author)["saved_by_viewer"] is False
    assert first()["saved_by_viewer"] is None


def test_only_the_author_can_withdraw(client) -> None:
    author = sign_up(client, "yuxia")
    plan_id = client.post("/community/plans", json={"itinerary": PLAN}, headers=author).json()["id"]
    other = sign_up(client, "meilin")

    assert client.delete(f"/community/plans/{plan_id}").status_code == 401
    assert client.delete(f"/community/plans/{plan_id}", headers=other).status_code == 404
    assert client.delete(f"/community/plans/{plan_id}", headers=author).status_code == 204


def test_a_plan_that_is_gone_is_a_404_not_an_empty_card(client) -> None:
    headers = sign_up(client, "yuxia")

    assert client.get("/community/plans/no-such-plan").status_code == 404
    assert (
        client.post("/community/plans/no-such-plan/save", json={}, headers=headers).status_code
        == 404
    )


# --- paging and search --------------------------------------------------------------


async def published(store: CommunityStore, count: int, **overrides) -> list[str]:
    """`count` plans, oldest first, returning their ids in publication order."""
    ids = []
    for index in range(count):
        shared = await store.publish(
            publication(note=f"trip {index}", **overrides), AUTHOR, AUTHOR_NAME
        )
        ids.append(shared.id)
    return ids


async def walk(store: CommunityStore, page_size: int, **kwargs) -> list[str]:
    """Every id the feed yields, one page at a time."""
    seen: list[str] = []
    cursor = None
    while True:
        page = await store.feed(limit=page_size, cursor=cursor, **kwargs)
        seen += [item.id for item in page.items]
        cursor = page.next_cursor
        if cursor is None:
            return seen


async def test_a_page_says_where_it_ends(store) -> None:
    await published(store, 5)

    page = await store.feed(limit=2)

    assert len(page.items) == 2
    assert page.next_cursor is not None


async def test_the_last_page_says_so(store) -> None:
    """Null rather than a cursor that would fetch nothing: a client cannot tell an empty
    page from a slow one, so it would keep asking."""
    await published(store, 2)

    page = await store.feed(limit=5)

    assert len(page.items) == 2
    assert page.next_cursor is None


async def test_a_full_page_that_is_exactly_the_end_is_still_the_end(store) -> None:
    """Off-by-one country: with exactly `limit` rows left there is no next page, and
    saying there is costs an extra round trip that returns nothing."""
    await published(store, 4)

    page = await store.feed(limit=4)

    assert page.next_cursor is None


async def test_walking_the_pages_sees_everything_once(store) -> None:
    ids = await published(store, 7)

    walked = await walk(store, page_size=3)

    assert walked == list(reversed(ids))


async def test_publishing_mid_read_does_not_duplicate_or_skip(store) -> None:
    """The reason for cursors rather than an offset. With `OFFSET 3`, one plan published
    between pages pushes everything down by one: the reader sees an item twice and never
    sees another."""
    ids = await published(store, 6)

    first = await store.feed(limit=3)
    interloper = await store.publish(publication(note="posted mid-read"), AUTHOR, AUTHOR_NAME)
    second = await store.feed(limit=3, cursor=first.next_cursor)

    seen = [item.id for item in first.items] + [item.id for item in second.items]
    assert seen == list(reversed(ids))
    assert interloper.id not in seen
    assert len(seen) == len(set(seen))


async def test_plans_published_in_the_same_instant_still_have_an_order(store) -> None:
    """`created_at` alone is not unique. Ordering by it alone leaves ties undefined, and a
    cursor landing on a tie repeats one row and skips the other."""
    ids = await published(store, 6)
    with store._connect() as connection:
        connection.execute("UPDATE shared_plans SET created_at = ?", ("2026-08-18T00:00:00+00:00",))

    walked = await walk(store, page_size=2)

    assert sorted(walked) == sorted(ids)
    assert len(walked) == len(set(walked))


async def test_a_cursor_this_service_did_not_issue_is_refused(store) -> None:
    """Ignoring it would show a client bug as an endless first page."""
    with pytest.raises(ValueError):
        await store.feed(cursor="not-a-cursor")


async def test_searching_by_destination(store) -> None:
    await store.publish(publication(), AUTHOR, AUTHOR_NAME)
    await store.publish(
        publication(itinerary=Itinerary.model_validate({**PLAN, "destination": "New Orleans"})),
        AUTHOR,
        AUTHOR_NAME,
    )

    found = await store.feed(destination="orlea")

    assert [item.destination for item in found.items] == ["New Orleans"]


async def test_the_search_is_case_insensitive(store) -> None:
    await store.publish(publication(), AUTHOR, AUTHOR_NAME)

    assert len((await store.feed(destination="CHICAGO")).items) == 1
    assert len((await store.feed(destination="chicago")).items) == 1


async def test_a_wildcard_is_searched_for_not_obeyed(store) -> None:
    """Unescaped, `%` matches every trip and `_` matches any character -- so the filter
    quietly stops filtering exactly when someone types punctuation."""
    await published(store, 3)

    assert (await store.feed(destination="%")).items == []
    assert (await store.feed(destination="Chi_ago")).items == []


async def test_search_and_paging_compose(store) -> None:
    await published(store, 4)
    await store.publish(
        publication(itinerary=Itinerary.model_validate({**PLAN, "destination": "New Orleans"})),
        AUTHOR,
        AUTHOR_NAME,
    )

    walked = await walk(store, page_size=2, destination="Chicago")

    assert len(walked) == 4


async def test_an_empty_search_is_not_a_filter(store) -> None:
    await published(store, 3)

    assert len((await store.feed(destination="   ")).items) == 3


def test_a_bad_cursor_over_http_is_a_422(client) -> None:
    assert client.get("/community/plans?cursor=nonsense").status_code == 422
