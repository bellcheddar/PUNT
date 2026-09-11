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
    headers: dict[str, str] = field(default_factory=dict)
    purpose: str = ""

    def ttl_for(self, live: bool) -> float:
        return self.live_ttl if (live and self.live_ttl is not None) else self.ttl

    def cache_key(self, season: int, league_id: str, scoring_period: int | None) -> str:
        parts = [self.name, str(season), league_id or "-"]
        if self.per_week:
            parts.append(f"sp{scoring_period}")
        return ":".join(parts)


SETTINGS = Feed(
    name="mSettings", views=("mSettings",), ttl=86_400,
    purpose="Scoring items, playoff seeds, tiebreakers, roster slots, the members block",
)
TEAM = Feed(
    name="mTeam", views=("mTeam", "mSettings"), ttl=21_600,
    purpose="Team names, abbreviations, uploaded logo URLs, owner display names, division",
)
SCHEDULE = Feed(
    name="mSchedule", views=("mSchedule", "mTeam"), ttl=3_600,
    purpose="Season grid for all-play, luck and simulations",
)
SCOREBOARD = Feed(
    name="mMatchupScore", views=("mMatchupScore", "mBoxscore"), ttl=30, per_week=True,
    purpose="Per-slot player points and projected remainder: the live feed",
)
ROSTER = Feed(
    name="mRoster", views=("mRoster",), ttl=300, live_ttl=30, per_week=True,
    purpose="Starters vs bench, the basis of bench regret",
)
PLAYERS = Feed(
    name="kona_player_info", views=("kona_player_info",), ttl=900,
    purpose="Projections, injury flags, ownership across the whole player universe",
)
NFL = Feed(
    name="nfl_scoreboard", ttl=20, absolute_url=NFL_SCOREBOARD, authenticated=False,
    purpose="Possession, down and distance, red zone, clock",
)

ALL_FEEDS: tuple[Feed, ...] = (SETTINGS, TEAM, SCHEDULE, SCOREBOARD, ROSTER, PLAYERS, NFL)
BY_NAME: dict[str, Feed] = {f.name: f for f in ALL_FEEDS}


def url_for(feed: Feed, season: int, league_id: str) -> str:
    if feed.absolute_url:
        return feed.absolute_url
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
