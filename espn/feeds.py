"""Every ESPN endpoint this app touches, in one table.

Keeping the URLs, view names and TTLs together in a single module is the stated
mitigation for the biggest known risk in the build: ESPN renames a host or a view
mid-season and every panel breaks at once. When that happens the fix is confined
to this file, and the per-panel degradation in `models.py` keeps the rest of the
app upright while it is made.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Reads go to the `lm-api-reads` host. The bare `fantasy.espn.com` host still
#: resolves but rate-limits harder and has been the first to break in past
#: seasons.
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
LEAGUE_PATH = "/seasons/{season}/segments/0/leagues/{league_id}"
NFL_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


@dataclass(frozen=True)
class Feed:
    """One upstream call: what to ask for, and how long the answer is good for."""

    name: str
    views: tuple[str, ...] = ()
    #: Seconds. `live_ttl` applies while games are in progress, when a five
    #: minute roster cache would mean a lineup change is invisible for a quarter.
    ttl: float = 60.0
    live_ttl: float | None = None
    #: Whether the URL takes `scoringPeriodId`, which makes the cache key
    #: week-specific rather than league-specific.
    per_week: bool = False
    #: NFL game state is a public endpoint on a different host with no auth.
    absolute_url: str = ""
    #: Whether this feed sends the league cookies. Read by the client, which must
    #: not report a 403 from a public host as "your espn_s2 has expired".
    authenticated: bool = True
    #: A league-independent read of the season itself, such as the NFL schedule.
    season_level: bool = False
    #: Not what a Sunday runs on. A failure here must not start the backoff the
    #: live feeds share, or flag the cookies as expired: a draft view that 500s
    #: would otherwise hold back the scores for the length of the backoff.
    optional: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    purpose: str = ""

    def ttl_for(self, live: bool) -> float:
        return self.live_ttl if (live and self.live_ttl is not None) else self.ttl

    def cache_key(self, season: int, league_id: str, scoring_period: int | None) -> str:
        parts = [self.name, str(season), league_id or "-"]
        if self.per_week:
            parts.append(f"sp{scoring_period}")
        return ":".join(parts)


#: Ten minutes, not the day it used to be, and the reason is one field.
#:
#: Almost everything in mSettings is set once in August and never touched --
#: scoring items, roster slots, playoff seeds -- which is what a 24 hour TTL was
#: reasoning about. But it also carries `scoringPeriodId`, and that single field
#: decides which week the entire application is showing. Cached for a day, the
#: scoring period rolls over on a Tuesday morning and PUNT carries on serving
#: the Sunday that has already finished until some time on Wednesday. Nothing
#: looks broken, so nobody thinks to restart it.
#:
#: Ten minutes costs six requests an hour, against a poller already making a
#: hundred and twenty. No `live_ttl`: this feed is never fetched with `live`
#: set, and a second number here would only be a thing to get wrong later.
SETTINGS = Feed(
    name="mSettings", views=("mSettings",), ttl=600,
    purpose="Scoring items, playoff seeds, tiebreakers, roster slots, the members block",
)
TEAM = Feed(
    name="mTeam", views=("mTeam", "mSettings"), ttl=21_600,
    purpose="Team names, abbreviations, uploaded logo URLs, owner display names, division",
)
#: The season grid. NOTE the view is `mMatchup`, not `mSchedule`: there is no
#: such view as mSchedule, and asking for it returns a perfectly valid response
#: with no `schedule` key in it at all -- no error, no empty list, just an
#: absence. Every downstream reader then degraded exactly as designed, and the
#: Multiverse tab said "the schedule feed is missing", which was true and
#: unhelpful, for the whole build.
#:
#: It survived because the SYNTHETIC FIXTURE answered to whatever PUNT asked
#: for. `tools/make_fixture.py` files its payload under this feed's `name`, so
#: the replay returned a schedule for a view ESPN has never served. A fixture
#: generated from the client's own assumptions cannot contradict them.
#:
#: `name` stays "mSchedule" deliberately: it is the cache and replay key, and
#: the committed recording is filed under it.
SCHEDULE = Feed(
    name="mSchedule", views=("mMatchup", "mTeam"), ttl=3_600,
    purpose="Season grid for all-play, luck and simulations",
)
#: 20 seconds while live, and it has to be shorter than the poll rather than
#: equal to it. With both at 30 the poller woke 30.0s after the last poll
#: STARTED, found an entry stored a fraction of a second into that poll -- aged
#: 29.9s, and `expired` is `age > ttl` -- and served it again. Every other poll
#: was a cache hit, so live scores actually refreshed once a minute, and the
#: horn for a touchdown arrived up to sixty seconds after the room saw it.
#: Measured on the first real Sunday: the score feed was 51 seconds old.
SCOREBOARD = Feed(
    name="mMatchupScore", views=("mMatchupScore", "mBoxscore"), ttl=30, live_ttl=20,
    per_week=True,
    purpose="Per-slot player points and projected remainder: the live feed",
)
ROSTER = Feed(
    name="mRoster", views=("mRoster",), ttl=300, live_ttl=30, per_week=True, optional=True,
    purpose="Starters vs bench, the basis of bench regret",
)
PLAYERS = Feed(
    name="kona_player_info", views=("kona_player_info",), ttl=900,
    purpose="Projections, injury flags, ownership across the whole player universe",
)
#: The front office. Everything below changes a few times a week at most, so the
#: TTLs are long: none of it is worth an upstream call per poll, and every one of
#: them is allowed to fail on its own without taking a panel beyond its own.
DRAFT = Feed(
    name="mDraftDetail", views=("mDraftDetail",), ttl=21_600, optional=True,
    purpose="Every pick of the draft: round, pick, team, player, keeper",
)
#: Per week only because ESPN files the request under a scoring period; the
#: response carries every transaction of the season whichever one is asked for.
TRANSACTIONS = Feed(
    name="mTransactions2", views=("mTransactions2",), ttl=900, per_week=True, optional=True,
    purpose="Waiver claims, free-agent adds, drops and trades",
)
#: The same views as the live score feed under a different name, so a finished
#: week is cached for hours rather than refetched every thirty seconds.
BOXSCORE_WEEK = Feed(
    name="mBoxscoreWeek", views=("mMatchupScore", "mBoxscore"), ttl=21_600, per_week=True, optional=True,
    purpose="A finished week: who started, what each was projected and what each scored",
)
#: Filtered to named players per request (see `EspnClient.get`), because the
#: box scores only carry rostered players and a dropped player's points after
#: the drop are the half of a move nobody ever sees.
PLAYER_HISTORY = Feed(
    name="kona_player_history", views=("kona_player_info",), ttl=3_600, optional=True,
    purpose="Week-by-week points and projections for drafted and moved players",
)
PRO_SCHEDULE = Feed(
    name="proTeamSchedules_wl", views=("proTeamSchedules_wl",), ttl=86_400, season_level=True, optional=True,
    purpose="NFL bye weeks",
)
NFL = Feed(
    name="nfl_scoreboard", ttl=20, absolute_url=NFL_SCOREBOARD, authenticated=False,
    purpose="Possession, down and distance, red zone, clock",
)

ALL_FEEDS: tuple[Feed, ...] = (SETTINGS, TEAM, SCHEDULE, SCOREBOARD, ROSTER, PLAYERS, NFL,
                               DRAFT, TRANSACTIONS, BOXSCORE_WEEK, PLAYER_HISTORY, PRO_SCHEDULE)
BY_NAME: dict[str, Feed] = {f.name: f for f in ALL_FEEDS}


def url_for(feed: Feed, season: int, league_id: str) -> str:
    if feed.absolute_url:
        return feed.absolute_url
    if feed.season_level:
        return BASE + f"/seasons/{season}"
    return BASE + LEAGUE_PATH.format(season=season, league_id=league_id)


def params_for(feed: Feed, scoring_period: int | None) -> list[tuple[str, str]]:
    """Query parameters as a list of pairs, because ESPN takes `view` repeatedly
    and a dict would silently keep only the last one."""
    if feed.absolute_url:
        return []
    params: list[tuple[str, str]] = [("view", v) for v in feed.views]
    if feed.per_week and scoring_period is not None:
        params.append(("scoringPeriodId", str(scoring_period)))
    return params
