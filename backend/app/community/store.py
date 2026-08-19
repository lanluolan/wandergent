"""Shared itineraries: publishing a plan, browsing what others published, saving one.

**Storage is SQLite via the standard library**, in a worker thread, exactly as
`memory/store.py` does it -- same reasoning, same Phase 4 swap to PostgreSQL behind the
same kind of seam.

The itinerary is stored as one JSON column rather than shredded into day and activity
tables. It is a document that is always read whole and never queried by its parts, so
normalising it would buy nothing and would couple this schema to every change in the
agent's models. The handful of fields the feed sorts and displays are denormalised
alongside it, derived once at publish time, so listing thirty plans never parses thirty
itineraries.

**Identity comes from the bearer token, never from the request** (as of 2026-08-17;
before that it was a claim the client made, and anyone could post as anyone). `author_id`
here is always an account id the server resolved -- see `app/auth/`. `PublishRequest`
carries no author field at all, which is what makes that true rather than merely intended.

**There is still no moderation of any kind**, and no rate limiting: an account can be
registered for free and used to post immediately. Authentication fixes impersonation and
nothing else.
"""

import asyncio
import base64
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from app.agent.schemas import Itinerary

logger = logging.getLogger(__name__)

#: Room for a sentence about the trip, not an essay. Same reasoning as preferences: a
#: field with no ceiling is a field someone will fill.
MAX_NOTE_CHARS = 240

#: Per-author cap, so one account cannot crowd out the feed. Trimming takes the oldest,
#: so the post someone just made is never the one refused.
MAX_SHARED_PER_AUTHOR = 20

#: Feed page size. The client asks for what it shows; this is the ceiling.
MAX_FEED = 100
DEFAULT_FEED = 30

_SCHEMA = (
    """
CREATE TABLE IF NOT EXISTS shared_plans (
    id          TEXT PRIMARY KEY,
    author_id   TEXT NOT NULL,
    author_name TEXT NOT NULL,
    note        TEXT NOT NULL,
    request     TEXT NOT NULL,
    plan_json   TEXT NOT NULL,
    destination TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    day_count   INTEGER NOT NULL,
    total_cost  REAL NOT NULL,
    currency    TEXT NOT NULL,
    created_at  TEXT NOT NULL
)
""",
    # (plan_id, user_id) as the key makes saving twice a no-op at the database level
    # rather than a check-then-insert race, the same trick the preference table uses.
    """
CREATE TABLE IF NOT EXISTS plan_saves (
    plan_id  TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    PRIMARY KEY (plan_id, user_id)
)
""",
    "CREATE INDEX IF NOT EXISTS shared_plans_created ON shared_plans (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS shared_plans_author ON shared_plans (author_id)",
)

_COLUMNS = (
    "SELECT s.id, s.author_id, s.author_name, s.note, s.request, s.destination,"
    " s.start_date, s.end_date, s.day_count, s.total_cost, s.currency, s.created_at,"
    " (SELECT COUNT(*) FROM plan_saves v WHERE v.plan_id = s.id),"
    " (SELECT COUNT(*) FROM plan_saves v WHERE v.plan_id = s.id AND v.user_id = ?)"
)


