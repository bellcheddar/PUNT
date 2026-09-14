"""The cookie-authenticated fetch layer, and the only thing that holds a secret.

`espn_s2` and `SWID` are session cookies for a real ESPN account. A browser
cannot send them cross-origin, which is the whole reason this app has a server:
the Flask process holds them, and the ten phones in the bar hold nothing.

Three things live here:

* `Transport` -- how a request actually happens. `LiveTransport` uses the
  network; `espn.replay` supplies one that reads recorded payloads instead. They
  are interchangeable, so every layer above this one is testable with no network
  and no cookies.
* `EspnClient` -- retries, backoff, the shared cache, and the auth state that the
  commissioner banner reads.
* `LeagueRepository` -- assembles the typed `LeagueSnapshot` the rest of the app
  consumes. Nothing above this line ever sees raw JSON.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from espn import feeds
from espn.cache import Result, TTLCache
from espn.models import (
    LeagueSettings,
    LeagueSnapshot,
    parse_byes,
    parse_draft,
    parse_game_states,
    parse_matchups,
    parse_members,
    parse_moves,
    parse_player_history,
    parse_rosters,
    parse_teams,
)

log = logging.getLogger(__name__)

def _user_agent() -> str:
    """Identify the app, but lead with the library that is actually making the call.

    `site.api.espn.com` -- the public NFL scoreboard, the one feed here with no
    auth on it -- runs bot management that allowlists the *leading token* of the
    User-Agent. Measured against it on 2026-09-11, from a droplet and from a
    home connection alike, so this is the header and not the address:

        curl/8.7.1                200      Go-http-client/2.0        200
        python-requests/2.34.2    200      curl/8.7.1 PUNT           200
        Wget/1.21.4               403      (empty)                   403
        Mozilla/5.0 ...           403      PUNT/1.0 (+url) ...       403

    So a polite, self-identifying User-Agent -- the correct thing to send, and
    the thing every guide on scraping etiquette asks for -- was the one being
    refused, while the library's own default sails through. The league host
    (`lm-api-reads.fantasy.espn.com`) does not do this and takes either.

    Leading with `python-requests/<version>` is not a disguise: it is what is
    making the request, and it is exactly what would be sent with no override at
    all. PUNT's own identification is appended, which is the conventional way to
    extend a User-Agent and leaves a human reading a log able to see who this is.
    """
    try:
        import requests  # noqa: PLC0415

        library = f"python-requests/{requests.__version__}"
    except Exception:  # noqa: BLE001 - a User-Agent is not worth an import error
        library = "python-requests/2"  # cold: requests is installed, so the fallback version never applies
    return f"{library} PUNT/1.0 (+https://punt.mdeller.com) ten-team bar league companion"


USER_AGENT = _user_agent()

#: Backoff ceiling for a failure that reached ESPN: a 500, a 429, a redirect to a
#: login page. Five minutes of silence is a long time on a Sunday, but the
#: alternative -- retrying a 429 every 30 s for four hours -- is how an
#: undocumented endpoint stops being available to anybody.
MAX_BACKOFF = 300.0

#: Ceiling for a failure that never reached ESPN at all: the bar's wifi dropped,
#: DNS failed, the socket timed out. Backing off for five minutes here protects
#: nobody -- the requests are not arriving anywhere -- and it means that plugging
#: the cable back in takes up to five minutes to notice, which is the thing the
#: whole hardening phase exists to prevent.
MAX_LOCAL_BACKOFF = 45.0

BASE_BACKOFF = 5.0

#: Where an unconfigured app joins the demo Sunday, and how fast it runs. 2.5 h in
#: is mid-afternoon: the early slate is live and the late one has not kicked off.
#: 60x turns the remaining eight hours into eight minutes, so somebody clicking
#: around a fresh clone sees scores actually move.
DEMO_START_OFFSET = 2.5 * 3600
DEMO_SPEED = 60.0


class UpstreamError(RuntimeError):
    """Any failure to get a usable payload from ESPN.

    `local` distinguishes "we could not reach the network" from "ESPN answered
    and the answer was bad", which is the difference between the bar's wifi and
    somebody else's outage. They deserve very different backoff.
    """

    def __init__(self, message: str, status: int | None = None, local: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.local = local


class AuthExpired(UpstreamError):
    """A 401 or 403: `espn_s2` has rotated and the commissioner must refresh it.

    Distinguished from a generic failure because the response is different: a
    500 is retried, an expired cookie is not, and the UI says something the
    commissioner can act on."""


class Transport(Protocol):
    def fetch(
        self, feed: feeds.Feed, season: int, league_id: str, scoring_period: int | None,
        player_ids: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        ...  # cold: a Protocol body: there is nothing here to run


@dataclass
class LiveTransport:
    """The real thing. `requests` is imported lazily so a replay-only clone --
    which is how the repo is meant to be first run -- needs nothing installed
    beyond Flask."""

    espn_s2: str = field(default="", repr=False)
    espn_swid: str = field(default="", repr=False)
    timeout: float = 12.0
    _session: Any = field(default=None, init=False, repr=False)

    def _get_session(self):
        if self._session is None:
            import requests  # noqa: PLC0415 - deliberately lazy, see docstring

            session = requests.Session()
            session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
            if self.espn_s2 and self.espn_swid:
                # SWID is stored with braces in the browser and ESPN rejects it
                # without them, which is a five-minute mystery every single time.
                swid = self.espn_swid
                if not swid.startswith("{"):
                    swid = "{" + swid.strip("{}") + "}"
                session.cookies.set("espn_s2", self.espn_s2, domain=".espn.com")
                session.cookies.set("SWID", swid, domain=".espn.com")
            self._session = session
        return self._session

    def fetch(
        self, feed: feeds.Feed, season: int, league_id: str, scoring_period: int | None,
        player_ids: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        import requests  # noqa: PLC0415

        url = feeds.url_for(feed, season, league_id)
        params = feeds.params_for(feed, scoring_period)
        try:
            response = self._get_session().get(
                url, params=params, timeout=self.timeout,
                headers=request_headers(feed, season, player_ids)
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            # Never reached ESPN: the venue's wifi, DNS, or a timeout.
            raise UpstreamError(f"{feed.name}: {exc}", local=True) from exc
        except requests.RequestException as exc:
            raise UpstreamError(f"{feed.name}: {exc}") from exc

        if response.status_code in (401, 403):
            # Only for a feed that actually sends the cookies. The NFL scoreboard
            # is public and on another host, and calling its 403 an auth failure
            # is how a perfectly good `espn_s2` gets replaced twice before
            # anybody reads the hostname: the banner said the cookie had rotated
            # while the league's own four feeds were loading fine beside it.
            if feed.authenticated:
                raise AuthExpired(
                    f"{feed.name}: ESPN returned {response.status_code}; espn_s2 has most "
                    "likely rotated and needs refreshing",
                    status=response.status_code,
                )
            raise UpstreamError(
                f"{feed.name}: ESPN returned {response.status_code} on a public endpoint, "
                "which is nothing to do with the league cookies",
                status=response.status_code,
            )
        if response.status_code != 200:
            raise UpstreamError(f"{feed.name}: HTTP {response.status_code}", status=response.status_code)

        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamError(f"{feed.name}: response was not JSON") from exc

        # A private league fetched without cookies returns a *list* rather than
        # the usual object, which reads downstream as "every field is missing"
        # instead of "you are not logged in". Name it here.
        if isinstance(payload, list):
            payload = payload[0] if payload and isinstance(payload[0], dict) else {}
            if not payload:
                raise AuthExpired(f"{feed.name}: ESPN returned an empty list, which means unauthenticated")
        if not isinstance(payload, dict):
            raise UpstreamError(f"{feed.name}: unexpected payload type {type(payload).__name__}")
        return payload


def request_headers(feed: feeds.Feed, season: int,
                    player_ids: tuple[int, ...] | None) -> dict[str, str]:
    """The feed's own headers, plus a player filter when the call names players.

    ESPN takes the filter as JSON in `x-fantasy-filter`, narrowed to single-week
    lines: unfiltered, one player carries season totals and last year's weeks
    in the same list. Two filters that look reasonable are not: a `limit` makes
    the whole request a 400, and `filterStatsForExternalIds` is accepted and
    returns no weekly lines at all. Both measured against the real league on
    2026-09-14. The season is still checked when parsing.
    """
    headers = dict(feed.headers)
    if player_ids:
        headers["x-fantasy-filter"] = json.dumps({"players": {
            "filterIds": {"value": list(player_ids)},
            "filterStatsForSplitTypeIds": {"value": [1]},
        }}, separators=(",", ":"))
    return headers


@dataclass
class AuthState:
    """What the commissioner banner needs to know. Never holds a cookie value."""

    ok: bool = True
    since: str = ""
    detail: str = ""

    def mark_expired(self, detail: str) -> None:
        if self.ok:
            self.since = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.ok = False
        self.detail = detail

    def mark_ok(self) -> None:
        self.ok = True
        self.since = ""
        self.detail = ""


class EspnClient:
    """Cache, retry and backoff around a `Transport`."""

    def __init__(
        self,
        transport: Transport,
        season: int,
        league_id: str,
        cache: TTLCache | None = None,
        poll_seconds: float = 30.0,
    ) -> None:
        self.transport = transport
        self.season = season
        self.league_id = league_id
        self.cache = cache or TTLCache()
        self.poll_seconds = poll_seconds
        self.auth = AuthState()
        self._backoff_until = 0.0
        self._consecutive_failures = 0
        self._lock = threading.Lock()

    # -- backoff -----------------------------------------------------------

    @property
    def backing_off(self) -> bool:
        return time.monotonic() < self._backoff_until

    @property
    def backoff_remaining(self) -> float:
        return max(0.0, self._backoff_until - time.monotonic())

    def _record_failure(self, local: bool = False) -> None:
        with self._lock:
            self._consecutive_failures += 1
            ceiling = MAX_LOCAL_BACKOFF if local else MAX_BACKOFF
            delay = min(ceiling, BASE_BACKOFF * (2 ** (self._consecutive_failures - 1)))
            # Jitter so that a restart of several workers does not resynchronise
            # them into one thundering retry. Clamped *after* the jitter, or the
            # documented ceiling is not one: a 1.25x multiplier applied to a
            # clamped 300 s gives 375 s.
            delay = min(ceiling, delay * (0.75 + random.random() * 0.5))
            self._backoff_until = time.monotonic() + delay
            log.warning(
                "upstream failure %d; backing off %.0fs", self._consecutive_failures, delay
            )

    def _record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._backoff_until = 0.0

    # -- fetching ----------------------------------------------------------

    def get(
        self, feed: feeds.Feed, scoring_period: int | None = None, live: bool = False,
        player_ids: tuple[int, ...] | None = None,
    ) -> Result[dict[str, Any]]:
        """Cached fetch. Never raises for a cache that has ever been filled.

        `player_ids` narrows a player feed to those players. They are part of the
        cache key: the set grows every time somebody makes a move, and an answer
        for last week's set is missing the player who was just dropped.
        """
        key = feed.cache_key(self.season, self.league_id, scoring_period)
        if player_ids:
            digest = hashlib.blake2b(",".join(map(str, player_ids)).encode(), digest_size=6)
            key += ":ids" + digest.hexdigest()
        ttl = feed.ttl_for(live)
        # Only passed when there are players to name, so a transport written
        # before the filter existed keeps working for every other feed.
        extra = {"player_ids": player_ids} if player_ids else {}

        def _fetch() -> dict[str, Any]:
            if self.backing_off:
                raise UpstreamError(
                    f"in backoff for another {self.backoff_remaining:.0f}s", status=None
                )
            # An optional feed neither starts the shared backoff nor speaks for
            # the cookies. The first real run of the draft feed would otherwise
            # have been able to hold back the live scores: one 500 from a view
            # nobody needs on a Sunday, and every feed after it refused as "in
            # backoff" until the timer ran out.
            core = not feed.optional
            try:
                payload = self.transport.fetch(feed, self.season, self.league_id, scoring_period, **extra)
            except AuthExpired as exc:
                if core:
                    self.auth.mark_expired(str(exc))
                    self._record_failure()
                raise
            except UpstreamError as exc:
                if core:
                    self._record_failure(local=exc.local)
                raise
            except Exception:
                if core:
                    self._record_failure()
                raise
            if core:
                self._record_success()
                self.auth.mark_ok()
            return payload

        try:
            return self.cache.get_or_set(key, ttl, _fetch)
        except Exception as exc:  # noqa: BLE001 - cold cache; the caller decides
            return Result(value={}, age=0.0, stale=True, error=str(exc))

    def clear_backoff(self) -> None:
        """Forget the backoff and try again now.

        Used by the commissioner refresh: somebody who has just fixed the cookies
        should not then wait out a five minute timer that exists to protect an
        endpoint from an app that did not know they were broken."""
        with self._lock:
            self._consecutive_failures = 0
            self._backoff_until = 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "cache": self.cache.stats(),
            "auth_ok": self.auth.ok,
            "auth_detail": self.auth.detail,
            "backing_off": self.backing_off,
            "backoff_remaining": round(self.backoff_remaining, 1),
            "consecutive_failures": self._consecutive_failures,
        }


#: Seconds before an optional feed that failed with nothing cached is asked again.
OPTIONAL_RETRY = 300.0


class LeagueRepository:
    """Assembles a `LeagueSnapshot`. The boundary the rest of the app sees."""

    def __init__(self, client: EspnClient) -> None:
        self.client = client
        #: (feed, period, players) -> (the payload parsed, what it parsed to). The
        #: snapshot is rebuilt on every request, and re-parsing sixteen box
        #: scores for each of them is the cost of a feed that changes weekly.
        self._parsed: dict[tuple, tuple[Any, Any]] = {}
        #: (feed, period) -> when it last failed with nothing cached. See
        #: `OPTIONAL_RETRY`.
        self._failed: dict[tuple, float] = {}

    def snapshot(self, scoring_period: int | None = None, live: bool = True) -> LeagueSnapshot:
        problems: list[str] = []
        stale = False

        settings_result = self.client.get(feeds.SETTINGS)
        stale |= settings_result.stale
        if settings_result.error:
            problems.append(f"settings: {settings_result.error}")
        settings = LeagueSettings.from_raw(settings_result.value)

        period = scoring_period or settings.current_scoring_period

        team_result = self.client.get(feeds.TEAM)
        stale |= team_result.stale
        if team_result.error:
            problems.append(f"teams: {team_result.error}")
        # The members block only travels with mSettings, so join across the two
        # responses rather than expecting owner names on the team objects.
        members = parse_members(team_result.value) or parse_members(settings_result.value)
        teams = parse_teams(team_result.value, members)
        if not teams:
            problems.append("no teams in payload")

        # The season grid. Allowed to be missing: a recording of one week has no
        # schedule in it, and everything that reads this degrades to "this week
        # only" rather than to an error.
        schedule_result = self.client.get(feeds.SCHEDULE)
        season_schedule = parse_matchups(schedule_result.value, period) if not schedule_result.error else []
        if schedule_result.error:
            problems.append(f"schedule: {schedule_result.error}")

        score_result = self.client.get(feeds.SCOREBOARD, scoring_period=period, live=live)
        stale |= score_result.stale
        if score_result.error:
            problems.append(f"scoreboard: {score_result.error}")
        matchups = parse_matchups(score_result.value, period)
        if not matchups:
            problems.append("no matchups in payload")

        # The NFL scoreboard is public, unauthenticated and on a different host,
        # so it is the one feed that keeps working when the cookies expire. It is
        # also allowed to fail on its own: without it, `game_over` stays unknown
        # and projections fall back to the naive figure rather than lying.
        games: dict = {}
        nfl_result = self.client.get(feeds.NFL)
        if nfl_result.error:
            problems.append(f"nfl scoreboard: {nfl_result.error}")
        else:
            games = parse_game_states(nfl_result.value)

        if not self.client.auth.ok:
            problems.append(f"auth: {self.client.auth.detail}")

        snapshot = LeagueSnapshot(
            season=self.client.season,
            scoring_period=period,
            settings=settings,
            teams=teams,
            matchups=matchups,
            season_schedule=season_schedule,
            captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            stale=stale,
            problems=problems,
        )
        snapshot.apply_game_states(games)
        self._front_office(snapshot, period)
        return snapshot

    def _optional(self, feed: feeds.Feed, parse, scoring_period: int | None = None,
                  player_ids: tuple[int, ...] | None = None):
        """Fetch and parse a feed that is allowed to be missing.

        A failure parses an empty payload, so its panel says it has nothing
        rather than the whole page reporting a problem: none of these is what
        the Sunday runs on. Parsed once per payload, not once per request.
        """
        key = (feed.name, scoring_period, player_ids)
        failed_at = self._failed.get(key[:2])
        if failed_at is not None and time.monotonic() - failed_at < OPTIONAL_RETRY:
            return parse({})
        result = self.client.get(feed, scoring_period=scoring_period, player_ids=player_ids)
        if not result.value:
            # Nothing cached and nothing fetched. Not asked again for a while:
            # the snapshot is rebuilt on every request, and a broken view would
            # otherwise be requested by every panel on every phone every poll.
            # A stale copy, by contrast, is used as it stands.
            if result.error:
                self._failed[key[:2]] = time.monotonic()
            return parse({})
        self._failed.pop(key[:2], None)
        cached = self._parsed.get(key)
        if cached is not None and cached[0] is result.value:
            return cached[1]
        parsed = parse(result.value)
        # One entry per feed and week: a new player set replaces the old one
        # rather than accumulating a parse for every set there has ever been.
        self._parsed = {k: v for k, v in self._parsed.items() if k[:2] != key[:2]}
        self._parsed[key] = (result.value, parsed)
        return parsed

    def _front_office(self, snapshot: LeagueSnapshot, period: int) -> None:
        """The draft, the moves, next week and the finished weeks, for the four
        decision panels. Every one of them optional."""
        season = self.client.season
        snapshot.draft = self._optional(feeds.DRAFT, parse_draft)
        snapshot.moves = self._optional(feeds.TRANSACTIONS, parse_moves, scoring_period=period)
        snapshot.byes = self._optional(feeds.PRO_SCHEDULE, parse_byes)
        upcoming = period + 1
        if upcoming <= snapshot.settings.final_scoring_period:
            snapshot.next_rosters = self._optional(
                feeds.ROSTER, lambda raw: parse_rosters(raw, upcoming), scoring_period=upcoming)
        snapshot.archive = {
            week: [m for m in self._optional(feeds.BOXSCORE_WEEK,
                                             lambda raw, w=week: parse_matchups(raw, w),
                                             scoring_period=week)
                   if m.matchup_period == week]
            for week in sorted(snapshot.settled_weeks) if week < period
        }
        players = tuple(sorted({p.player_id for p in snapshot.draft}
                               | {pid for m in snapshot.moves for pid in m.added + m.dropped}))
        if players:
            snapshot.player_history = self._optional(
                feeds.PLAYER_HISTORY, lambda raw: parse_player_history(raw, season),
                player_ids=players)


def build_client(cfg, cache: TTLCache | None = None) -> EspnClient:
    """Pick a transport from the configuration.

    Precedence is replay, then live, then the shipped demo recording. The demo
    fallback is what makes `git clone && flask run` work with no cookies, which
    is the Phase 1 acceptance criterion and also how anyone else ever evaluates
    this repo.
    """
    from espn.replay import ReplayTransport, RecordingTransport  # noqa: PLC0415 - avoids a cycle
    from config import DEMO_RECORDING

    if cfg.is_replaying:
        transport: Transport = ReplayTransport.load(cfg.replay, speed=cfg.replay_speed)
        log.info("ESPN transport: replay of %s at %gx", cfg.replay, cfg.replay_speed)
    elif cfg.can_reach_espn:
        transport = LiveTransport(espn_s2=cfg.espn_s2, espn_swid=cfg.espn_swid)
        if cfg.record:
            transport = RecordingTransport(inner=transport)  # cold: RECORD=1 against the live league, which writes real managers' names to disk
            log.info("ESPN transport: live, recording to %s", transport.directory)  # cold: same
        else:
            log.info("ESPN transport: live (league %s, season %s)", cfg.league_id, cfg.season)
    else:
        # Demo mode starts mid-afternoon rather than at kickoff, and runs fast.
        # Started at zero and 1x, the first thing anyone evaluating this repo sees
        # is ten cards reading 0.0, because at 12:45 ET nothing has happened yet --
        # a correct rendering of a state nobody wants to look at. Two and a half
        # hours in, the early games are live, scores are real and the day still has
        # somewhere to go.
        transport = ReplayTransport.load(
            DEMO_RECORDING, speed=DEMO_SPEED, start=DEMO_START_OFFSET
        )
        log.warning(
            "No LEAGUE_ID/ESPN_S2/ESPN_SWID set: falling back to the shipped demo "
            "recording %s, from %.1fh in at %gx. Nothing you see is a real score.",
            DEMO_RECORDING, DEMO_START_OFFSET / 3600, DEMO_SPEED,
        )

    return EspnClient(
        transport=transport,
        season=cfg.season,
        league_id=cfg.league_id or "demo",
        cache=cache,
        poll_seconds=cfg.poll_seconds,
    )
