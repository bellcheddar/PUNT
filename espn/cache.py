"""A TTL cache with single-flight semantics.

Ten phones in a bar must produce one upstream poll, not ten. A plain TTL cache
does not achieve that: when an entry expires, every request that arrives in the
window before the first refill completes sees a miss and goes upstream, so the
moment of highest concurrency is exactly the moment the cache stops working.

`TTLCache.get_or_set` therefore holds a per-key lock across the fetch. The first
caller fetches; the rest block on the lock and are served the result it stored.

Serving stale data beats serving an error, so a fetch that raises returns the
expired value with `stale=True` when there is one, and only propagates when the
cache is genuinely empty.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


#: Seconds between "serving stale" warnings for one key.
STALE_LOG_EVERY = 60.0


@dataclass
class Entry(Generic[T]):
    value: T
    stored_at: float
    ttl: float

    @property
    def age(self) -> float:
        return time.monotonic() - self.stored_at

    @property
    def expired(self) -> bool:
        return self.age > self.ttl


@dataclass
class Result(Generic[T]):
    """A cache read, plus how much to trust it."""

    value: T
    age: float
    stale: bool = False
    error: str = ""


class TTLCache:
    def __init__(self) -> None:
        self._entries: dict[str, Entry[Any]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.upstream_calls = 0
        #: key -> when a stale serve was last logged. See `STALE_LOG_EVERY`.
        self._warned: dict[str, float] = {}

    def _lock_for(self, key: str) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    def peek(self, key: str) -> Entry[Any] | None:
        with self._guard:
            return self._entries.get(key)

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._guard:
            self._entries[key] = Entry(value=value, stored_at=time.monotonic(), ttl=ttl)

    def invalidate(self, key: str | None = None) -> None:
        with self._guard:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)

    def get_or_set(self, key: str, ttl: float, fetch: Callable[[], T]) -> Result[T]:
        entry = self.peek(key)
        if entry is not None and not entry.expired:
            self.hits += 1
            return Result(value=entry.value, age=entry.age)

        lock = self._lock_for(key)
        with lock:
            # Re-check under the lock: while we waited, the caller that held it
            # may already have refilled this key. This is the whole point of the
            # exercise -- without it, ten blocked requests become ten fetches.
            entry = self.peek(key)
            if entry is not None and not entry.expired:
                self.hits += 1
                return Result(value=entry.value, age=entry.age)

            self.misses += 1
            try:
                self.upstream_calls += 1
                value = fetch()
            except Exception as exc:  # noqa: BLE001 - any upstream failure, deliberately
                if entry is not None:
                    # Once a minute per key, not once per request. During an
                    # ESPN backoff every panel on every phone lands here, and
                    # the first real Sunday's log was a wall of this one line
                    # hiding the handful that said anything new.
                    now = time.monotonic()
                    if now - self._warned.get(key, -STALE_LOG_EVERY) >= STALE_LOG_EVERY:
                        self._warned[key] = now
                        log.warning("fetch failed for %s, serving stale (%.0fs old): %s",
                                    key, entry.age, exc)
                    return Result(value=entry.value, age=entry.age, stale=True, error=str(exc))
                raise
            self.set(key, value, ttl)
            return Result(value=value, age=0.0)

    def stats(self) -> dict[str, Any]:
        with self._guard:
            keys = {k: round(e.age, 1) for k, e in self._entries.items()}
        total = self.hits + self.misses
        return {
            "entries": keys,
            "hits": self.hits,
            "misses": self.misses,
            "upstream_calls": self.upstream_calls,
            "hit_rate": round(self.hits / total, 3) if total else None,
        }
