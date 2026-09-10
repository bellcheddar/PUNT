"""The cache's contract is about concurrency, so the tests are about concurrency."""

import threading
import time

import pytest

from espn.cache import TTLCache


def test_single_flight_collapses_concurrent_misses():
    """Ten phones, one upstream poll. This is the property the bar depends on."""
    cache = TTLCache()
    calls = []
    started = threading.Barrier(10)

    def slow_fetch():
        calls.append(1)
        time.sleep(0.05)  # long enough that all ten are certainly in flight
        return "payload"

    results = []

    def worker():
        started.wait()
        results.append(cache.get_or_set("live", ttl=30, fetch=slow_fetch))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(calls) == 1, f"{len(calls)} upstream calls for 10 concurrent readers"
    assert all(r.value == "payload" for r in results)
    assert len(results) == 10


def test_expired_entry_is_refetched():
    cache = TTLCache()
    values = iter(["first", "second"])
    fetch = lambda: next(values)  # noqa: E731

    assert cache.get_or_set("k", ttl=0.01, fetch=fetch).value == "first"
    time.sleep(0.02)
    assert cache.get_or_set("k", ttl=0.01, fetch=fetch).value == "second"


def test_failed_refresh_serves_stale_rather_than_raising():
    """ESPN going down mid-Sunday must degrade to a stale banner, not a 500."""
    cache = TTLCache()
    cache.get_or_set("k", ttl=0.01, fetch=lambda: "good")
    time.sleep(0.02)

    def boom():
        raise RuntimeError("502 from ESPN")

    result = cache.get_or_set("k", ttl=0.01, fetch=boom)
    assert result.value == "good"
    assert result.stale is True
    assert "502" in result.error


def test_failure_with_empty_cache_propagates():
    """There is nothing honest to show on a cold cache, so the caller decides."""
    cache = TTLCache()
    with pytest.raises(RuntimeError):
        cache.get_or_set("k", ttl=1, fetch=lambda: (_ for _ in ()).throw(RuntimeError("cold")))
