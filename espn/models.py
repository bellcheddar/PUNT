"""Typed views over ESPN's fantasy payloads.

ESPN's fantasy API is undocumented and changes shape without notice: keys appear,
disappear and get renamed between seasons, and a team that has never set a logo
simply omits the field. Every parser here is therefore total -- it accepts any
JSON at all and returns a valid object -- and records what it could not
understand in `.problems` instead of raising.

That is a deliberate trade. A `KeyError` in this layer takes down a request and
with it the whole page; a degraded `Player` with a missing projection takes down
one row of one panel, and the panel says so.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# --------------------------------------------------------------------------
# ESPN's integer enumerations. These are stable across seasons in a way the
# payload shapes are not, but they are also completely opaque in the wire
# format, so nothing outside this module should ever see a bare slot id.
# --------------------------------------------------------------------------

LINEUP_SLOTS: dict[int, str] = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
    20: "BE", 21: "IR", 22: "RES", 23: "FLEX", 24: "EDR",
}

#: Slots whose occupant does not score for you. Everything else is a starter,
#: which is the definition bench regret depends on.
NON_SCORING_SLOTS: frozenset[int] = frozenset({20, 21, 22})

POSITIONS: dict[int, str] = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 7: "P",
    9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S", 14: "DB", 15: "DP",
    16: "D/ST",
}

PRO_TEAMS: dict[int, str] = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL",
    7: "DEN", 8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV",
    14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG",
    20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
    26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL",
    34: "HOU",
}

#: ESPN's `statSourceId`: 0 is what actually happened, 1 is the projection.
STAT_SOURCE_ACTUAL = 0
STAT_SOURCE_PROJECTED = 1
#: `statSplitTypeId` 1 is the single-week split. Season totals use other values
#: and will silently swamp a weekly number if you forget to filter on it.
STAT_SPLIT_WEEK = 1

_INJURY_SHORT = {
    "ACTIVE": "", "NORMAL": "", "QUESTIONABLE": "Q", "DOUBTFUL": "D",
    "OUT": "OUT", "INJURY_RESERVE": "IR", "SUSPENSION": "SUSP",
    "DAY_TO_DAY": "DTD", "PROBABLE": "P",
}


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce anything ESPN might put in a numeric field to a float."""
    if isinstance(value, bool):  # bool is an int subclass; never a score
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")


def sanitise_user_text(raw: Any, fallback: str = "") -> str:
    """Team names and abbreviations are user-generated and hostile by default.

    A manager can and eventually will set their team name to 40 characters, to
    pure emoji, to an empty string, or to something containing angle brackets.
    Strip markup and collapse whitespace here; truncation is deliberately *not*
    done, because the card measures its own width in CSS and a fixed character
    count would cut a wide name early and a narrow one late.
    """
    if not isinstance(raw, str):
        return fallback
    text = _WS_RE.sub(" ", _TAG_RE.sub("", raw)).strip()
    return text or fallback


