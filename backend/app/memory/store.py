"""Durable user preferences.

What the agent learned about someone that is worth carrying into the next trip:
"avoids hiking", "travels with a toddler", "vegetarian". Not conversation history --
that would grow without bound and mostly repeat itself.

**Storage is SQLite via the standard library**, run in a worker thread. Phase 4 moves
this to PostgreSQL; the swap is behind `PreferenceStore`, so nothing above it changes.
Using SQLAlchemy now would mean the same behaviour with more dependencies and more
code to throw away.

**Dedup happens twice.** `(user_id, text)` is the primary key, so remembering something
verbatim is a no-op at the database level rather than a check-then-insert race. On top
of that, a near-duplicate check catches the restatements a model actually produces --
"loves museums" after "loves museum", "avoids hiking." after "avoids hiking" -- which
are different strings and the same fact.
"""

import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Long enough for a real preference, short enough that a model cannot smuggle an essay
# into the system prompt of every future run.
MAX_PREFERENCE_CHARS = 120

# The prompt budget matters more than completeness: the most recent preferences win.
MAX_PREFERENCES_RECALLED = 20

# Storage cap, distinct from the recall cap: without one the table grows for the life of
# the account even though only the newest 20 are ever read.
MAX_PREFERENCES_STORED = 50

# How alike two preferences must be before the newer one is treated as a restatement.
#
# **Deliberately high, and the asymmetry is the reason.** A missed duplicate costs a few
# prompt tokens against a capped budget. A false one silently discards an *update*, and
# "avoids hiking" -> "loves hiking" is exactly the pair a looser threshold would merge:
# they share a word and mean opposite things. At 0.8 only near-identical wording
# collapses, so contradictions survive to be resolved by recency.
DUPLICATE_SIMILARITY = 0.8

_NON_WORD = re.compile(r"\W+", re.UNICODE)


def _tokens(text: str) -> frozenset[str]:
    """Content words, crudely normalised, for comparing two preferences.

    Trailing "s" is dropped from longer words so "museums" and "museum" match without
    pulling in a stemmer. **CJK text collapses to whole runs rather than words**, so
    this only ever catches exact repeats in those scripts -- one more reason the stored
    preferences were consolidated into English (see decisions.md 2026-08-17).
    """
    words = [word for word in _NON_WORD.split(text.lower()) if word]
    return frozenset(word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words)


def _similarity(left: frozenset[str], right: frozenset[str]) -> float:
    """Jaccard overlap. 1.0 is the same words, 0.0 shares none."""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def is_restatement(candidate: str, existing: list[str]) -> bool:
    """Whether `candidate` says something already known, in different words."""
    tokens = _tokens(candidate)
    return any(_similarity(tokens, _tokens(known)) >= DUPLICATE_SIMILARITY for known in existing)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    user_id    TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, text)
)
"""


@dataclass(frozen=True)
class Preference:
    text: str
    created_at: datetime


class PreferenceStore:
    """Per-user preference storage. Every method is safe to call concurrently."""

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
                    connection.execute(_SCHEMA)

            await asyncio.to_thread(create)
            self._ready = True

    async def recall(self, user_id: str, limit: int = MAX_PREFERENCES_RECALLED) -> list[Preference]:
        """Most recent preferences first. Unknown users simply have none."""
        if not user_id:
            return []
        await self._ensure_schema()

        def query() -> list[tuple[str, str]]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT text, created_at FROM preferences WHERE user_id = ?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            return rows

        rows = await asyncio.to_thread(query)
        return [
            Preference(text=text, created_at=datetime.fromisoformat(created_at))
            for text, created_at in rows
        ]

    async def remember(self, user_id: str, texts: list[str]) -> list[str]:
        """Store preferences, returning the ones that were actually new.

        Silently drops blanks and over-long entries rather than failing the call: this
        runs inside a tool the model invoked, and a malformed suggestion should not
        take down a planning run.
        """
        if not user_id:
            return []
        await self._ensure_schema()

        cleaned = []
        for text in texts:
            trimmed = " ".join(text.split())[:MAX_PREFERENCE_CHARS].strip()
            if trimmed:
                cleaned.append(trimmed)
        if not cleaned:
            return []

        now = datetime.now(UTC).isoformat()

        def insert() -> list[str]:
            stored: list[str] = []
            with self._connect() as connection:
                known = [
                    row[0]
                    for row in connection.execute(
                        "SELECT text FROM preferences WHERE user_id = ?", (user_id,)
                    )
                ]
                for text in cleaned:
                    # Checked against what this batch has already added too, so a model
                    # that says the same thing twice in one call is caught as well.
                    if is_restatement(text, known):
                        logger.info("preference %r already known for %s; skipped", text, user_id)
                        continue
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO preferences (user_id, text, created_at)"
                        " VALUES (?, ?, ?)",
                        (user_id, text, now),
                    )
                    if cursor.rowcount:
                        stored.append(text)
                        known.append(text)

                # Trim oldest first, so a long-lived account keeps what it learned most
                # recently rather than whatever it happened to learn first.
                connection.execute(
                    "DELETE FROM preferences WHERE user_id = ? AND rowid NOT IN ("
                    "  SELECT rowid FROM preferences WHERE user_id = ?"
                    "  ORDER BY created_at DESC, rowid DESC LIMIT ?"
                    ")",
                    (user_id, user_id, MAX_PREFERENCES_STORED),
                )
            return stored

        return await asyncio.to_thread(insert)

    async def forget(self, user_id: str) -> int:
        """Drop everything known about a user. Returns how many rows went."""
        await self._ensure_schema()

        def delete() -> int:
            with self._connect() as connection:
                cursor = connection.execute("DELETE FROM preferences WHERE user_id = ?", (user_id,))
                return cursor.rowcount

        return await asyncio.to_thread(delete)