def _escape_like(text: str) -> str:
    """Neutralise LIKE wildcards in user input.

    Without this a search for "%" matches every trip and "_" matches any single character,
    so the filter quietly stops filtering. `!` is the escape character rather than the
    conventional backslash purely so neither the Python literal nor the SQL string needs
    to double it.
    """
    return text.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _encode_cursor(created_at: str, plan_id: str) -> str:
    """Opaque position marker: base64 of the row's sort key.

    Base64 rather than the raw values so a client cannot hand-build one and end up
    depending on the ordering columns, which are free to change.
    """
    return base64.urlsafe_b64encode(f"{created_at}|{plan_id}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str] | None:
    """The (created_at, id) a cursor points at, or None if it is not one of ours."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_at, _, plan_id = base64.urlsafe_b64decode(padded).decode().partition("|")
    except (ValueError, UnicodeDecodeError):
        return None
    return (created_at, plan_id) if created_at and plan_id else None


class SharedPlanSummary(BaseModel):
    """One card in the feed. Deliberately without the itinerary; see the module docstring."""

    id: str
    author_id: str
    author_name: str
    note: str
    request: str
    destination: str
    start_date: str
    end_date: str
    day_count: int
    total_cost: float
    currency: str
    created_at: datetime
    save_count: int = 0
    #: Whether the *caller* has already saved this. Null when no viewer was given, which
    #: is different from False and must not render as an empty heart.
    saved_by_viewer: bool | None = None


class SharedPlan(SharedPlanSummary):
    """A shared plan opened in full."""

    itinerary: Itinerary


class Feed(BaseModel):
    """One page of the feed.

    An envelope rather than a bare list because a page has to carry where it *ends*.
    Without that the client has only "how many did I skip", which is offset paging -- and
    offset paging on a feed that grows at the top shows duplicates and skips items every
    time somebody posts while you are reading.
    """

    items: list[SharedPlanSummary] = []
    #: Pass back as `cursor` for the next page. Null means this is the last one.
    next_cursor: str | None = None


class PublishRequest(BaseModel):
    """What the client sends to share a plan.

    Two kinds of field are absent on purpose. The summary fields (destination, dates,
    totals) are derived from the itinerary at publish time, so a caller cannot advertise a
    trip as somewhere it is not. And there is **no author field at all** -- the author is
    whoever the bearer token says it is. While `author_id` was in this model, every check
    on it was a formality.
    """

    request: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)
    itinerary: Itinerary


class CommunityStore:
    """Shared plans and who saved them. Every method is safe to call concurrently."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._init_lock = asyncio.Lock()
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return

            def create() -> None:
                with self._connect() as connection:
                    for statement in _SCHEMA:
                        connection.execute(statement)

            await asyncio.to_thread(create)
            self._ready = True

    async def publish(
        self, payload: PublishRequest, author_id: str, author_name: str
    ) -> SharedPlan:
        """Share a plan, returning it as the feed will show it.

        The author is passed separately from the payload because it comes from a different
        place: the token, not the request body. Keeping them apart in the signature is what
        makes it impossible to accidentally read it off the wire again.
        """
        await self._ensure_schema()
        plan = payload.itinerary
        record = SharedPlan(
            id=str(uuid.uuid4()),
            author_id=author_id,
            author_name=author_name.strip() or "Traveller",
            note=payload.note.strip(),
            request=payload.request.strip(),
            destination=plan.destination,
            start_date=plan.start_date.isoformat(),
            end_date=plan.end_date.isoformat(),
            day_count=len(plan.days),
            total_cost=plan.total_estimated_cost,
            currency=plan.currency,
            created_at=datetime.now(UTC),
            save_count=0,
            saved_by_viewer=False,
            itinerary=plan,
        )

        def insert() -> None:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO shared_plans (id, author_id, author_name, note, request,"
                    " plan_json, destination, start_date, end_date, day_count, total_cost,"
                    " currency, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.id,
                        record.author_id,
                        record.author_name,
                        record.note,
                        record.request,
                        plan.model_dump_json(),
                        record.destination,
                        record.start_date,
                        record.end_date,
                        record.day_count,
                        record.total_cost,
                        record.currency,
                        record.created_at.isoformat(),
                    ),
                )
                # Trim this author back to the cap, oldest first. Done after the insert
                # so the post someone just made is never the one refused.
                connection.execute(
                    "DELETE FROM shared_plans WHERE id IN ("
                    "  SELECT id FROM shared_plans WHERE author_id = ?"
                    "  ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                    (record.author_id, MAX_SHARED_PER_AUTHOR),
                )

        await asyncio.to_thread(insert)
        logger.info("shared plan %s (%s) by %s", record.id, record.destination, author_id)
        return record

    async def feed(
        self,
        viewer_id: str = "",
        limit: int = DEFAULT_FEED,
        cursor: str | None = None,
        destination: str = "",
    ) -> Feed:
        """One page, newest first. Unknown viewers simply have saved nothing.

        Ordered by `(created_at, id)` rather than `created_at` alone: two plans published
        in the same millisecond would otherwise have no defined order between them, and a
        cursor sitting on that boundary could repeat one and skip the other.
        """
        await self._ensure_schema()
        capped = max(1, min(limit, MAX_FEED))

        where: list[str] = []
        params: list = [viewer_id]
        if cursor:
            position = _decode_cursor(cursor)
            if position is None:
                raise ValueError("that is not a valid cursor")
            where.append("(s.created_at < ? OR (s.created_at = ? AND s.id < ?))")
            params += [position[0], position[0], position[1]]
        if destination.strip():
            # Substring, case-insensitive for ASCII via SQLite's default LIKE. Matching
            # the destination only, not the note or the request: the note is the author's
            # prose and the request may not be published at all.
            where.append("s.destination LIKE ? ESCAPE '!'")
            params.append(f"%{_escape_like(destination.strip())}%")

        clause = f" WHERE {' AND '.join(where)}" if where else ""
        # One more than asked for, so "is there a next page" is answered by the query
        # rather than by a second COUNT that can disagree with it.
        params.append(capped + 1)

        def query() -> list[tuple]:
            with self._connect() as connection:
                return connection.execute(
                    f"{_COLUMNS} FROM shared_plans s{clause}"
                    " ORDER BY s.created_at DESC, s.id DESC LIMIT ?",
                    tuple(params),
                ).fetchall()

        rows = await asyncio.to_thread(query)
        has_more = len(rows) > capped
        rows = rows[:capped]
        items = [_summary(row, viewer_id) for row in rows]
        next_cursor = _encode_cursor(rows[-1][11], rows[-1][0]) if (has_more and rows) else None
        return Feed(items=items, next_cursor=next_cursor)

    async def get(self, plan_id: str, viewer_id: str = "") -> SharedPlan | None:
        """One shared plan in full, or None if it is not there or was withdrawn."""
        await self._ensure_schema()

        def query() -> tuple | None:
            with self._connect() as connection:
                return connection.execute(
                    f"{_COLUMNS}, s.plan_json FROM shared_plans s WHERE s.id = ?",
                    (viewer_id, plan_id),
                ).fetchone()

        row = await asyncio.to_thread(query)
        if row is None:
            return None
        try:
            itinerary = Itinerary.model_validate_json(row[14])
        except ValueError:
            # A stored plan that no longer parses means the schema moved under it, not
            # that the caller did anything wrong. Hiding one row beats failing the read.
            logger.warning("shared plan %s no longer parses against the current schema", plan_id)
            return None
        return SharedPlan(**_summary(row, viewer_id).model_dump(), itinerary=itinerary)

    async def set_saved(self, plan_id: str, user_id: str, saved: bool) -> tuple[bool, int] | None:
        """Save or unsave, returning (saved, save_count). None if the plan is gone.

        Idempotent both ways: saving twice is one save, and unsaving something never
        saved is not an error. The client is a phone with a flaky connection, and a
        retry must not double-count.
        """
        await self._ensure_schema()

        def write() -> tuple[bool, int] | None:
            with self._connect() as connection:
                if (
                    connection.execute(
                        "SELECT 1 FROM shared_plans WHERE id = ?", (plan_id,)
                    ).fetchone()
                    is None
                ):
                    return None
                if saved:
                    connection.execute(
                        "INSERT OR IGNORE INTO plan_saves (plan_id, user_id, saved_at)"
                        " VALUES (?,?,?)",
                        (plan_id, user_id, datetime.now(UTC).isoformat()),
                    )
                else:
                    connection.execute(
                        "DELETE FROM plan_saves WHERE plan_id = ? AND user_id = ?",
                        (plan_id, user_id),
                    )
                count = connection.execute(
                    "SELECT COUNT(*) FROM plan_saves WHERE plan_id = ?", (plan_id,)
                ).fetchone()[0]
            return saved, int(count)

        return await asyncio.to_thread(write)

    async def withdraw(self, plan_id: str, author_id: str) -> bool:
        """Take a plan down. False when it is not there or not this author's.

        The author check is a claim, not proof -- see the module docstring -- but it is
        made anyway, so the intended rule is written down and a real token slots in
        later without changing a single caller.
        """
        await self._ensure_schema()

        def delete() -> bool:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM shared_plans WHERE id = ? AND author_id = ?",
                    (plan_id, author_id),
                )
                if cursor.rowcount:
                    connection.execute("DELETE FROM plan_saves WHERE plan_id = ?", (plan_id,))
                return bool(cursor.rowcount)

        return await asyncio.to_thread(delete)


def _summary(row: tuple, viewer_id: str) -> SharedPlanSummary:
    return SharedPlanSummary(
        id=row[0],
        author_id=row[1],
        author_name=row[2],
        note=row[3],
        request=row[4],
        destination=row[5],
        start_date=row[6],
        end_date=row[7],
        day_count=row[8],
        total_cost=row[9],
        currency=row[10],
        created_at=datetime.fromisoformat(row[11]),
        save_count=int(row[12]),
        # Null rather than False when nobody is viewing: "not saved" and "we do not know
        # who is asking" are different, and only one of them should draw an empty heart.
        saved_by_viewer=bool(row[13]) if viewer_id else None,
    )


__all__ = [
    "CommunityStore",
    "Feed",
    "PublishRequest",
    "SharedPlan",
    "SharedPlanSummary",
]
