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

from config import DEMO_HISTORY_DB, HISTORY_DB, SEEN_MOMENTS, Config
from engine.commentary import Commentator, PhraseBank
from engine.events import EventEngine
from engine.history import History
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
    history: History | None = None

    def store(self) -> History:
        """The season so far, opened on first use.

        Lazily, because a test that never asks for it should not create a file,
        and because `History` swallows its own failures: a read-only disk costs
        the week selector and nothing else.
        """
        if self.history is None:
            self.history = History(HISTORY_DB if self.mode == "live" else DEMO_HISTORY_DB)
        return self.history

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
                on_week_change=self._week_changed,
                after_poll=self._record,
                ranks=_ranks,
            )
        self.live.start()
        return self.live

    def _record(self, snapshot: LeagueSnapshot) -> None:
        """Write the week down, every poll.

        Every poll rather than once at the final whistle, because there is no
        reliable final whistle: a Monday night game can end at half past eleven
        and the process can be restarted, redeployed or simply killed at any
        point before it. Upserting on each poll means the stored week is never
        more than thirty seconds behind the live one and a finished week simply
        stops changing.
        """
        store = self.store()
        if not store.available:
            return
        from engine.scoring import optimal_lineup  # noqa: PLC0415 - avoids a cycle

        slots = snapshot.settings.starting_slots
        lineups = {}
        for matchup in snapshot.live_matchups or snapshot.matchups:
            for side in (matchup.home, matchup.away):
                lineups[side.team_id] = optimal_lineup(side.players, slots)
        store.record(snapshot, lineups)
        if self.live is not None:
            store.remember(snapshot.season, snapshot.scoring_period,
                           self.live.recent(limit=500), self.live.lines)

    def _week_changed(self, ended: int | None, snapshot: LeagueSnapshot) -> None:
        """ESPN has moved on. The feed is about to empty itself.

        The last write of the week that just ended has to happen HERE and not on
        the next poll, because by then the Moment buffer has been cleared and
        the commentary is gone. It is the only irreplaceable half of the record:
        ESPN can still be asked for the scores.
        """
        if ended is None or self.live is None:
            return
        store = self.store()
        if store.available:
            store.remember(snapshot.season, ended,
                           self.live.recent(limit=500), self.live.lines)
            log.info("week %s recorded before rolling on", ended)

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

    def weeks(self, snapshot: LeagueSnapshot | None = None) -> list[dict[str, Any]]:
        """What the header's week menu offers.

        The live week always appears, whether or not it has been recorded yet:
        on the first run of a fresh install the database is empty and a menu
        with nothing in it would be worse than no menu. Everything else comes
        from what PUNT has actually seen, which is the honest list -- offering
        every week of the season would put eighteen entries in the menu and
        fourteen of them would open a page saying nothing happened.
        """
        current = snapshot.scoring_period if snapshot is not None else None
        season = snapshot.season if snapshot is not None else self.cfg.season
        seen = {row["week"]: row for row in self.store().weeks(season)}
        if current:
            seen.setdefault(current, {"week": current, "settled": False, "moments": 0})
        out = []
        for week in sorted(seen, reverse=True):
            row = seen[week]
            out.append({
                "week": week,
                "current": week == current,
                "settled": bool(row.get("settled")),
                "moments": row.get("moments", 0),
            })
        return out

    def diagnostics(self) -> dict[str, Any]:
        out: dict[str, Any] = {"mode": self.mode, "config": self.cfg.redacted(), **self.client.stats()}
        if self.replay is not None:
            out["replay"] = self.replay.describe()
        if self.live is not None:
            out["live"] = self.live.stats()
        out["history"] = self.store().stats()
        return out


def _ranks(snapshot) -> dict[int, int]:
    """Album position per team, for the ticker's "up to #3 on form" lines.

    Injected rather than imported by the engine. The rating that decides the
    album is a view model, and `engine/` importing `views/` would be a cycle and
    the wrong direction besides: the engine has no business knowing the app has
    an album in it.
    """
    from views.viewmodels import album_view  # noqa: PLC0415 - avoids a cycle

    return {card["id"]: card["rank"] for card in album_view(snapshot)}


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