@dataclass
class Player:
    """One athlete in one lineup slot on one team, for one scoring period."""

    id: int
    name: str
    slot_id: int
    position: str
    pro_team: str
    pro_team_id: int = 0
    #: The slots ESPN says this player may fill. The league's own rule, not a
    #: guess from his position -- which matters because the optimal lineup, and
    #: therefore bench regret, is only legal if it respects the real one. A
    #: player listed eligible at both RB and WR, or a league running a superflex,
    #: cannot be inferred from `defaultPositionId`.
    eligible_slots: tuple[int, ...] = ()
    points: float = 0.0
    projected: float = 0.0
    injury: str = ""
    opponent: str = ""
    #: Whether this player's real NFL game has finished. Only known once the NFL
    #: scoreboard feed has been joined on; `None` means nobody has said.
    game_over: bool | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def slot(self) -> str:
        return LINEUP_SLOTS.get(self.slot_id, f"?{self.slot_id}")

    @property
    def is_starter(self) -> bool:
        return self.slot_id not in NON_SCORING_SLOTS

    @property
    def injury_short(self) -> str:
        return _INJURY_SHORT.get(self.injury, self.injury[:4] if self.injury else "")

    @property
    def remaining(self) -> float:
        """Projected points still to come.

        Two things are wrong with the naive `projected - points`. It can go
        negative, which makes a live projected total tick *down* as somebody
        scores. And it keeps promising points from a player whose game finished
        two hours ago: without the NFL game state joined on, a settled Sunday
        night still reads "four players left to play" for every manager, and
        every projection sits above every final score.
        """
        if self.game_over:
            return 0.0
        return max(0.0, self.projected - self.points)

    @classmethod
    def from_entry(cls, entry: Any, scoring_period: int) -> "Player":
        entry = _dict(entry)
        problems: list[str] = []

        pool = _dict(entry.get("playerPoolEntry"))
        raw = _dict(pool.get("player")) or _dict(entry.get("player"))
        if not raw:
            problems.append("no player object")

        pid = raw.get("id") or entry.get("playerId") or 0
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            pid = 0

        name = sanitise_user_text(raw.get("fullName"), "")
        if not name:
            first = sanitise_user_text(raw.get("firstName"), "")
            last = sanitise_user_text(raw.get("lastName"), "")
            name = (first + " " + last).strip() or "Unknown player"
            if not first and not last:
                problems.append("no name")

        slot_id = entry.get("lineupSlotId")
        try:
            slot_id = int(slot_id)
        except (TypeError, ValueError):
            slot_id = 20  # unknown slots are treated as bench, never as starters
            problems.append("no lineup slot; treated as bench")

        pos_id = raw.get("defaultPositionId")
        try:
            position = POSITIONS.get(int(pos_id), "")
        except (TypeError, ValueError):
            position = ""

        pro_id = raw.get("proTeamId")
        try:
            pro_team = PRO_TEAMS.get(int(pro_id), "")
        except (TypeError, ValueError):
            pro_team = ""

        eligible: list[int] = []
        for slot in _list(raw.get("eligibleSlots")):
            try:
                eligible.append(int(slot))
            except (TypeError, ValueError):
                continue

        points, projected, found_a_week = _split_stats(raw.get("stats"), scoring_period)
        # Whether a weekly row was *found*, not whether it was non-zero. This
        # asked `projected == 0.0 and points == 0.0`, which is a perfectly
        # ordinary player: a backup on a bench, somebody ruled out, a defence on
        # bye. ESPN projects those at zero and they score zero, and the first
        # live league put four of them on the degradation banner -- which is how
        # a banner stops being read by the time it means something.
        if raw and not found_a_week:
            problems.append("no week stats")

        injury = raw.get("injuryStatus")
        injury = injury if isinstance(injury, str) else ""

        # `appliedStatTotal` on the entry is ESPN's own applied score and is
        # authoritative when present -- it already includes league scoring rules
        # that the raw stat split does not.
        applied = entry.get("appliedStatTotal")
        if isinstance(applied, (int, float)) and not isinstance(applied, bool):
            points = float(applied)

        try:
            pro_team_id = int(pro_id)
        except (TypeError, ValueError):
            pro_team_id = 0

        return cls(
            id=pid, name=name, slot_id=slot_id, position=position,
            pro_team=pro_team, pro_team_id=pro_team_id,
            eligible_slots=tuple(eligible), points=points,
            projected=projected, injury=injury, problems=problems,
        )


def _split_stats(stats: Any, scoring_period: int) -> tuple[float, float, bool]:
    """Pull (actual, projected) for one week out of ESPN's stats array.

    The array mixes season totals, weekly splits, actuals and projections in one
    flat list distinguished only by `statSourceId` and `statSplitTypeId`. Reading
    it without filtering on both is the classic way to display a season total as
    a weekly score and not notice until someone's card says 1,400 points.

    Returns `(actual, projected, found_a_weekly_row)`. The third is not
    cosmetic: a player can legitimately have both numbers at zero, and telling
    that apart from a player ESPN sent no weekly row for is the difference
    between a quiet bench and a degraded feed.
    """
    actual = projected = 0.0
    found = False
    for stat in _list(stats):
        stat = _dict(stat)
        if stat.get("scoringPeriodId") != scoring_period:
            continue
        if stat.get("statSplitTypeId") != STAT_SPLIT_WEEK:
            continue
        total = _num(stat.get("appliedTotal"))
        if stat.get("statSourceId") == STAT_SOURCE_PROJECTED:
            projected = total
            found = True
        elif stat.get("statSourceId") == STAT_SOURCE_ACTUAL:
            actual = total
            found = True
    # `found` separates "ESPN sent no weekly row for this player", which is worth
    # putting on the banner, from "both numbers are zero", which is Tuesday.
    return actual, projected, found


