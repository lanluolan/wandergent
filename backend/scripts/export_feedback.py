"""Export text-free feedback candidates for human review.

The export is deliberately not an eval file. A user label is evidence to investigate,
not ground truth. Review it, reproduce it, then encode the smallest failing fixture in
`tests/` or a stable live scenario in `evals/cases.py`.
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.config import settings
from app.feedback import FeedbackStore


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, dest="target")
    args = parser.parse_args()
    feedback = await FeedbackStore(settings.feedback_db_path).export()
    target = Path(args.target)
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        target.write_text,
        json.dumps(feedback, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {len(feedback)} text-free candidate(s) to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
