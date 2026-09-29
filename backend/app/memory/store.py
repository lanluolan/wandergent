"""Durable user preferences.

What the agent learned about someone that is worth carrying into the next trip:
"avoids hiking", "travels with a toddler", "vegetarian". Not conversation history --
that would grow without bound and mostly repeat itself.

**Storage is SQLite via the standard library**, in a worker thread. Phase 4 moves this to
PostgreSQL behind `PreferenceStore`, so nothing above it changes.

**Dedup happens twice.** `(user_id, text)` is the primary key, so remembering something
verbatim is a database-level no-op rather than a check-then-insert race. A near-duplicate
check then catches the restatements a model actually produces -- "loves museums" after
"loves museum" -- different strings, one fact.
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

# How alike two preferences must be before the newer one counts as a restatement.
#
# **High on purpose**, because the errors are not symmetric: a missed duplicate costs a few
# prompt tokens, a false one silently discards an *update*. "avoids hiking" -> "loves
# hiking" share a word and mean opposite things, so only near-identical wording collapses
# and contradictions survive to be resolved by recency.
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
    preference_key TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, text)
)
"""


@dataclass(frozen=True)
class Preference:
    text: str
    created_at: datetime
    key: str | None = None


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
                    columns = {
                        row[1] for row in connection.execute("PRAGMA table_info(preferences)")
                    }
                    if "preference_key" not in columns:
                        connection.execute("ALTER TABLE preferences ADD COLUMN preference_key TEXT")
                    connection.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS preferences_user_key "
                        "ON preferences(user_id, preference_key) "
                        "WHERE preference_key IS NOT NULL"
                    )

            await asyncio.to_thread(create)
            self._ready = True

    async def recall(self, user_id: str, limit: int = MAX_PREFERENCES_RECALLED) -> list[Preference]:
        """Most recent preferences first. Unknown users simply have none."""
        if not user_id:
            return []
        await self._ensure_schema()

        def query() -> list[tuple[str, str, str | None]]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT text, created_at, preference_key FROM preferences WHERE user_id = ?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            return rows

        rows = await asyncio.to_thread(query)
        return [
            Preference(text=text, created_at=datetime.fromisoformat(created_at), key=key)
            for text, created_at, key in rows
        ]

    async def remember(
        self,
        user_id: str,
        texts: list[str],
        *,
        keys: list[str | None] | None = None,
    ) -> list[str]:
        """Store preferences, returning the ones that were actually new.

        Silently drops blanks and over-long entries rather than failing the call: this
        runs inside a tool the model invoked, and a malformed suggestion should not
        take down a planning run.
        """
        if not user_id:
            return []
        await self._ensure_schema()

        supplied_keys = keys or [None] * len(texts)
        cleaned: list[tuple[str, str | None]] = []
        for index, text in enumerate(texts):
            trimmed = " ".join(text.split())[:MAX_PREFERENCE_CHARS].strip()
            if trimmed:
                key = supplied_keys[index] if index < len(supplied_keys) else None
                cleaned.append((trimmed, key))
        if not cleaned:
            return []

        now = datetime.now(UTC).isoformat()

        def insert() -> list[str]:
            stored: list[str] = []
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                known = [
                    row[0]
                    for row in connection.execute(
                        "SELECT text FROM preferences WHERE user_id = ?", (user_id,)
                    )
                ]
                for text, key in cleaned:
                    if key:
                        existing = connection.execute(
                            "SELECT text FROM preferences WHERE user_id = ? AND preference_key = ?",
                            (user_id, key),
                        ).fetchone()
                        if existing:
                            if existing[0] == text:
                                continue
                            # One semantic slot has one current value. This is the
                            # correction/override path; the old value does not remain in
                            # recall to contradict the new one.
                            connection.execute(
                                "DELETE FROM preferences "
                                "WHERE user_id = ? AND (preference_key = ? OR text = ?)",
                                (user_id, key, text),
                            )
                            connection.execute(
                                "INSERT INTO preferences "
                                "(user_id, text, preference_key, created_at) VALUES (?, ?, ?, ?)",
                                (user_id, text, key, now),
                            )
                            stored.append(text)
                            known = [
                                known_text for known_text in known if known_text != existing[0]
                            ]
                            known.append(text)
                            continue
                        if text in known:
                            # Adopt a key for an exact legacy row without creating a
                            # duplicate or pretending a user-visible preference changed.
                            connection.execute(
                                "UPDATE preferences SET preference_key = ? "
                                "WHERE user_id = ? AND text = ? AND preference_key IS NULL",
                                (key, user_id, text),
                            )
                            continue
                    # Checked against what this batch has already added too, so a model
                    # that says the same thing twice in one call is caught as well.
                    if is_restatement(text, known):
                        logger.info("preference %r already known for %s; skipped", text, user_id)
                        continue
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO preferences "
                        "(user_id, text, preference_key, created_at) VALUES (?, ?, ?, ?)",
                        (user_id, text, key, now),
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

    async def forget_one(self, user_id: str, text: str) -> bool:
        """Delete one exact preference, scoped to its owner."""
        await self._ensure_schema()

        def delete() -> bool:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM preferences WHERE user_id = ? AND text = ?", (user_id, text)
                )
                return cursor.rowcount > 0

        return await asyncio.to_thread(delete)