@dataclass
class GameState:
    """One real NFL game, from the public scoreboard feed.

    This is what turns a fantasy score into a live one: which players can still
    score, who has the ball, and whether anybody is inside the five yard line --
    which is the trigger for the countdown overlay.
    """

    pro_team_id: int
    abbrev: str
    state: str = "pre"  # pre | in | post
    period: int = 0
    clock: str = ""
    score: int = 0
    opponent: str = ""
    possession: bool = False
    red_zone: bool = False
    down_distance: str = ""
    #: Kickoff, as ESPN sends it: an ISO 8601 instant in UTC. Kept as the raw
    #: string rather than a datetime because every other field on this class is
    #: what the feed said, and the only thing that reads it wants a bucket.
    kickoff: str = ""

    @property
    def finished(self) -> bool:
        return self.state == "post"

    @property
    def live(self) -> bool:
        return self.state == "in"

    @property
    def window(self) -> str:
        """Which slot of the NFL week this game kicks off in.

        The buckets are the ones people actually say out loud -- the early
        games, the late games, Sunday night -- rather than clock times, because
        "my week is decided by six" is the thought this exists to answer.

        Converted to US Eastern before bucketing. The alternative, bucketing the
        UTC hour directly, is right for about four months of the year: the same
        one o'clock kickoff is 18:00 UTC in November and 17:00 in September, and
        a fixed offset silently files half the season in the wrong slot.
        """
        if not self.kickoff:
            return ""
        try:
            from datetime import datetime  # noqa: PLC0415
            from zoneinfo import ZoneInfo  # noqa: PLC0415

            at = datetime.fromisoformat(self.kickoff.replace("Z", "+00:00"))
            at = at.astimezone(ZoneInfo("America/New_York"))
        except Exception:  # noqa: BLE001
            return ""  # cold: a missing tzdata or a malformed date costs the bucket, not the game
        day, hour = at.weekday(), at.hour
        # The committed fixture is one Sunday, so the three weekday buckets are
        # unreachable from it by construction: a recording of a Sunday has no
        # Thursday game in it. They are reached every single week in production,
        # which is why they are here, and `tests/test_season_panels.py` checks
        # each one against a known instant rather than against the recording.
        if day == 3:
            return "THU"  # cold: the fixture is a Sunday
        if day == 0:
            return "MNF"  # cold: the fixture is a Sunday
        if day == 5:
            return "SAT"  # cold: the fixture is a Sunday, and Saturday games are December only
        if hour >= 19:
            return "SNF"
        if hour >= 15:
            return "LATE"
        return "EARLY"

    @property
    def elapsed(self) -> float:
        """How much of this game has been played, from 0 to 1.

        Read from the period and the play clock rather than from wall time,
        because a live feed's idea of kickoff is not reliable and the clock is
        what every screen in the bar is already showing.

        Lives on the model rather than in a view, because two different things
        need it and they are on opposite sides of the import graph: the card
        ratings prorate a projection by it, and the ticker uses it to decide
        whether a player is behind or merely early.

        Overtime is deliberately not modelled. It would push this past 1.0 and
        make a finished game look unfinished, and the only question anyone asks
        of it is "how much of this has happened", which overtime does not
        change.
        """
        if self.finished:
            return 1.0
        if not self.live or not self.period:
            return 0.0
        # The clock counts DOWN within a quarter, so the elapsed part of the
        # current one is what is missing from it. A malformed or empty clock is
        # treated as the quarter having just started, which errs towards a
        # smaller denominator and so towards a flattering figure: the
        # alternative errs towards dividing by something that has not happened.
        left = 15.0
        try:
            minutes, _, seconds = str(self.clock or "15:00").partition(":")
            left = float(minutes) + float(seconds or 0) / 60.0
        except ValueError:
            pass  # cold: ESPN has never sent a clock that is not mm:ss
        played = (self.period - 1) * 15.0 + max(0.0, 15.0 - left)
        return max(0.0, min(1.0, played / 60.0))


