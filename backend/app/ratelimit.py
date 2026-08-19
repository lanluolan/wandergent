"""Rate limiting, so free-and-instant does not mean unlimited.

Three different things are being protected, and they are worth naming separately because
the cost of an unlimited request differs by orders of magnitude between them:

1. **Money.** `/plan` spends real LLM tokens and Google quota on every call. It is by far
   the most expensive endpoint here and it was completely open -- one signed-in account
   could have burned a month's budget in an afternoon.
2. **Passwords.** PBKDF2 at 600k iterations costs an attacker about 0.2 s per guess, which
   sounds like a defence until you multiply: roughly 400,000 attempts a day against one
   account. That is fatal for a weak password.
3. **The feed.** Registration is free and instant, so "20 posts per author" bounds nothing
   -- an attacker registers more authors.

**In-memory, single process.** A sliding window of hit timestamps per key. This is honest
for one server and **wrong for two**: the counters are per process, so N replicas mean N
times the limit, and a restart forgives everyone. Redis is the Phase 4 answer and this
lives behind `RateLimiter` so swapping it changes nothing above. Saying that here rather
than discovering it during a deploy.

**Failures, not attempts, for login-by-username.** Counting every attempt would let an
attacker lock a victim out of their own account by guessing wrong on purpose, which trades
a brute-force hole for a denial-of-service one.
"""

import logging
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Limit:
    """`count` events allowed per `window` seconds."""

    count: int
    window: float

    def per_hour(self) -> str:
        return f"{self.count} per {round(self.window / 60)} min"


# Money first. A planning run makes several LLM calls plus Places and Routes lookups, so
# this is the one number here that maps directly to a bill.
PLAN = Limit(count=20, window=3600)


#: The ceiling across *everyone*, which is the one that actually bounds the bill.
#:
#: Per-account limits do not: accounts are free and instant, so twenty runs an hour each
#: multiplied by an unbounded number of accounts is an unbounded number of runs. This is
#: the difference between "no single person can run up the bill" and "the bill has a
#: maximum". The count comes from `settings.max_plans_per_day`.
def plan_daily_global() -> Limit:
    return Limit(count=settings.max_plans_per_day, window=86_400)


# Targeted brute force. Ten wrong guesses in a quarter of an hour is far more than a
# person typos and far less than an attack needs.
LOGIN_FAILURES_PER_USER = Limit(count=10, window=900)

# Password spraying: one guess each against many accounts, which the per-user counter
# cannot see because no single account is attacked twice.
LOGIN_PER_ADDRESS = Limit(count=30, window=900)

# Account farming. The per-author post cap is meaningless if authors are free.
REGISTER_PER_ADDRESS = Limit(count=5, window=3600)

# Feed flooding by an account that already exists.
PUBLISH = Limit(count=10, window=3600)

# Reset requests send mail to somebody else's inbox. Without a limit this endpoint is a
# way to have a stranger's address flooded, using the service's own good name to do it.
RESET_PER_ADDRESS = Limit(count=5, window=3600)

# Guessing codes. This, not a per-code counter, is what makes an eight-character code
# safe -- see `auth/store.py` for why the counter was removed.
RESET_CONFIRM_PER_ADDRESS = Limit(count=10, window=900)

# Saves are idempotent and cheap, so this is only a backstop against a loop.
SAVE = Limit(count=120, window=3600)

ALL_LIMITS = (
    PLAN,
    RESET_PER_ADDRESS,
    RESET_CONFIRM_PER_ADDRESS,
    LOGIN_FAILURES_PER_USER,
    LOGIN_PER_ADDRESS,
    REGISTER_PER_ADDRESS,
    PUBLISH,
    SAVE,
)


class RateLimiter:
    """Sliding-window counters keyed by an arbitrary string.

    The clock is injectable so tests can advance it instead of sleeping; a limiter tested
    with real `sleep` is a test suite that gets slower every time a limit is added.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str, limit: Limit) -> float | None:
        """Seconds to wait before this key is allowed again, or None if it is allowed now.

        Checks without recording, because the two are not always the same event: a login
        is *checked* before the password is verified and only *recorded* if it was wrong.
        """
        now = self._clock()
        hits = self._hits[key]
        cutoff = now - limit.window
        while hits and hits[0] <= cutoff:
            hits.popleft()

        if len(hits) >= limit.count:
            return max(0.0, hits[0] + limit.window - now)
        return None

    def record(self, key: str) -> None:
        """Count one event against a key."""
        self._hits[key].append(self._clock())

    def check(self, key: str, limit: Limit) -> float | None:
        """Check and, if allowed, record. The common case.

        A refused request is **not** recorded, so a client that keeps hammering does not
        push its own window further out. The limit is a ceiling on work done, not a
        punishment that compounds.
        """
        retry_after = self.blocked(key, limit)
        if retry_after is None:
            self.record(key)
        return retry_after

    def forget(self, key: str) -> None:
        """Clear one key. Used after a successful login, so a person who eventually
        remembers their password is not still counting against the failure limit."""
        self._hits.pop(key, None)

    def reset(self) -> None:
        """Drop every counter. For tests, and for nothing else."""
        self._hits.clear()

    def prune(self) -> int:
        """Drop keys with no live hits, and report how many went.

        Without this the dictionary grows one entry per address or username ever seen,
        which is a slow memory leak with an attacker-controlled key space.
        """
        now = self._clock()
        # The longest window in use, so a key is only dropped once it cannot matter.
        horizon = max(limit.window for limit in (*ALL_LIMITS, plan_daily_global()))
        stale = [key for key, hits in self._hits.items() if not hits or hits[-1] <= now - horizon]
        for key in stale:
            del self._hits[key]
        return len(stale)


limiter = RateLimiter()
