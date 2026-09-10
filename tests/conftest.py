"""Shared fixtures.

Every test in this suite runs against the committed synthetic recording, with no
network and no cookies. That is not a limitation being worked around: it is the
Phase 1 acceptance criterion, so making it the default for the whole suite means
a regression in it fails everything rather than one test.
"""

from __future__ import annotations

import socket

import pytest

from config import DEMO_RECORDING, Config
from espn.cache import TTLCache
from espn.client import EspnClient, LeagueRepository
from espn.replay import Recording, ReplayTransport


@pytest.fixture
def no_network(monkeypatch):
    """Make any outbound socket a hard error.

    A replay harness that quietly falls through to the network on a missing
    payload would pass every test on a developer's laptop and fail on a plane,
    in CI, or on a Sunday when ESPN is the thing that is broken. This fixture is
    what turns "no network access" from a claim into an assertion.
    """

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a socket; replay must be self-contained")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    return True


@pytest.fixture
def recording() -> Recording:
    return Recording.load(DEMO_RECORDING)


@pytest.fixture
def transport() -> ReplayTransport:
    """Paused at zero. Tests drive the clock with `.clock.seek()` rather than
    sleeping, so a ten-hour Sunday runs in milliseconds."""
    t = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    t.clock.seek(0)
    return t


@pytest.fixture
def repo(transport) -> LeagueRepository:
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    return LeagueRepository(client)


@pytest.fixture
def app(transport):
    from app import create_app

    cfg = Config(league_id="demo", season=2025, replay=DEMO_RECORDING, replay_speed=0.0)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    application = create_app(cfg=cfg, client=client, start_live=False)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()