def parse_game_states(raw: Any) -> dict[int, GameState]:
    """Index the NFL scoreboard by fantasy `proTeamId`.

    ESPN's site API and its fantasy API happen to use the same integer ids for
    NFL franchises today, but they are separate systems and have drifted before
    (a relocated team gets a new abbreviation on one side before the other), so
    the id is used when it is present and the abbreviation is kept as a second
    key rather than trusting either alone.
    """
    states: dict[int, GameState] = {}
    by_abbrev: dict[str, int] = {v: k for k, v in PRO_TEAMS.items()}

    for event in _list(_dict(raw).get("events")):
        event = _dict(event)
        status = _dict(event.get("status"))
        state = _dict(status.get("type")).get("state")
        state = state if isinstance(state, str) else "pre"
        period = int(_num(status.get("period")))
        display_clock = status.get("displayClock")
        display_clock = display_clock if isinstance(display_clock, str) else ""

        # ESPN puts the kickoff on the event and repeats it on the competition.
        # Either will do; taking both means a feed that drops one still buckets.
        kickoff = event.get("date")
        kickoff = kickoff if isinstance(kickoff, str) else ""

        for competition in _list(event.get("competitions")):
            competition = _dict(competition)
            if not kickoff and isinstance(competition.get("date"), str):
                kickoff = competition["date"]
            situation = _dict(competition.get("situation"))
            possession_id = str(situation.get("possession") or "")
            competitors = [_dict(c) for c in _list(competition.get("competitors"))]

            for competitor in competitors:
                team = _dict(competitor.get("team"))
                abbrev = team.get("abbreviation")
                abbrev = abbrev.upper() if isinstance(abbrev, str) else ""
                try:
                    team_id = int(team.get("id"))
                except (TypeError, ValueError):
                    team_id = by_abbrev.get(abbrev, 0)
                if team_id not in PRO_TEAMS and abbrev in by_abbrev:
                    team_id = by_abbrev[abbrev]
                if not team_id:
                    continue

                others = [c for c in competitors if c is not competitor]
                opponent = ""
                if others:
                    opponent_abbrev = _dict(others[0].get("team")).get("abbreviation")
                    opponent = opponent_abbrev.upper() if isinstance(opponent_abbrev, str) else ""

                states[team_id] = GameState(
                    pro_team_id=team_id,
                    abbrev=abbrev or PRO_TEAMS.get(team_id, ""),
                    state=state,
                    period=period,
                    clock=display_clock,
                    score=int(_num(competitor.get("score"))),
                    opponent=opponent,
                    possession=bool(possession_id) and possession_id == str(team.get("id")),
                    red_zone=bool(situation.get("isRedZone")),
                    down_distance=str(situation.get("shortDownDistanceText") or ""),
                    kickoff=kickoff,
                )
    return states


@dataclass
class Member:
    """A human. Joined to a team through the `owners` GUID list."""

    guid: str
    display_name: str

    @classmethod
    def from_raw(cls, raw: Any) -> "Member":
        raw = _dict(raw)
        guid = raw.get("id")
        guid = guid.upper() if isinstance(guid, str) else ""
        name = sanitise_user_text(raw.get("displayName"), "")
        if not name:
            first = sanitise_user_text(raw.get("firstName"), "")
            last = sanitise_user_text(raw.get("lastName"), "")
            name = (first + " " + last).strip() or "Anonymous"
        return cls(guid=guid, display_name=name)


