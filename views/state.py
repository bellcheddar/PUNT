"""The per-process application state, and the one place a snapshot is fetched.

Held on `app.extensions["punt"]` rather than in module globals so that tests can
build an app with a stubbed transport, and so two apps in one interpreter (which
is exactly what the test suite does) do not share a cache.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from flask import current_app

from config import SEEN_MOMENTS, Config
from engine.commentary import Commentator, PhraseBank
from engine.events import EventEngine
from engine.live import LiveFeed
from engine.speech import SpeechCache
from espn.cache import TTLCache
from espn.client import EspnClient, LeagueRepository
from espn.models import LeagueSnapshot
from espn.replay import ReplayTransport

log = logging.getLogger(__name__)


@dataclass
class PuntState:
    cfg: Config
    client: EspnClient
    repo: LeagueRepository
    live: LiveFeed | None = None
    speech: SpeechCache | None = None

    def start_live(self) -> LiveFeed:
        """Begin polling in the background.

        The feed goes through the same TTL cache every request uses, so the
        poller and the phones share one upstream call rather than adding one.
        """
        if self.speech is None:
            self.speech = SpeechCache()
        if self.live is None:
            self.live = LiveFeed(
                fetch=lambda: self.repo.snapshot(live=True),
                poll_seconds=self.cfg.poll_seconds,
                engine=EventEngine(seen_path=SEEN_MOMENTS),
                commentator=_commentator(self.cfg),
                speech=self.speech,
            )
        self.live.start()
        return self.live

    @property
    def replay(self) -> ReplayTransport | None:
        transport = self.client.transport
        return transport if isinstance(transport, ReplayTransport) else None

    @property
    def mode(self) -> str:
        if self.replay is not None:
            return "replay" if self.cfg.is_replaying else "demo"
        return "live"

    def snapshot(self, scoring_period: int | None = None) -> LeagueSnapshot:
        return self.repo.snapshot(scoring_period=scoring_period, live=True)

    def diagnostics(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode, "config": self.cfg.redacted(), **self.client.stats()}
        if self.replay is not None:
            out["replay"] = self.replay.describe()
        if self.live is not None:
            out["live"] = self.live.stats()
        return out


def _commentator(cfg: Config) -> Commentator | None:
    """Load the phrase bank, or carry on without one.

    A missing or malformed bank must not stop the scores working: the app is a
    scoreboard first and a broadcast second."""
    from config import PHRASES_DIR  # noqa: PLC0415

    try:
        bank = PhraseBank.load(PHRASES_DIR)
    except Exception as exc:  # noqa: BLE001
        log.warning("no commentary: %s", exc)
        return None
    log.info("commentary: %d phrases, roast level %d", len(bank), cfg.roast_level)
    return Commentator(bank, roast_level=cfg.roast_level)


def state() -> PuntState:
    return current_app.extensions["punt"]


def snapshot(scoring_period: int | None = None) -> LeagueSnapshot:
    """Fetch the current snapshot, never raising.

    Every route calls this, so a failure here would be a 500 on every tab at
    once. Instead an unreachable upstream with a cold cache produces an empty
    snapshot carrying the reason, and the template renders its empty state with
    a human-readable message -- which is the "never a blank screen" rule made
    mechanical rather than left to each template's discipline.
    """
    st = state()
    try:
        return st.snapshot(scoring_period)
    except Exception as exc:  # noqa: BLE001
        log.exception("snapshot failed")
        from espn.models import LeagueSettings

        return LeagueSnapshot(
            season=st.cfg.season,
            scoring_period=scoring_period or 0,
            settings=LeagueSettings(),
            stale=True,
            problems=[f"Could not read the league: {exc}"],
        )
