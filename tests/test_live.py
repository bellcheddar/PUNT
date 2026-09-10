"""The live loop.

Driven synchronously through `poll_once` rather than by starting the thread and
sleeping: a test that waits on a background poller is slow when it passes and
flaky when it does not.
"""

from __future__ import annotations

import queue

from config import DEMO_RECORDING
from engine.events import EventEngine
from engine.live import LiveFeed
from espn.cache import TTLCache
from espn.client import EspnClient, LeagueRepository
from espn.replay import ReplayTransport


def make_feed(draws: int = 50) -> tuple[LiveFeed, ReplayTransport, EspnClient]:
    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    repo = LeagueRepository(client)

    def fetch():
        client.cache.invalidate()
        return repo.snapshot()

    return LiveFeed(fetch=fetch, poll_seconds=30, engine=EventEngine(simulate_draws=draws)), transport, client


def test_polling_produces_moments_and_buffers_them(no_network):
    feed, transport, _ = make_feed()
    for position in range(3 * 3600, 5 * 3600, 300):
        transport.clock.seek(position)
        feed.poll_once()

    assert feed.polls == 24
    assert feed.moments, "two hours of a Sunday produced no moments"
    assert feed.recent(5) == list(reversed(list(feed.moments)[-5:]))


def test_a_listener_receives_the_backlog_then_live_events(no_network):
    feed, transport, _ = make_feed()
    transport.clock.seek(3 * 3600)
    feed.poll_once()
    transport.clock.seek(4 * 3600)
    feed.poll_once()

    stream = feed.listen()
    # A phone unlocking mid-afternoon should find the feed populated rather than
    # an empty panel waiting for the next thing to happen.
    first = next(stream)
    assert first["event"] == "moment" and first["replayed"] is True

    transport.clock.seek(4 * 3600 + 600)
    feed.poll_once()
    events = []
    for _ in range(40):
        try:
            events.append(next(stream))
        except StopIteration:
            break
        if events[-1]["event"] == "tick":
            break
    assert any(e["event"] == "tick" for e in events)


def test_a_dead_listener_is_dropped_rather_than_blocking_the_room(no_network):
    """One phone in a pocket must not stop everybody else's scores updating."""
    from engine.live import LISTENER_BACKLOG, Listener

    feed, transport, _ = make_feed()
    dead = Listener()
    with feed._lock:
        feed._listeners.add(dead)

    for _ in range(LISTENER_BACKLOG + 20):
        feed._broadcast({"event": "tick", "data": {}})

    assert dead.dropped >= 20
    assert dead.queue.qsize() == LISTENER_BACKLOG


def test_a_failing_poll_does_not_kill_the_loop(no_network):
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise RuntimeError("ESPN is down")

    feed = LiveFeed(fetch=flaky, poll_seconds=30, engine=EventEngine(simulate_draws=10))
    try:
        feed.poll_once()
    except RuntimeError:
        pass  # poll_once propagates; the thread's _run is what swallows it
    assert calls["n"] == 1
    assert feed.polls == 0
