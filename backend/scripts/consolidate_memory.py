"""Rewrite stored preferences: translate to English and drop what is already covered.

A maintenance one-off, kept as a script rather than typed as ad-hoc SQL so the mapping
is reviewable and the operation is repeatable. Dry run by default.

    cd backend
    uv run python -m scripts.consolidate_memory            # show what would change
    uv run python -m scripts.consolidate_memory --apply    # write it, after a backup

Why it was needed: preferences were written in whichever language the traveller used,
so an account accumulated the same fact twice -- "不想爬山" alongside "avoids hiking".
The near-duplicate check in `store.py` cannot see across scripts (CJK text has no word
boundaries to compare), so the cross-language pairs had to be resolved by hand once.
After this, new preferences are written in English and the automatic check keeps up.
"""

import argparse
import asyncio
import shutil
import sqlite3
import sys
from pathlib import Path

from app.config import settings
from app.memory.store import PreferenceStore

#: Old text -> replacement, or None to drop it entirely.
#:
#: Entries are dropped when an English preference already says the same thing; the
#: dedup check cannot match them across scripts, so the judgement is made here instead.
REWRITES: dict[str, str | None] = {
    # Language preference: deliberately gone. The interface is English, and this one
    # overrode it on every request.
    "旅行时使用中文": None,
    # Already covered by the English entries alongside them.
    "不想爬山": None,  # == "avoids hiking"
    "不爬山": None,  # == "avoids hiking"
    "喜欢美食和博物馆": None,  # == "food-focused traveller" + "loves museums"
    "喜欢逛美术馆": None,  # == "loves museums"
    # Nothing English says these yet, so they are translated rather than dropped.
    "喜欢人文历史": "interested in history and culture",
    "避开治安不好的区域": "avoids unsafe neighbourhoods",
    "预算意识强": "budget-conscious",
}


def current(path: str) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT user_id, text, created_at FROM preferences ORDER BY user_id, created_at"
        ).fetchall()


def plan_changes(path: Path) -> tuple[list[tuple[str, str, str]], dict[str, list[str]]]:
    """Read the store and work out the target set per user. No I/O beyond the read."""
    rows = current(str(path))
    planned: dict[str, list[str]] = {}
    for user_id, text, _ in rows:
        replacement = REWRITES.get(text, text)
        if replacement is None:
            continue
        planned.setdefault(user_id, [])
        if replacement not in planned[user_id]:
            planned[user_id].append(replacement)
    return rows, planned


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    parser.add_argument("--db", default=settings.memory_db_path)
    args = parser.parse_args()

    path = Path(args.db)
    if not await asyncio.to_thread(path.exists):
        print(f"no memory database at {path}")
        return 1

    rows, planned = await asyncio.to_thread(plan_changes, path)
    if not rows:
        print("nothing stored")
        return 0

    for user_id in sorted({row[0] for row in rows}):
        before = [text for uid, text, _ in rows if uid == user_id]
        after = planned.get(user_id, [])
        print(f"\nuser {user_id}: {len(before)} -> {len(after)}")
        for text in before:
            mark = "  drop" if REWRITES.get(text, text) is None else "  keep"
            if REWRITES.get(text, text) not in (text, None):
                mark = "  edit"
            print(f"{mark}  {text}")
        if after:
            print("  result:")
            for text in after:
                print(f"    - {text}")

    if not args.apply:
        print("\ndry run; pass --apply to write")
        return 0

    backup = path.with_suffix(path.suffix + ".bak")
    await asyncio.to_thread(shutil.copy, path, backup)
    print(f"\nbackup: {backup}")

    store = PreferenceStore(path)
    for user_id, texts in planned.items():
        await store.forget(user_id)
        # Through the normal path, so the near-duplicate check applies to the result.
        await store.remember(user_id, texts)
    for user_id in {row[0] for row in rows} - set(planned):
        await store.forget(user_id)

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
