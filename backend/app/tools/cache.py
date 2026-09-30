"""Small in-process TTL cache for successful, read-only tool results.

This is intentionally not Redis. Tool facts only need reuse across nearby planning
requests in one worker, and a bounded local cache has no deployment or consistency
surface. Durable memory writes are never eligible. Failures are not shared across runs:
one timeout must not make the next traveller inherit an outage.
"""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from time import monotonic


@dataclass(frozen=True)
class CachedToolResult:
    content: str
    ok: bool
    code: str | None
    error: str | None
    evidence: tuple[str, ...] = ()
    collected_at: str = ""


@dataclass(frozen=True)
class CacheHit:
    value: CachedToolResult
    age_seconds: float


class TTLToolCache:
    """Thread-safe, bounded LRU with per-entry TTLs."""

    def __init__(self, max_entries: int = 512) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, float, CachedToolResult]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: str) -> CacheHit | None:
        now = monotonic()
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            created_at, expires_at, value = item
            if expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return CacheHit(value=value, age_seconds=max(0.0, now - created_at))

    def put(self, key: str, value: CachedToolResult, ttl_seconds: float) -> None:
        if ttl_seconds <= 0 or not value.ok:
            return
        now = monotonic()
        with self._lock:
            self._entries[key] = (now, now + ttl_seconds, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


def collected_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


shared_tool_cache = TTLToolCache()