@dataclass
class Team:
    """A franchise. Identity is resolved live from ESPN on every load, so a
    mid-season rename or logo upload appears without a deploy."""

    id: int
    name: str
    abbrev: str
    logo: str = ""
    owner_guids: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    points_against: float = 0.0
    division_id: int = 0
    playoff_seed: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def manager(self) -> str:
        """The first owner, which is who the banter addresses. Co-owners are
        listed on the manager profile rather than shouted at from a card."""
        return self.owners[0] if self.owners else self.name

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}" + (f"-{self.ties}" if self.ties else "")

    @property
    def monogram(self) -> str:
        """The fallback tile's letters. At least two managers will never upload a
        logo, so this is a designed state, not an error state."""
        letters = re.sub(r"[^A-Za-z0-9]", "", self.abbrev or self.name)
        return (letters[:3] or "?").upper()

    @property
    def hue(self) -> int:
        """Deterministic card colour, derived from the team id alone.

        Hashing the id rather than storing a palette means every phone in the bar
        agrees on a team's colour with no shared state, and the colour survives a
        rename. `id` is used rather than `name` precisely so it survives one.
        """
        digest = hashlib.sha256(f"punt-team-{self.id}".encode()).digest()
        return int.from_bytes(digest[:2], "big") % 360

    @classmethod
    def from_raw(cls, raw: Any, members: dict[str, str] | None = None) -> "Team":
        raw = _dict(raw)
        members = members or {}
        problems: list[str] = []

        try:
            tid = int(raw.get("id", 0))
        except (TypeError, ValueError):
            tid = 0
            problems.append("no team id")

        # Modern payloads carry `name`; older ones split it into location +
        # nickname and both still turn up depending on the view requested.
        name = sanitise_user_text(raw.get("name"), "")
        if not name:
            location = sanitise_user_text(raw.get("location"), "")
            nickname = sanitise_user_text(raw.get("nickname"), "")
            name = (location + " " + nickname).strip()
        if not name:
            name = f"Team {tid}"
            problems.append("no team name")

        abbrev = sanitise_user_text(raw.get("abbrev"), "")[:6]

        logo = raw.get("logo")
        logo = logo.strip() if isinstance(logo, str) else ""

        guids = [g.upper() for g in _list(raw.get("owners")) if isinstance(g, str)]
        owner_names = [members[g] for g in guids if g in members]
        if guids and not owner_names:
            problems.append("owners not found in members block")

        overall = _dict(_dict(raw.get("record")).get("overall"))

        def _int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        return cls(
            id=tid, name=name, abbrev=abbrev or name[:4].upper(), logo=logo,
            owner_guids=guids, owners=owner_names,
            wins=_int(overall.get("wins")), losses=_int(overall.get("losses")),
            ties=_int(overall.get("ties")),
            points_for=_num(overall.get("pointsFor")),
            points_against=_num(overall.get("pointsAgainst")),
            division_id=_int(raw.get("divisionId")),
            playoff_seed=_int(raw.get("playoffSeed")),
            problems=problems,
        )


@dataclass
class Side:
    """One half of a matchup: a team, its roster and its running total."""

    team_id: int
    total: float = 0.0
    projected: float = 0.0
    players: list[Player] = field(default_factory=list)

    @property
    def starters(self) -> list[Player]:
        return [p for p in self.players if p.is_starter]

    @property
    def bench(self) -> list[Player]:
        return [p for p in self.players if not p.is_starter]

    @property
    def live_projection(self) -> float:
        """Where this side finishes if every remaining player hits projection."""
        return round(self.total + sum(p.remaining for p in self.starters), 2)

    @property
    def in_play(self) -> int:
        """Starters who can still score: their real game has not finished.

        Counted from game state rather than from `points == 0`, because a player
        who genuinely scored nothing and a player who has not kicked off yet look
        identical on the fantasy feed, and telling a manager they have four
        players left when they have none is the difference between hope and a
        goose egg.

        Deliberately *not* called "yet to play": a player in the third quarter has
        very much played, and labelling a mid-afternoon row "9 to play" reads as
        though the whole lineup is still on the bus.
        """
        return sum(1 for p in self.starters if p.game_over is False)

    @classmethod
    def from_raw(cls, raw: Any, scoring_period: int) -> "Side":
        raw = _dict(raw)
        try:
            team_id = int(raw.get("teamId", 0))
        except (TypeError, ValueError):
            team_id = 0

        # `rosterForCurrentScoringPeriod` is the live roster and only appears
        # with mBoxscore; `rosterForMatchupPeriod` is the settled one. Prefer the
        # live view and fall back, or a completed week renders empty.
        roster = _dict(raw.get("rosterForCurrentScoringPeriod"))
        if not roster.get("entries"):
            roster = _dict(raw.get("rosterForMatchupPeriod"))

        players = [Player.from_entry(e, scoring_period) for e in _list(roster.get("entries"))]

        total = _num(raw.get("totalPoints"))
        if not total and players:
            # Some payloads only carry per-entry scores; summing starters is the
            # correct reconstruction and matches ESPN's own displayed total.
            total = round(sum(p.points for p in players if p.is_starter), 2)

        projected = _num(raw.get("totalProjectedPointsLive"))
        if not projected and players:
            projected = round(sum(p.projected for p in players if p.is_starter), 2)

        return cls(team_id=team_id, total=total, projected=projected, players=players)


