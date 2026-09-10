"""Phase 1 acceptance: a full recorded Sunday replays at 60x with no network
access and no cookies.

This is the gate the build plan puts on starting Phase 2, so it is written as
one test that either passes or does not, rather than as a collection of things
that are individually true.
"""

from __future__ import annotations

import time

from config import DEMO_RECORDING, Config
from espn.cache import TTLCache
from espn.client import EspnClient, LeagueRepository
from espn.replay import ReplayTransport


def test_full_sunday_replays_at_60x_with_no_network_and_no_cookies(no_network):
    cfg = Config(replay=DEMO_RECORDING, replay_speed=60.0)
    assert cfg.has_cookies is False, "the acceptance criterion is explicitly cookie-free"

    transport = ReplayTransport.load(cfg.replay, speed=cfg.replay_speed)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    repo = LeagueRepository(client)

    duration = transport.recording.duration
    assert duration > 6 * 3600

    # Sample the day at the same 30 s cadence the live app polls at, driving the
    # clock directly rather than sleeping: 60x of a ten-hour Sunday is still ten
    # minutes of wall clock, which is not a unit test.
    finals: dict[int, float] = {}
    seen_periods = set()
    for position in range(0, int(duration) + 30, 30):
        transport.clock.seek(position)
        client.cache.invalidate()  # a fresh poll, as a live client would make
        snap = repo.snapshot(live=True)

        assert snap.teams, f"no teams at {position}s"
        assert len(snap.teams) == 10
        assert snap.matchups, f"no matchups at {position}s"
        seen_periods.add(snap.scoring_period)

        for matchup in snap.matchups:
            for side in (matchup.home, matchup.away):
                finals[side.team_id] = side.total

    assert seen_periods == {11}, "the recording is one week and must stay one week"
    assert len(finals) == 10
    # A ten-team week where everyone scores between 25 and 200 is the sanity
    # check that the fixture is a fantasy Sunday and not a random walk.
    assert all(25 <= total <= 200 for total in finals.values()), finals
    assert max(finals.values()) - min(finals.values()) > 30, "no spread; the day had no story"

    # The whole point of 60x is that a Sunday is testable in a loop.
    started = time.monotonic()
    transport.clock.seek(0)
    repo.snapshot()
    assert time.monotonic() - started < 5.0


def test_a_fresh_clone_needs_no_configuration_at_all(no_network):
    """`Config.from_env()` with an empty environment must still produce an app
    that serves real-looking data. This is how anyone else ever evaluates the
    repo, and it is the reason the demo fixture is committed."""
    from app import create_app

    cfg = Config()  # nothing set: no league id, no cookies, no replay
    app = create_app(cfg=cfg)
    assert app.extensions["punt"].mode == "demo"

    response = app.test_client().get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Demo data" in body, "an unconfigured app must say so, loudly"
    assert "Bench Mob Rule" in body
