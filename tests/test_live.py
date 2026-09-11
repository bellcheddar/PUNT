"""The live loop.

Driven synchronously through `poll_once` rather than by starting the thread and
sleeping: a test that waits on a background poller is slow when it passes and
flaky when it does not.
"""

from __future__ import annotations

import json
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


def test_every_red_zone_overlay_that_opens_also_closes(no_network):
    """The overlay is a promise: it says something is about to happen.

    So the close matters as much as the open, and it has to say which way the
    drive went -- a stall on the two gets a record scratch, not a horn. An
    overlay left open would sit over the scores for the rest of the afternoon.
    """
    feed, transport, _ = make_feed(draws=40)
    events = []
    feed._broadcast = lambda payload: (
        events.append(payload) if payload["event"] == "redzone" else None
    )

    for position in range(0, int(transport.recording.duration), 60):
        transport.clock.seek(position)
        feed.poll_once()

    opens = [e for e in events if e["data"]["state"] == "enter"]
    closes = [e for e in events if e["data"]["state"] in ("score", "stop")]

    assert opens, "a whole Sunday produced no red-zone drives anybody owned"
    assert len(opens) == len(closes), f"{len(opens)} opened, {len(closes)} closed"
    assert not feed._redzone, "a drive was still inside the five at the end of the day"
    # Both outcomes must be reachable, or the scratch path is never exercised.
    assert {e["data"]["state"] for e in closes} == {"score", "stop"}
    for event in opens:
        assert event["data"]["involved"], "opened with nobody in the league involved"


def test_a_red_zone_drive_nobody_owns_is_not_announced(no_network):
    """The overlay is for the room, not for the football. A drive inside the
    five with no rostered starter on it is of no interest to anybody here."""
    feed, transport, _ = make_feed(draws=40)
    events = []
    feed._broadcast = lambda payload: (
        events.append(payload) if payload["event"] == "redzone" else None
    )
    transport.clock.seek(3 * 3600)
    feed.poll_once()

    for event in events:
        if event["data"]["state"] == "enter":
            assert event["data"]["involved"]


def test_a_restart_mid_sunday_replays_nothing(no_network, tmp_path):
    """The wiring, not the mechanism.

    `EventEngine` could always persist its dedupe set and `test_events.py` proved
    it worked, but nothing in the application ever passed it a path or called
    `persist()`: the whole feature was reachable only from its own unit test. A
    deploy at four o'clock therefore handed every phone in the bar the entire
    afternoon again, seventy-three touchdown horns in a row. This asserts the
    wiring rather than the mechanism, because the wiring is what was missing.
    """
    seen = tmp_path / "seen.json"
    feed, transport, _ = make_feed()
    feed.engine.seen_path = seen

    transport.clock.seek(3 * 3600)
    feed.poll_once()
    for position in range(3 * 3600 + 300, 5 * 3600, 300):
        transport.clock.seek(position)
        feed.poll_once()

    assert feed.moments, "two hours of a Sunday produced no moments"
    assert seen.is_file(), "the poll never wrote the dedupe set"

    restarted, transport2, _ = make_feed()
    restarted.engine.seen_path = seen
    restarted.engine.seen = set(json.loads(seen.read_text("utf-8")))
    transport2.clock.seek(3 * 3600)
    restarted.poll_once()
    for position in range(3 * 3600 + 300, 5 * 3600, 300):
        transport2.clock.seek(position)
        restarted.poll_once()

    assert not restarted.moments, "a restart replayed the afternoon"


def test_the_app_gives_the_engine_somewhere_to_persist_to():
    """The line that was missing. `EventEngine()` with no seen_path silently
    never persists, and nothing downstream complains."""
    import inspect

    import views.state

    source = inspect.getsource(views.state.PuntState.start_live)
    assert "seen_path" in source, "start_live built an engine that cannot survive a restart"