@dataclass
class Matchup:
    """A head-to-head pairing for one matchup period."""

    id: int
    matchup_period: int
    home: Side
    away: Side
    winner: str = "UNDECIDED"

    @property
    def team_ids(self) -> tuple[int, int]:
        return (self.home.team_id, self.away.team_id)

    @property
    def margin(self) -> float:
        """Signed towards the home side, which is the arbitrary but consistent
        convention every consumer of this field assumes."""
        return round(self.home.total - self.away.total, 2)

    def side_for(self, team_id: int) -> Side | None:
        if self.home.team_id == team_id:
            return self.home
        if self.away.team_id == team_id:
            return self.away
        return None

    def opponent_of(self, team_id: int) -> Side | None:
        if self.home.team_id == team_id:
            return self.away
        if self.away.team_id == team_id:
            return self.home
        return None

    @classmethod
    def from_raw(cls, raw: Any, scoring_period: int) -> "Matchup":
        raw = _dict(raw)
        try:
            mid = int(raw.get("id", 0))
        except (TypeError, ValueError):
            mid = 0
        try:
            period = int(raw.get("matchupPeriodId", 0))
        except (TypeError, ValueError):
            period = 0
        winner = raw.get("winner")
        return cls(
            id=mid,
            matchup_period=period,
            home=Side.from_raw(raw.get("home"), scoring_period),
            away=Side.from_raw(raw.get("away"), scoring_period),
            winner=winner if isinstance(winner, str) else "UNDECIDED",
        )


@dataclass
class LeagueSettings:
    """The house rules, read once a season."""

    name: str = "League"
    playoff_team_count: int = 6
    regular_season_matchups: int = 14
    lineup_slot_counts: dict[int, int] = field(default_factory=dict)
    current_scoring_period: int = 1
    current_matchup_period: int = 1
    problems: list[str] = field(default_factory=list)

    def title_for(self, season: int) -> str:
        """The league's name with the season it is actually being played in.

        Leagues get named once and carry the founding year for ever: this one is
        called "Logan House 2023" in ESPN and is playing the 2026 season, so the
        header read three years out of date. A trailing four-digit year is
        replaced rather than appended, and a name with no year in it simply
        gains one.

        Only a *trailing* year, and only a plausible one. "Legion 1984" is a
        joke somebody made on purpose and lives in the middle of nothing; a year
        at the end, within a few of the season being played, is a stamp.
        """
        import re  # noqa: PLC0415

        stem = self.name.strip()
        match = re.search(r"\s+(\d{4})$", stem)
        if match and abs(int(match.group(1)) - season) <= 12:
            stem = stem[: match.start()].rstrip()
        return f"{stem} {season}" if stem else str(season)

    @property
    def starting_slots(self) -> list[int]:
        """Every starting slot the league runs, one entry per seat, ordered the
        way a lineup is displayed. This is the shape `optimal lineup` fills."""
        order = [0, 2, 4, 6, 23, 3, 5, 7, 16, 17, 18, 19]
        out: list[int] = []
        for slot in order:
            out.extend([slot] * int(self.lineup_slot_counts.get(slot, 0)))
        for slot, count in sorted(self.lineup_slot_counts.items()):
            if slot in NON_SCORING_SLOTS or slot in order:
                continue
            out.extend([slot] * int(count))
        return out

    @classmethod
    def from_raw(cls, raw: Any) -> "LeagueSettings":
        raw = _dict(raw)
        settings = _dict(raw.get("settings"))
        problems: list[str] = []
        if not settings:
            problems.append("no settings block")

        counts: dict[int, int] = {}
        for key, value in _dict(_dict(settings.get("rosterSettings")).get("lineupSlotCounts")).items():
            try:
                slot, count = int(key), int(value)
            except (TypeError, ValueError):
                continue
            if count > 0:
                counts[slot] = count
        if not counts:
            problems.append("no lineup slot counts")

        def _int(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        return cls(
            name=sanitise_user_text(settings.get("name"), "League"),
            playoff_team_count=_int(_dict(settings.get("scheduleSettings")).get("playoffTeamCount"), 6),
            regular_season_matchups=_int(
                _dict(settings.get("scheduleSettings")).get("matchupPeriodCount"), 14
            ),
            lineup_slot_counts=counts,
            current_scoring_period=_int(raw.get("scoringPeriodId"), 1),
            current_matchup_period=_int(
                _dict(raw.get("status")).get("currentMatchupPeriod"), _int(raw.get("scoringPeriodId"), 1)
            ),
            problems=problems,
        )


@dataclass
class LeagueSnapshot:
    """Everything the app knows at one instant. The unit the engine diffs."""

    season: int
    scoring_period: int
    settings: LeagueSettings
    teams: list[Team] = field(default_factory=list)
    matchups: list[Matchup] = field(default_factory=list)
    #: Every matchup period of the season, settled and future. Separate from
    #: `matchups`, which is this week's boxscore detail: the grid carries totals
    #: and pairings only, which is all the standings and the playoff simulator
    #: need and all `mSchedule` provides.
    #:
    #: Named `season_schedule` rather than `season`, which on this class already
    #: means the year. The collision was a syntax error the first time and would
    #: have been a silent shadowing in a less lucky arrangement.
    season_schedule: list[Matchup] = field(default_factory=list)
    games: dict[int, GameState] = field(default_factory=dict)
    captured_at: str = ""
    stale: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def teams_by_id(self) -> dict[int, Team]:
        return {t.id: t for t in self.teams}

    def team(self, team_id: int) -> Team | None:
        return self.teams_by_id.get(team_id)

    def matchup_for(self, team_id: int) -> Matchup | None:
        for m in self.matchups:
            if team_id in m.team_ids:
                return m
        return None

    def apply_game_states(self, games: dict[int, GameState]) -> None:
        """Join the NFL scoreboard onto every player in the snapshot.

        Done as a pass over the assembled snapshot rather than during player
        parsing, because the two feeds are fetched independently and one of them
        is allowed to be missing: with no game states, `game_over` stays `None`
        and every consumer falls back to the naive projection rather than
        claiming knowledge it does not have.
        """
        self.games = games
        if not games:
            return
        for matchup in self.matchups:
            for side in (matchup.home, matchup.away):
                for player in side.players:
                    state = games.get(player.pro_team_id)
                    if state is None:
                        continue
                    player.game_over = state.finished
                    player.opponent = state.opponent

    def season_weeks(self) -> dict[int, list[Matchup]]:
        """The season grid, by matchup period."""
        weeks: dict[int, list[Matchup]] = {}
        for matchup in self.season_schedule:
            weeks.setdefault(matchup.matchup_period, []).append(matchup)
        return weeks

    @property
    def settled_weeks(self) -> dict[int, list[Matchup]]:
        """Only the weeks that are finished. A week in progress has real scores
        and no result, and counting it as one would make every standing wrong
        for four hours every Sunday."""
        return {week: games for week, games in self.season_weeks().items()
                if games and all(m.winner not in ("UNDECIDED", "") for m in games)}

    @property
    def red_zone_games(self) -> list[GameState]:
        """Live drives inside the five. The countdown overlay's trigger."""
        return [g for g in self.games.values() if g.live and g.red_zone]

    @property
    def live_matchups(self) -> list[Matchup]:
        return [m for m in self.matchups if m.matchup_period == self.settings.current_matchup_period]

    def all_problems(self) -> list[str]:
        """Every degradation in this snapshot, flattened, for the stale banner.

        Surfacing these is the difference between "one panel looks odd" and "the
        commissioner knows the cookies expired an hour ago".
        """
        out = list(self.problems) + list(self.settings.problems)
        for team in self.teams:
            out.extend(f"team {team.id}: {p}" for p in team.problems)
        for matchup in self.matchups:
            for side in (matchup.home, matchup.away):
                for player in side.players:
                    out.extend(f"{player.name}: {p}" for p in player.problems)
        return out


def parse_members(raw: Any) -> dict[str, str]:
    """GUID -> display name, from the members block of an mSettings response."""
    return {m.guid: m.display_name for m in (Member.from_raw(x) for x in _list(_dict(raw).get("members"))) if m.guid}


def parse_teams(raw: Any, members: dict[str, str] | None = None) -> list[Team]:
    return [Team.from_raw(t, members) for t in _list(_dict(raw).get("teams"))]


def parse_matchups(raw: Any, scoring_period: int) -> list[Matchup]:
    return [Matchup.from_raw(m, scoring_period) for m in _list(_dict(raw).get("schedule"))]
