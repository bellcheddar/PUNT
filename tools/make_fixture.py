#!/usr/bin/env python3
"""Generate the synthetic recorded Sunday that ships with the repo.

Why synthetic rather than a capture of the real league:

* A capture of a real Sunday is roughly 480 polls of a payload near a megabyte.
  Nobody commits half a gigabyte to make a clone runnable.
* A real capture carries ten real people's ESPN display names and account GUIDs.
  This is a public repository.
* A capture is whatever happened that week. The fixture is built to contain one
  of every event the engine must detect -- a bench disaster, a goose egg, a late
  lead change, a doomed manager, a long touchdown -- so the Phase 2 tests have
  something to assert against instead of hoping.

Everything below is invented. No real athlete, team or person appears.

    python3 tools/make_fixture.py            # regenerate in place
    python3 tools/make_fixture.py --check    # verify the committed fixture matches

The generator is seeded, so regenerating produces a byte-identical fixture and a
diff means somebody changed the generator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEMO_RECORDING, RECORDINGS_DIR  # noqa: E402
from espn.replay import Entry, Recording, write_recording  # noqa: E402

#: macOS names a sync conflict "<stem> 2.<ext>". This repository lives under an
#: iCloud-synced Documents folder, and rewriting the fixture in place makes
#: iCloud resurrect the previous generation under these names -- 218 of them
#: appeared during a single editing session. Nothing reads them, the manifest
#: does not name them, and 99 reached a commit before anyone looked.
CONFLICT_COPY = re.compile(r" \d+(\.[A-Za-z0-9.]+)?$")


def sweep_conflict_copies(directory: Path, keep: set[str]) -> list[str]:
    """Delete unreferenced iCloud conflict copies. Returns what was removed."""
    removed = []
    for path in directory.iterdir():
        if not path.is_file() or path.name in keep:
            continue
        stem = path.name.split(".", 1)[0]
        if CONFLICT_COPY.search(stem):
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return sorted(removed)


#: What this generator writes: a four-digit sequence number, the feed name, and
#: gzipped JSON. Deliberately narrow, because the sweep below deletes files.
PAYLOAD_FILE = re.compile(r"^\d{4}_[A-Za-z_]+\.json\.gz$")


def sweep_orphans(directory: Path, keep: set[str]) -> list[str]:
    """Delete payloads left behind by a previous generation.

    The generator dedupes: a minute in which nothing changed writes no payload.
    So a change to the fixture can *reduce* the payload count, and writing in
    place then leaves the tail of the old run on disk under names the new
    manifest does not use. Nothing reads them, `--check` calls them strays, and
    the repository quietly carries a second fixture forever. Planting one stack
    of three receivers dropped the count by one and orphaned twenty-nine files.
    """
    removed = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.name not in keep and PAYLOAD_FILE.match(path.name):
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed

SEED = 20251116
SEASON = 2025
SCORING_PERIOD = 11
MATCHUP_PERIOD = 11
LEAGUE_ID = "000000000"

#: Sampled every 60 s of game time across the Sunday windows, 13:00 to 20:15 ET.
#: One minute is finer than the app's own 30 s poll needs at 1x and coarse enough
#: that the whole day is a few hundred frames.
STEP = 60
#: Fifteen minutes of pre-game before the first kickoff. Without it the
#: recording opens mid-play and there is no way to test what the app looks like
#: when nothing has happened yet -- which is the state every visitor sees first.
PREGAME = 900
DAY_END = 10 * 3600 + 3300  # 12:45 -> 23:40 ET, the whole Sunday slate

# NFL windows, as offsets from the 13:00 ET kickoff. A game runs a little over
# three hours of wall clock. Players only accrue points while their game is live,
# which is what gives the day its shape: the early slate settles, the late slate
# swings it, and one manager spends four hours waiting on a Sunday night flex.
WINDOWS = {
    "early": (PREGAME, PREGAME + 3 * 3600 + 600),                    # 13:00 - 16:10
    "late": (PREGAME + 3 * 3600 + 300, PREGAME + 6 * 3600 + 300),    # 16:05 - 19:05
    "night": (PREGAME + 7 * 3600 + 1200, PREGAME + 10 * 3600 + 1800),  # 20:20 - 23:30
}

#: The real kickoff instant for each window, in UTC, on the Sunday this
#: recording pretends to be. ESPN sends `date` on every scoreboard event and
#: PUNT buckets games by it, so a fixture without one leaves the Scoring Clock
#: panel with nothing to draw and `deadcode.py` reporting it as never reached.
#: 2025-11-16 was a Sunday, and US Eastern was UTC-5 that week.
KICKOFFS = {
    "early": "2025-11-16T18:00Z",
    "late": "2025-11-16T21:05Z",
    "night": "2025-11-17T01:20Z",
}

PRO_TEAMS = {
    # id: (abbrev, window). Ids match ESPN's real enumeration so the parser is
    # genuinely exercised; the games themselves are invented.
    1: ("ATL", "early"), 2: ("BUF", "early"), 3: ("CHI", "early"), 4: ("CIN", "early"),
    5: ("CLE", "early"), 6: ("DAL", "late"), 7: ("DEN", "late"), 8: ("DET", "early"),
    9: ("GB", "late"), 10: ("TEN", "early"), 11: ("IND", "early"), 12: ("KC", "late"),
    13: ("LV", "late"), 14: ("LAR", "late"), 15: ("MIA", "early"), 16: ("MIN", "early"),
    17: ("NE", "early"), 18: ("NO", "early"), 19: ("NYG", "early"), 20: ("NYJ", "early"),
    21: ("PHI", "night"), 22: ("ARI", "late"), 23: ("PIT", "early"), 24: ("LAC", "late"),
    25: ("SF", "night"), 26: ("SEA", "late"), 27: ("TB", "early"), 28: ("WSH", "early"),
    29: ("CAR", "early"), 30: ("JAX", "late"), 33: ("BAL", "late"), 34: ("HOU", "late"),
}

# Ten invented managers and their invented franchises.
TEAMS = [
    (1, "Regret Merchants", "REGT", "Bex"),
    (2, "The Wounded Ferrets", "FERT", "Chidi"),
    (3, "Vibes Only FC", "VIBE", "Noor"),
    (4, "Statistically Irrelevant", "STAT", "Ollie"),
    (5, "Bench Mob Rule", "MOBR", "Priya"),
    (6, "Sunday Roast", "ROST", "Gus"),
    (7, "Certified Bottlers", "BOTL", "Wren"),
    (8, "Late Swap Larry", "LSWP", "Sam"),
    (9, "Panic at the Flex", "FLEX", "Theo"),
    (10, "Fourth and Forever", "4EVR", "Marguerite"),
]

# Week 11 pairings. Five games, every team playing.
PAIRINGS = [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)]

#: A fourteen week regular season, six of ten teams making the playoffs.
SEASON_WEEKS = 14
PLAYOFF_TEAMS = 6


def round_robin(team_ids: list[int], weeks: int) -> list[list[tuple[int, int]]]:
    """A schedule where everybody plays everybody, then it cycles.

    The circle method: fix one team and rotate the rest. Ten teams gives nine
    distinct rounds, so a fourteen week season repeats the first five -- which is
    exactly what a real ten-team league does, and it is why the Receipts tab's
    luck index has anything to say.
    """
    fixed, rotating = team_ids[0], team_ids[1:]
    schedule: list[list[tuple[int, int]]] = []
    for week in range(weeks):
        order = rotating[week % len(rotating):] + rotating[: week % len(rotating)]
        pairs = [(fixed, order[0])] if week % 2 == 0 else [(order[0], fixed)]
        for i in range(1, len(order) // 2 + 1):
            a, b = order[i], order[len(order) - i]
            pairs.append((a, b) if (week + i) % 2 == 0 else (b, a))
        schedule.append(pairs)
    return schedule


def build_season(rng: random.Random, rosters: dict[int, list[Athlete]]) \
        -> tuple[list[list[tuple[int, int]]], dict[int, list[float]], dict[int, dict]]:
    """The whole season: the fixture list, the settled scores, and the standings.

    Generated *before* the team payloads, because the standings have to be
    derived from the results rather than invented alongside them. Random records
    beside random scores contradict each other the moment anybody adds up a
    column, and the all-play and luck figures are built on exactly that sum.

    Takes the rosters because one of the season's numbers depends on the live
    week: see `_plant_season_high`.
    """
    team_ids = [t[0] for t in TEAMS]
    schedule = round_robin(team_ids, SEASON_WEEKS)

    # Weeks 1 to 10 are settled. Week 11 is the one being replayed, so its scores
    # come from the live simulation rather than from here.
    settled: dict[int, list[float]] = {tid: [] for tid in team_ids}
    for week in range(SCORING_PERIOD - 1):
        for tid in team_ids:
            settled[tid].append(round(max(38.0, rng.gauss(104, 21)), 2))

    _plant_season_high(settled, rosters, rng)
    _plant_a_tie(schedule, settled)

    standings = {tid: {"wins": 0, "losses": 0, "ties": 0, "pf": 0.0, "pa": 0.0}
                 for tid in team_ids}
    for week in range(SCORING_PERIOD - 1):
        for home, away in schedule[week]:
            home_score, away_score = settled[home][week], settled[away][week]
            standings[home]["pf"] += home_score
            standings[home]["pa"] += away_score
            standings[away]["pf"] += away_score
            standings[away]["pa"] += home_score
            if home_score > away_score:
                standings[home]["wins"] += 1
                standings[away]["losses"] += 1
            elif away_score > home_score:
                standings[away]["wins"] += 1
                standings[home]["losses"] += 1
            else:
                standings[home]["ties"] += 1
                standings[away]["ties"] += 1

    for record in standings.values():
        record["pf"] = round(record["pf"], 2)
        record["pa"] = round(record["pa"], 2)
    return schedule, settled, standings


#: Whose week 11 is the best week of his season. Sam, because he already owns the
#: night game and winning it late with a career day is one story rather than two.
SEASON_HIGH_TEAM = 8


def _plant_season_high(settled: dict[int, list[float]],
                       rosters: dict[int, list[Athlete]],
                       rng: random.Random) -> None:
    """Make one manager's live week his best of the season.

    A Legendary card is minted two ways: a win from under ten percent, or a
    season-high score. The first fires on this Sunday and the second never did --
    Sam finished 130.75 against a season best of 131.37, which is a nicer near
    miss than anyone would have written on purpose and left half the rarity rule
    unexercised end to end. The branch had a unit test and had never once run
    against the fixture, which is the same shape of hole as the INJURY detector.

    Clamped rather than redrawn, and only the weeks that were above the line, so
    the rest of his season and everybody else's is untouched. The standings are
    computed after this, from these numbers, so they stay consistent with it.
    """
    squad = rosters.get(SEASON_HIGH_TEAM)
    if not squad:
        return
    live_total = sum(a.target for a in squad if a.is_starter)
    ceiling = live_total - 2.0
    weeks = settled.get(SEASON_HIGH_TEAM)
    if not weeks:
        return
    settled[SEASON_HIGH_TEAM] = [
        score if score <= ceiling else round(ceiling - rng.uniform(0.5, 9.0), 2)
        for score in weeks
    ]


#: Which settled week ends level, and which pairing in it. Week 4, because a tie
#: early enough to be forgotten is funnier than one that decided the season.
TIE_WEEK = 4


def _plant_a_tie(schedule, settled: dict[int, list[float]]) -> None:
    """One week that ended level.

    A fantasy tie is rare, real, and the single most argued-about outcome in any
    league. Every layer here handles it -- the payload writes "TIE", the record
    carries ties, the standings weight them at half a win, all-play counts them
    separately -- and none of that had ever run, because ten gaussian draws a
    week never landed on the same two decimal places. Three branches deep in the
    season maths with no data behind them.
    """
    if len(schedule) < TIE_WEEK:
        return
    home, away = schedule[TIE_WEEK - 1][0]
    settled[away][TIE_WEEK - 1] = settled[home][TIE_WEEK - 1]


def schedule_payload(schedule, settled, rosters, week_done: bool = False) -> dict:
    """An `mSchedule` response: every matchup period of the season.

    Completed weeks carry scores and a winner; the current week carries the live
    totals; future weeks carry the pairing and nothing else, which is what the
    playoff simulator needs and all it needs.
    """
    games = []
    match_id = 0
    for week_index, pairs in enumerate(schedule, start=1):
        for home, away in pairs:
            match_id += 1
            entry = {"id": match_id, "matchupPeriodId": week_index,
                     "home": {"teamId": home}, "away": {"teamId": away}}
            if week_index < SCORING_PERIOD:
                home_score = settled[home][week_index - 1]
                away_score = settled[away][week_index - 1]
                entry["home"]["totalPoints"] = home_score
                entry["away"]["totalPoints"] = away_score
                entry["winner"] = ("HOME" if home_score > away_score
                                   else "AWAY" if away_score > home_score else "TIE")
            elif week_index == SCORING_PERIOD:
                for key, tid in (("home", home), ("away", away)):
                    squad = rosters[tid]
                    entry[key]["totalPoints"] = round(
                        sum(a.points for a in squad if a.is_starter), 2)
                # Undecided while the games are on, settled once they are not.
                # Without this the week stays "remaining" after the final whistle
                # and the playoff simulator keeps re-rolling an afternoon that
                # already happened.
                if not week_done:
                    entry["winner"] = "UNDECIDED"
                else:
                    home_total = entry["home"]["totalPoints"]
                    away_total = entry["away"]["totalPoints"]
                    entry["winner"] = ("HOME" if home_total > away_total
                                       else "AWAY" if away_total > home_total else "TIE")
            else:
                entry["winner"] = "UNDECIDED"
            games.append(entry)
    return {"id": int(LEAGUE_ID), "seasonId": SEASON,
            "scoringPeriodId": SCORING_PERIOD, "schedule": games}

LINEUP_SLOT_COUNTS = {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1, "20": 7}
STARTER_SLOTS = [0, 2, 2, 4, 4, 6, 23, 16, 17]
BENCH_SLOTS = [20] * 7

POSITION_FOR_SLOT = {0: 1, 2: 2, 4: 3, 6: 4, 16: 16, 17: 5, 23: 2}
ELIGIBLE = {1: [0, 20], 2: [2, 23, 20], 3: [4, 23, 20], 4: [6, 23, 20], 5: [17, 20], 16: [16, 20]}

FIRST = [
    "Dax", "Quill", "Rennie", "Tobiah", "Cass", "Marlo", "Ozzie", "Bram", "Fitz",
    "Delroy", "Nate", "Kip", "Sol", "Juno", "Ander", "Rico", "Wes", "Bo", "Trey",
    "Lonnie", "Ash", "Cody", "Ellis", "Gray", "Hollis", "Jax", "Knox", "Linus",
    "Micah", "Nash", "Otis", "Pax", "Quinn", "Roscoe", "Silas", "Tate", "Vance",
    "Wilder", "Xander", "Yusuf", "Zeke", "Arlo", "Brix", "Cove", "Dune",
]
LAST = [
    "Ashgrove", "Bellweather", "Crowfoot", "Dunmore", "Eastcott", "Fairbrother",
    "Greenhalgh", "Hartnell", "Inglewood", "Jessop", "Kettleby", "Longstaff",
    "Marchbank", "Nettleton", "Oakhurst", "Pemberton", "Quarrie", "Ravensworth",
    "Stonebridge", "Thackeray", "Underhill", "Vaughan-Rees", "Wetherby",
    "Yarrowmead", "Ziegler", "Applewhite", "Braithwaite", "Cadwallader",
    "Danforth", "Eversley", "Fenwick", "Garrowby", "Huddleston", "Ilkeston",
]

#: Per position: (mean points, spread, probability any given play is a touchdown).
#:
#: The simulation draws a player's *final* score first and then distributes it
#: across his game as discrete plays, rather than accumulating minute by minute
#: from a rate. Rate-based accumulation compounds: a plausible-looking per-minute
#: probability over a three-hour game produced 860-point fantasy teams on the
#: first attempt, because nothing in the model knew what a normal week looks like.
POSITION_PROFILE = {
    1: (19.0, 7.0, 0.16),   # QB
    2: (11.5, 6.5, 0.13),   # RB
    3: (11.0, 6.5, 0.13),   # WR
    4: (8.0, 4.5, 0.10),    # TE
    5: (8.5, 3.0, 0.00),    # K
    16: (7.5, 4.5, 0.06),   # D/ST
}


def _guid(index: int) -> str:
    """A stable, obviously fake GUID. Real ones identify a real ESPN account."""
    digest = hashlib.sha256(f"punt-demo-owner-{index}".encode()).hexdigest().upper()
    return f"{{{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}}}"


class Athlete:
    __slots__ = (
        "id", "name", "position", "pro_team_id", "window", "slot",
        "projected", "target", "points", "plays", "_next",
        "injury_at", "injury_status", "_status",
    )

    def __init__(self, pid, name, position, pro_team_id, slot, projected, target):
        self.id = pid
        self.name = name
        self.position = position
        self.pro_team_id = pro_team_id
        self.window = PRO_TEAMS[pro_team_id][1]
        self.slot = slot
        self.projected = projected
        #: What this player actually finishes on. Drawn independently of the
        #: projection: the gap between the two is the entire emotional content
        #: of a fantasy week.
        self.target = target
        self.points = 0.0
        #: (minute, delta, kind), built up front by `schedule_plays`.
        self.plays: list[tuple[int, float, str]] = []
        self._next = 0
        #: When this player's afternoon ends early, and how it is reported.
        #: Planted rather than random: without one, `injuryStatus` is "ACTIVE"
        #: for every player all day, the INJURY detector never fires, and fifteen
        #: phrase lines plus a whole branch of the engine have never run against
        #: the data path they exist for.
        self.injury_at: int | None = None
        self.injury_status: str = "OUT"
        self._status = "ACTIVE"

    @property
    def is_starter(self) -> bool:
        return self.slot != 20

    def live_at(self, t: int) -> bool:
        start, end = WINDOWS[self.window]
        return start <= t < end

    def schedule_plays(self, rng: random.Random) -> None:
        """Break this player's final score into plays at random points in his game."""
        self.plays = []
        self._next = 0
        if self.target <= 0:
            return
        start, end = WINDOWS[self.window]
        _, _, td_prob = POSITION_PROFILE[self.position]

        deltas: list[tuple[float, str]] = []
        total = 0.0
        guard = 0
        while total < self.target and guard < 60:
            guard += 1
            roll = rng.random()
            if self.position == 5:
                delta, kind = rng.choice([(3.0, "field_goal"), (3.0, "field_goal"), (1.0, "extra_point")])
            elif self.position == 16:
                delta, kind = rng.choice([(2.0, "sack"), (2.0, "turnover_forced"), (6.0, "defensive_touchdown")])
            elif roll < td_prob:
                delta, kind = 6.0 + rng.randint(1, 78) / 10.0, "touchdown"
            elif roll < td_prob + 0.06:
                delta, kind = -2.0, "turnover"
            elif roll < td_prob + 0.16:
                delta, kind = rng.randint(25, 70) / 10.0, "big_play"
            else:
                delta, kind = rng.randint(2, 22) / 10.0, "gain"
            deltas.append((delta, kind))
            total += delta

        # Trim the overshoot off the last positive play so the final score is the
        # one that was drawn, not the one the loop happened to stop on.
        if deltas and total > self.target:
            for i in range(len(deltas) - 1, -1, -1):
                delta, kind = deltas[i]
                if delta > 0:
                    trimmed = round(max(0.1, delta - (total - self.target)), 2)
                    deltas[i] = (trimmed, kind if trimmed >= 6.0 or kind != "touchdown" else "gain")
                    break

        minutes = sorted(rng.randint(start, max(start, end - 60)) // 60 * 60 for _ in deltas)
        self.plays = [(m, round(d, 2), k) for m, (d, k) in zip(minutes, deltas)]

    def advance_to(self, t: int) -> list[tuple[float, str]]:
        """Every play that has happened by minute `t` and not yet been applied."""
        if self.injury_at is not None and t >= self.injury_at:
            # Reported hurt, and done scoring. A player who keeps accumulating
            # points after being ruled out would make the feed contradict itself.
            self._status = self.injury_status
            self._next = len(self.plays)
            return []
        fired: list[tuple[float, str]] = []
        while self._next < len(self.plays) and self.plays[self._next][0] <= t:
            _, delta, kind = self.plays[self._next]
            self.points = round(self.points + delta, 2)
            fired.append((delta, kind))
            self._next += 1
        return fired

    def entry(self) -> dict:
        eligible = ELIGIBLE.get(self.position, [20])
        return {
            "lineupSlotId": self.slot,
            "playerId": self.id,
            "appliedStatTotal": round(self.points, 2),
            "playerPoolEntry": {
                "player": {
                    "id": self.id,
                    "fullName": self.name,
                    "defaultPositionId": self.position,
                    "proTeamId": self.pro_team_id,
                    "eligibleSlots": eligible,
                    "injuryStatus": self._status,
                    "stats": [
                        {
                            "scoringPeriodId": SCORING_PERIOD,
                            "statSourceId": 0,
                            "statSplitTypeId": 1,
                            "appliedTotal": round(self.points, 2),
                        },
                        {
                            "scoringPeriodId": SCORING_PERIOD,
                            "statSourceId": 1,
                            "statSplitTypeId": 1,
                            "appliedTotal": round(self.projected, 2),
                        },
                    ],
                }
            },
        }


def build_rosters(rng: random.Random) -> dict[int, list[Athlete]]:
    """Sixteen invented athletes per franchise, then a few deliberate storylines."""
    used_names: set[str] = set()
    pid = 1000
    rosters: dict[int, list[Athlete]] = {}

    for team_id, *_ in TEAMS:
        squad: list[Athlete] = []
        for slot in STARTER_SLOTS + BENCH_SLOTS:
            pid += 1
            if slot == 20:
                # A bench is mostly skill positions, which is what makes bench
                # regret possible in the first place.
                position = rng.choice([1, 2, 2, 3, 3, 4])
            else:
                position = POSITION_FOR_SLOT[slot]

            while True:
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                if name not in used_names:
                    used_names.add(name)
                    break

            pro_team_id = rng.choice(list(PRO_TEAMS))
            mean, spread, _ = POSITION_PROFILE[position]
            projected = max(1.0, rng.gauss(mean, spread * 0.4))
            target = max(0.0, rng.gauss(mean * (0.85 if slot == 20 else 1.0), spread))
            squad.append(Athlete(pid, name, position, pro_team_id, slot, projected, target))
        rosters[team_id] = squad

    _plant_storylines(rosters, rng)
    for squad in rosters.values():
        for athlete in squad:
            athlete.schedule_plays(rng)
    return rosters


def _plant_storylines(rosters: dict[int, list[Athlete]], rng: random.Random) -> None:
    """Guarantee one of each event the engine has to detect.

    Left to chance, a seeded four-hour simulation might contain no bench disaster
    at all, and the Phase 2 test for the funniest event in the app would pass by
    asserting nothing. These are the fixture's fixed points, and the tests name
    the players involved.
    """
    # Priya (team 5, "Bench Mob Rule") benches the day's biggest score.
    priya = rosters[5]
    hero = next(a for a in priya if not a.is_starter and a.position in (2, 3))
    hero.target, hero.projected = 41.2, 6.0
    victim = next(a for a in priya if a.slot == 23)  # her flex
    victim.target, victim.projected = 1.4, 12.0

    # Gus (team 6) starts a player who does nothing whatsoever: the goose egg.
    goose = next(a for a in rosters[6] if a.slot == 4)
    goose.target, goose.projected = 0.0, 11.5

    # Wren (team 7) is out of it by mid-afternoon: the DOOM path.
    for athlete in rosters[7]:
        if athlete.is_starter:
            athlete.target *= 0.55

    # Two starters get hurt, and one bench player does. The bench one is there to
    # prove the detector's starter-only filter end to end: an injury to somebody
    # nobody is starting is not news.
    hurt_early = next(a for a in rosters[2] if a.slot == 2)          # Chidi's RB
    hurt_early.injury_at = PREGAME + 70 * 60                          # early second quarter
    hurt_early.target *= 0.3

    hurt_late = next(a for a in rosters[9] if a.slot == 4)            # Theo's WR
    hurt_late.injury_at = PREGAME + 150 * 60
    hurt_late.injury_status = "DOUBTFUL"
    hurt_late.target *= 0.6

    bench_knock = next(a for a in rosters[3] if not a.is_starter)     # Noor's bench
    bench_knock.injury_at = PREGAME + 100 * 60

    # Bex (team 1) stacks his quarterback, receiver and tight end on one pro
    # team. Left to a uniform draw over thirty-two franchises nobody ever had
    # three starters in the same NFL game, so the Cheer tab could never say
    # "you have A, B and one more" -- the truncation that stops a verdict
    # becoming a paragraph had no data to truncate.
    stack_qb = next(a for a in rosters[1] if a.slot == 0)
    for slot in (4, 6):
        teammate = next(a for a in rosters[1] if a.slot == slot)
        teammate.pro_team_id = stack_qb.pro_team_id
        teammate.window = stack_qb.window

    # Sam (team 8) plays the whole of the night game and wins it late, which is
    # the lead change the swing tab exists for. His opponent Wren finished hours
    # ago, so the win probability curve does something worth drawing.
    late_hero = next(a for a in rosters[8] if a.slot == 0)
    late_hero.pro_team_id = 25
    late_hero.window = "night"
    late_hero.target, late_hero.projected = 34.5, 21.0


def boxscore_payload(rosters: dict[int, list[Athlete]], t: int) -> dict:
    """One `mMatchupScore&mBoxscore` response, shaped as ESPN shapes it."""
    schedule = []
    for index, (home_id, away_id) in enumerate(PAIRINGS, start=1):
        sides = {}
        for key, team_id in (("home", home_id), ("away", away_id)):
            squad = rosters[team_id]
            total = round(sum(a.points for a in squad if a.is_starter), 2)
            projected = round(
                total + sum(max(0.0, a.projected - a.points) for a in squad if a.is_starter), 2
            )
            sides[key] = {
                "teamId": team_id,
                "totalPoints": total,
                "totalProjectedPointsLive": projected,
                "rosterForCurrentScoringPeriod": {"entries": [a.entry() for a in squad]},
            }
        schedule.append(
            {
                "id": index,
                "matchupPeriodId": MATCHUP_PERIOD,
                "winner": "UNDECIDED" if t < DAY_END else _winner(sides),
                **sides,
            }
        )
    return {
        "id": int(LEAGUE_ID),
        "seasonId": SEASON,
        "scoringPeriodId": SCORING_PERIOD,
        "schedule": schedule,
    }


def _winner(sides: dict) -> str:
    home, away = sides["home"]["totalPoints"], sides["away"]["totalPoints"]
    if home > away:
        return "HOME"
    return "AWAY" if away > home else "TIE"


#: Which pro teams face which, so the scoreboard has real-looking fixtures.
#: Pairs within a window, in the order `PRO_TEAMS` lists them.
def _pairings_by_window() -> dict[str, list[tuple[int, int]]]:
    grouped: dict[str, list[int]] = {}
    for team_id, (_, window) in PRO_TEAMS.items():
        grouped.setdefault(window, []).append(team_id)
    for window, ids in grouped.items():
        # An odd window silently drops a team from the scoreboard, and every
        # player on it then has an unknown game state: their projection never
        # settles and their manager is told all night that somebody is still to
        # play. Caught exactly that way the first time this ran.
        if len(ids) % 2:
            raise ValueError(f"window {window!r} has {len(ids)} teams; every game needs two")
    return {w: list(zip(ids[0::2], ids[1::2])) for w, ids in grouped.items()}


NFL_PAIRINGS = _pairings_by_window()


def nfl_payload(t: int, rng: random.Random) -> dict:
    """One `site.api` NFL scoreboard response.

    Without this feed the fantasy data cannot tell "scored nothing" from "has not
    kicked off", so a settled Sunday night still reports four players left to play
    for every manager and every projection sits above every final score. It is
    also where possession, down and distance and the red zone come from, which is
    what the countdown overlay is triggered off.
    """
    events = []
    for window, pairs in NFL_PAIRINGS.items():
        start, end = WINDOWS[window]
        if t < start:
            state, completed, period, clock = "pre", False, 0, "0:00"
        elif t >= end:
            state, completed, period, clock = "post", True, 4, "0:00"
        else:
            elapsed = (t - start) / (end - start)
            state, completed = "in", False
            period = min(4, int(elapsed * 4) + 1)
            remaining = 15 * (1 - (elapsed * 4) % 1)
            clock = f"{int(remaining)}:{int((remaining % 1) * 60):02d}"

        for home_id, away_id in pairs:
            # Seeded off the game and a two-minute bucket. Per-quarter was
            # stable but wrong: a red-zone trip lasts a couple of minutes, and
            # seeding per quarter left a game inside the five for fifteen
            # minutes of game time, which would pin the countdown overlay open
            # for most of an afternoon.
            bucket = t // 120
            situation_rng = random.Random(f"{home_id}-{away_id}-{period}")
            drive_rng = random.Random(f"{home_id}-{away_id}-{bucket}")
            possession_id = home_id if drive_rng.random() < 0.5 else away_id
            red_zone = state == "in" and drive_rng.random() < 0.07
            competition = {
                "competitors": [
                    {
                        "homeAway": "home",
                        "score": str(int(7 * period * situation_rng.random() + 3)),
                        "team": {"id": str(home_id), "abbreviation": PRO_TEAMS[home_id][0]},
                    },
                    {
                        "homeAway": "away",
                        "score": str(int(7 * period * situation_rng.random() + 3)),
                        "team": {"id": str(away_id), "abbreviation": PRO_TEAMS[away_id][0]},
                    },
                ]
            }
            if state == "in":
                competition["situation"] = {
                    "down": situation_rng.randint(1, 4),
                    "distance": situation_rng.randint(1, 15),
                    "isRedZone": red_zone,
                    "possession": str(possession_id),
                    "shortDownDistanceText": f"{situation_rng.randint(1, 4)}rd & {situation_rng.randint(1, 15)}",
                }
            events.append(
                {
                    "id": f"{home_id}{away_id}",
                    "name": f"{PRO_TEAMS[away_id][0]} at {PRO_TEAMS[home_id][0]}",
                    "date": KICKOFFS[window],
                    "status": {
                        "period": period,
                        "displayClock": clock,
                        "type": {"state": state, "completed": completed},
                    },
                    "competitions": [competition],
                }
            )
    return {"events": events}


def settings_payload() -> dict:
    return {
        "id": int(LEAGUE_ID),
        "seasonId": SEASON,
        "scoringPeriodId": SCORING_PERIOD,
        "status": {"currentMatchupPeriod": MATCHUP_PERIOD, "latestScoringPeriod": SCORING_PERIOD},
        "settings": {
            "name": "The Feathers Sunday League",
            "size": len(TEAMS),
            "rosterSettings": {"lineupSlotCounts": LINEUP_SLOT_COUNTS},
            "scheduleSettings": {"matchupPeriodCount": 14, "playoffTeamCount": 6},
            "scoringSettings": {"scoringType": "H2H_POINTS"},
        },
        "members": [
            {"id": _guid(i), "displayName": manager, "firstName": manager, "lastName": ""}
            for i, (_, _, _, manager) in enumerate(TEAMS)
        ],
    }


def team_payload(rng: random.Random, standings: dict[int, dict]) -> dict:
    """`mTeam` plus the members block, which is how owner names reach a card.

    Records come from `standings`, which was computed by playing the season out.
    Inventing them here instead would contradict the schedule the moment anybody
    added up a column -- and the luck index is exactly that sum.
    """
    teams = []
    for i, (team_id, name, abbrev, _manager) in enumerate(TEAMS):
        record = standings[team_id]
        wins, losses, ties = record["wins"], record["losses"], record["ties"]
        teams.append(
            {
                "id": team_id,
                "abbrev": abbrev,
                "name": name,
                # Two managers have never uploaded a logo. That is the realistic
                # rate, and it is why the monogram fallback is a designed state.
                "logo": "" if team_id in (4, 9) else f"https://g.espncdn.com/lm-static/logo-packs/core/demo-{team_id}.svg",
                "owners": [_guid(i)],
                "divisionId": 0 if team_id <= 5 else 1,
                "playoffSeed": 0,
                "record": {
                    "overall": {
                        "wins": wins,
                        "losses": losses,
                        "ties": ties,
                        "pointsFor": record["pf"],
                        "pointsAgainst": record["pa"],
                    }
                },
            }
        )
    payload = settings_payload()
    payload["teams"] = teams
    return payload


# -- the front office ----------------------------------------------------------
#
# What the four decision panels read: the draft, the moves since, next week's
# rosters, the NFL byes and a box score for every settled week. Invented to the
# same rules as everything else here -- consistent with itself, seeded, and
# planted with one of every case the panels have to handle.

OFFICE_SEED = SEED + 404
NEXT_PERIOD = SCORING_PERIOD + 1
DRAFT_ROUNDS = 17
#: Picks a team made that are no longer on its roster: three replaced by waiver
#: claims and one simply cut. 16 - PICKUPS + GONE is the seventeen rounds.
GONE_PER_TEAM = 4
PICKUPS_PER_TEAM = 3
#: Draft-day value by position, relative to projection. Quarterbacks score the
#: most and go later than that suggests; kickers and defences go last.
DRAFT_WEIGHT = {1: 0.62, 2: 1.12, 3: 1.05, 4: 0.9, 5: 0.2, 16: 0.25}
#: How far ahead of what each team's starters actually deliver ESPN projects
#: them. Positive is a projection that oversells the team. Planted wide so
#: Promise vs delivery has a clear best and worst.
PROJECTION_BIAS = {1: -0.10, 2: 0.04, 3: 0.12, 4: -0.02, 5: 0.07,
                   6: -0.14, 7: 0.02, 8: -0.05, 9: 0.09, 10: 0.0}
#: Next week's injuries: (team, roster index, status). Starters are indexes 0-8
#: in `STARTER_SLOTS` order. One of each designation, and one on a bench, so
#: the look-ahead has to know an injured backup is no fix.
HOLES = [(1, 3, "OUT"), (3, 0, "QUESTIONABLE"), (9, 1, "DOUBTFUL"),
         (5, 6, "INJURY_RESERVE"), (1, 10, "OUT")]
#: A starter ESPN projects for nothing, with no designation to explain it.
ZERO_HOLE = (4, 8)
#: The trade: team a, team b, week. One each way, both bench players.
TRADE = (2, 7, 6)


def front_office(rosters: dict[int, list[Athlete]], schedule, settled) \
        -> list[tuple[str, int | None, dict]]:
    """Every front-office payload, as (feed, scoring period or None, payload)."""
    rng = random.Random(OFFICE_SEED)
    team_ids = [t[0] for t in TEAMS]
    used = {a.name for squad in rosters.values() for a in squad}
    weeks = list(range(1, SCORING_PERIOD))

    # Drafted, and gone since.
    gone: dict[int, list[Athlete]] = {}
    pid = 9000
    for tid in team_ids:
        gone[tid] = []
        for _ in range(GONE_PER_TEAM):
            pid += 1
            while True:
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                if name not in used:
                    used.add(name)
                    break
            position = rng.choice([1, 2, 2, 3, 3, 4])
            mean, spread, _ = POSITION_PROFILE[position]
            gone[tid].append(Athlete(pid, name, position, rng.choice(list(PRO_TEAMS)), 20,
                                     max(1.0, rng.gauss(mean, spread * 0.4)), 0.0))

    # Off the wire: bench players only, so every lineup the Sunday replays stays
    # exactly the lineup it was.
    pickups = {tid: rng.sample([a for a in rosters[tid] if not a.is_starter], PICKUPS_PER_TEAM)
               for tid in team_ids}

    # Every player's settled weeks.
    weekly: dict[int, dict[int, list[float]]] = {}
    for tid in team_ids:
        for athlete in rosters[tid] + gone[tid]:
            mean, spread, _ = POSITION_PROFILE[athlete.position]
            weekly[athlete.id] = {
                week: [max(0.0, rng.gauss(mean, spread)),
                       max(0.5, mean * (1 + PROJECTION_BIAS[tid]) * rng.uniform(0.88, 1.12))]
                for week in weeks
            }
    # A settled week's starters add up to that week's settled score, or the box
    # score and the season grid would disagree about the same week.
    for tid in team_ids:
        starters = [a for a in rosters[tid] if a.is_starter]
        for week in weeks:
            raw = [weekly[a.id][week][0] for a in starters]
            target = settled[tid][week - 1]
            scale = target / sum(raw) if sum(raw) > 0 else 0.0
            values = [round(v * scale, 2) for v in raw]
            values[0] = round(values[0] + target - sum(values), 2)
            for athlete, value in zip(starters, values):
                weekly[athlete.id][week][0] = value

    # The draft: seventeen picks a team, kickers and defences last, snake order.
    picks_by_team: dict[int, list[Athlete]] = {}
    for tid in team_ids:
        drafted = [a for a in rosters[tid] if a not in pickups[tid]] + gone[tid]
        value = {a.id: a.projected * DRAFT_WEIGHT[a.position] + rng.uniform(-2.5, 2.5) for a in drafted}
        picks_by_team[tid] = sorted(drafted, key=lambda a: (a.position in (5, 16), -value[a.id]))
    trade_a, trade_b, trade_week = TRADE
    # Each side of the trade was drafted by the other team.
    x = next(a for a in picks_by_team[trade_a] if not a.is_starter and a in rosters[trade_a])
    y = next(a for a in picks_by_team[trade_b] if not a.is_starter and a in rosters[trade_b])
    ix, iy = picks_by_team[trade_a].index(x), picks_by_team[trade_b].index(y)
    picks_by_team[trade_a][ix], picks_by_team[trade_b][iy] = y, x

    picks = []
    for rnd in range(1, DRAFT_ROUNDS + 1):
        order = team_ids if rnd % 2 else list(reversed(team_ids))
        for position, tid in enumerate(order, start=1):
            athlete = picks_by_team[tid][rnd - 1]
            picks.append({
                "id": len(picks) + 1, "overallPickNumber": (rnd - 1) * len(team_ids) + position,
                "roundId": rnd, "roundPickNumber": position, "teamId": tid,
                "playerId": athlete.id, "keeper": (tid, rnd) == (4, 1),
                "autoDraftTypeId": 0, "bidAmount": 0, "lineupSlotId": 0,
                "nominatingTeamId": 0, "reservedForKeeper": False, "tradeLocked": False,
            })
    draft = {"id": int(LEAGUE_ID), "seasonId": SEASON,
             "draftDetail": {"drafted": True, "inProgress": False, "picks": picks}}

    # The moves. Each pickup cost a gone player, the last gone player was simply
    # cut, and three records that are not moves at all ride along to be ignored.
    txs: list[dict] = []

    def item(kind: str, athlete: Athlete, from_team: int, to_team: int) -> dict:
        return {"type": kind, "playerId": athlete.id, "fromTeamId": from_team, "toTeamId": to_team,
                "fromLineupSlotId": -1, "toLineupSlotId": -1, "isKeeper": False, "overallPickNumber": 0}

    def tx(kind: str, week: int, team: int, items: list[dict], status: str = "EXECUTED") -> None:
        txs.append({"id": f"demo-{len(txs) + 1:04d}", "type": kind, "status": status,
                    "scoringPeriodId": week, "teamId": team, "bidAmount": 0,
                    "executionType": "EXECUTE", "isPending": False,
                    "proposedDate": 1_757_000_000_000 + week * 604_800_000 + len(txs) * 60_000,
                    "items": items})

    for tid in team_ids:
        when = sorted(rng.sample(range(2, SCORING_PERIOD), PICKUPS_PER_TEAM + 1))
        for n, athlete in enumerate(pickups[tid]):
            tx(rng.choice(["WAIVER", "FREEAGENT"]), when[n], tid,
               [item("ADD", athlete, -1, tid), item("DROP", gone[tid][n], tid, -1)])
        tx("FREEAGENT", when[-1], tid, [item("DROP", gone[tid][-1], tid, -1)])
    tx("TRADE_ACCEPT", trade_week, trade_a,
       [item("TRADE", y, trade_a, trade_b), item("TRADE", x, trade_b, trade_a)])
    tx("WAIVER", 5, 3, [item("ADD", gone[1][0], -1, 3)], status="FAILED_ROSTERLIMIT")
    tx("ROSTER", 7, 5, [item("LINEUP", rosters[5][9], 5, 5)])
    tx("DRAFT", 1, 8, [item("DRAFT", rosters[8][0], -1, 8)])
    transactions = {"id": int(LEAGUE_ID), "seasonId": SEASON,
                    "scoringPeriodId": SCORING_PERIOD, "transactions": txs}

    # Every drafted or moved player's weeks, wherever he is now. The season
    # split rides along because ESPN sends one, and it has to be ignored.
    on_team = {a.id: tid for tid in team_ids for a in rosters[tid]}
    players = []
    for tid in team_ids:
        for athlete in rosters[tid] + gone[tid]:
            stats = []
            for week in weeks:
                actual, projected = weekly[athlete.id][week]
                stats.append({"seasonId": SEASON, "scoringPeriodId": week, "statSourceId": 0,
                              "statSplitTypeId": 1, "appliedTotal": round(actual, 2)})
                stats.append({"seasonId": SEASON, "scoringPeriodId": week, "statSourceId": 1,
                              "statSplitTypeId": 1, "appliedTotal": round(projected, 2)})
            stats.append({"seasonId": SEASON, "scoringPeriodId": 0, "statSourceId": 0, "statSplitTypeId": 0,
                          "appliedTotal": round(sum(weekly[athlete.id][w][0] for w in weeks), 2)})
            players.append({"id": athlete.id, "onTeamId": on_team.get(athlete.id, 0),
                            "player": {"id": athlete.id, "fullName": athlete.name,
                                       "defaultPositionId": athlete.position,
                                       "proTeamId": athlete.pro_team_id, "stats": stats}})
    history = {"players": players}

    # Byes. Nobody's is this week, and one team's is next week: the one the
    # Sunday Roast tight end plays for, so at least one lineup has a bye in it.
    bye_team = rosters[6][5].pro_team_id
    bye_weeks = [5, 6, 7, 8, 9, 10, 13, 14]
    byes = {pro: bye_weeks[n % len(bye_weeks)] for n, pro in enumerate(sorted(PRO_TEAMS))}
    byes[bye_team] = NEXT_PERIOD
    pro_schedule = {"settings": {"proTeams": [
        {"id": pro, "abbrev": PRO_TEAMS[pro][0], "byeWeek": byes[pro]} for pro in sorted(PRO_TEAMS)]}}

    # Next week's rosters: next week's projections, and next week's injuries.
    status_for = {(team, index): status for team, index, status in HOLES}

    def next_entry(athlete: Athlete, tid: int, index: int) -> dict:
        status = status_for.get((tid, index), "ACTIVE")
        projected = round(max(0.5, athlete.projected * rng.uniform(0.85, 1.15)), 2)
        if status in ("OUT", "INJURY_RESERVE") or byes.get(athlete.pro_team_id) == NEXT_PERIOD \
                or (tid, index) == ZERO_HOLE:
            projected = 0.0
        elif status == "DOUBTFUL":
            projected = round(projected * 0.25, 2)
        elif status == "QUESTIONABLE":
            projected = round(projected * 0.8, 2)
        return {"lineupSlotId": athlete.slot, "playerId": athlete.id, "playerPoolEntry": {"player": {
            "id": athlete.id, "fullName": athlete.name, "defaultPositionId": athlete.position,
            "proTeamId": athlete.pro_team_id, "eligibleSlots": ELIGIBLE.get(athlete.position, [20]),
            "injuryStatus": status,
            "stats": [{"seasonId": SEASON, "scoringPeriodId": NEXT_PERIOD, "statSourceId": 1,
                       "statSplitTypeId": 1, "appliedTotal": projected}]}}}

    next_rosters = {"id": int(LEAGUE_ID), "seasonId": SEASON, "scoringPeriodId": NEXT_PERIOD,
                    "teams": [{"id": tid, "roster": {"entries": [
                        next_entry(a, tid, i) for i, a in enumerate(rosters[tid])]}}
                        for tid in team_ids]}

    # A box score for every settled week.
    def week_entry(athlete: Athlete, week: int) -> dict:
        actual, projected = weekly[athlete.id][week]
        return {"lineupSlotId": athlete.slot, "playerId": athlete.id,
                "appliedStatTotal": round(actual, 2), "playerPoolEntry": {"player": {
                    "id": athlete.id, "fullName": athlete.name, "defaultPositionId": athlete.position,
                    "proTeamId": athlete.pro_team_id, "eligibleSlots": ELIGIBLE.get(athlete.position, [20]),
                    "injuryStatus": "ACTIVE",
                    "stats": [{"scoringPeriodId": week, "statSourceId": 0, "statSplitTypeId": 1,
                               "appliedTotal": round(actual, 2)},
                              {"scoringPeriodId": week, "statSourceId": 1, "statSplitTypeId": 1,
                               "appliedTotal": round(projected, 2)}]}}}

    out: list[tuple[str, int | None, dict]] = [
        ("mDraftDetail", None, draft),
        ("mTransactions2", SCORING_PERIOD, transactions),
        ("kona_player_history", None, history),
        ("proTeamSchedules_wl", None, pro_schedule),
        ("mRoster", NEXT_PERIOD, next_rosters),
    ]
    for week in weeks:
        games = []
        for n, (home, away) in enumerate(schedule[week - 1], start=1):
            home_score, away_score = settled[home][week - 1], settled[away][week - 1]
            games.append({
                "id": n, "matchupPeriodId": week,
                "winner": "HOME" if home_score > away_score else "AWAY" if away_score > home_score else "TIE",
                "home": {"teamId": home, "totalPoints": home_score,
                         "rosterForMatchupPeriod": {"entries": [week_entry(a, week) for a in rosters[home]]}},
                "away": {"teamId": away, "totalPoints": away_score,
                         "rosterForMatchupPeriod": {"entries": [week_entry(a, week) for a in rosters[away]]}},
            })
        out.append(("mBoxscoreWeek", week, {"id": int(LEAGUE_ID), "seasonId": SEASON,
                                            "scoringPeriodId": week, "schedule": games}))
    return out


def generate(directory: Path) -> tuple[Recording, dict[str, dict]]:
    rng = random.Random(SEED)
    rosters = build_rosters(rng)

    payloads: dict[str, dict] = {}
    entries: list[Entry] = []
    seq = 0

    def add(feed: str, payload: dict, offset: float, per_week: bool, period: int | None = None) -> None:
        nonlocal seq
        filename = f"{seq:04d}_{feed}.json.gz"
        payloads[filename] = payload
        entries.append(
            Entry(
                seq=seq,
                feed=feed,
                offset=offset,
                file=filename,
                captured_at="",
                scoring_period=(period or SCORING_PERIOD) if per_week else None,
            )
        )
        seq += 1

    schedule, settled, standings = build_season(rng, rosters)

    add("mSettings", settings_payload(), 0.0, per_week=False)
    add("mTeam", team_payload(rng, standings), 0.0, per_week=False)
    add("mSchedule", schedule_payload(schedule, settled, rosters), 0.0, per_week=False)

    last_signature: str | None = None
    last_nfl_signature: str | None = None
    schedule_settled = False
    for t in range(0, DAY_END + STEP, STEP):
        for squad in rosters.values():
            for athlete in squad:
                athlete.advance_to(t)

        # The two feeds are polled independently, and this block sits *before*
        # the boxscore dedup for that reason. Below it, a minute in which nobody
        # scored also dropped the NFL state change that happened in the same
        # minute -- so the last game of the night stayed "in progress" for ten
        # minutes after it ended, every player on it kept a live projection, and
        # a settled matchup came back from the simulator at 91% instead of 100%.
        nfl = nfl_payload(t, rng)
        nfl_signature = json.dumps(
            [[e["status"]["type"]["state"], e["status"]["period"]] for e in nfl["events"]]
        )
        if nfl_signature != last_nfl_signature:
            last_nfl_signature = nfl_signature
            add("nfl_scoreboard", nfl, float(t), per_week=False)

        # The season grid is rewritten the moment the last game ends, not at the
        # end of the capture. Otherwise the week stays "remaining" for the gap
        # between the final whistle and the recording stopping, and the playoff
        # simulator spends it re-rolling an afternoon that is over.
        if not schedule_settled and all(
            e["status"]["type"]["completed"] for e in nfl["events"]
        ):
            schedule_settled = True
            add("mSchedule", schedule_payload(schedule, settled, rosters, week_done=True),
                float(t), per_week=False)

        payload = boxscore_payload(rosters, t)
        # Nothing changed in this minute means no new payload: that is both what
        # a real poll would see and a straightforward halving of the fixture.
        signature = json.dumps(
            [[s["home"]["totalPoints"], s["away"]["totalPoints"]] for s in payload["schedule"]]
            + [round(a.points, 2) for squad in rosters.values() for a in squad]
        )
        if signature == last_signature and t not in (0, DAY_END):
            continue
        last_signature = signature
        add("mMatchupScore", payload, float(t), per_week=True)

    # The front office, filed after everything above and drawn from its own
    # generator, so the Sunday -- every payload, every Moment, the golden
    # timeline -- is byte for byte what it was before these panels existed.
    for feed, period, office_payload in front_office(rosters, schedule, settled):
        add(feed, office_payload, 0.0, per_week=period is not None, period=period)

    recording = Recording(
        name=DEMO_RECORDING,
        directory=directory,
        season=SEASON,
        league_id=LEAGUE_ID,
        scoring_period=SCORING_PERIOD,
        synthetic=True,
        description=(
            "Synthetic week 11 Sunday for a ten-team league. Entirely invented: no real "
            "athlete, franchise or person appears. Generated by tools/make_fixture.py "
            f"with seed {SEED}, sampled every {STEP}s of game time from 13:00 to 20:30 ET. "
            "Contains a planted bench disaster (Priya), a goose egg (Gus), a doomed "
            "manager (Wren) and a Sunday-night lead change (Sam), which is also his\n            "
            "best week of the season."
        ),
        created="2025-11-16T18:00:00+00:00",
        entries=entries,
    )
    return recording, payloads


def summarise(recording: Recording, payloads: dict[str, dict]) -> str:
    # The last entry is whichever feed was written last, which is not necessarily
    # the boxscore; ask for the feed by name rather than by position.
    final = payloads[[e for e in recording.entries if e.feed == "mMatchupScore"][-1].file]
    counts: dict[str, int] = {}
    for entry in recording.entries:
        counts[entry.feed] = counts.get(entry.feed, 0) + 1
    lines = [
        f"{recording.name}: {len(recording.entries)} payloads "
        f"({', '.join(f'{n}x {f}' for f, n in sorted(counts.items()))}), "
        f"{recording.duration / 3600:.1f}h of game time"
    ]
    names = {t[0]: t[1] for t in TEAMS}
    managers = {t[0]: t[3] for t in TEAMS}
    for game in final["schedule"]:
        h, a = game["home"], game["away"]
        lines.append(
            f"  {managers[h['teamId']]:<11}{names[h['teamId']]:<26}{h['totalPoints']:7.2f}"
            f"   vs {a['totalPoints']:7.2f}  {names[a['teamId']]:<26}{managers[a['teamId']]}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the committed fixture is current")
    parser.add_argument("--out", default=str(RECORDINGS_DIR / DEMO_RECORDING))
    args = parser.parse_args()

    directory = Path(args.out)
    recording, payloads = generate(directory)

    if args.check:
        try:
            existing = Recording.load(str(directory))
        except (FileNotFoundError, OSError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        if len(existing.entries) != len(recording.entries):
            print(
                f"FAIL: committed fixture has {len(existing.entries)} payloads, "
                f"generator produces {len(recording.entries)}. Regenerate it.",
                file=sys.stderr,
            )
            return 1

        # Nothing in the directory but the manifest and the payloads it names.
        #
        # This repository lives under an iCloud-synced Documents folder, and
        # regenerating the fixture in place while iCloud was mid-sync produced
        # conflict copies -- "0002_mMatchupScore 2.json.gz" and so on. Ninety-nine
        # of them reached a commit before anybody looked at a file listing. They
        # are invisible to every other check here, because the manifest does not
        # mention them and nothing ever reads them.
        expected = {e.file for e in recording.entries} | {"manifest.json"}
        swept = sweep_conflict_copies(directory, expected)
        if swept:
            print(f"  swept {len(swept)} iCloud conflict copies before checking")
        actual = {p.name for p in directory.iterdir() if p.is_file() and p.name != ".DS_Store"}
        strays = sorted(actual - expected)
        if strays:
            print(
                f"FAIL: {len(strays)} file(s) in {directory.name} that the manifest does "
                f"not name, e.g. {strays[:3]}. Delete the directory and regenerate.",
                file=sys.stderr,
            )
            return 1
        missing = sorted(expected - actual)
        if missing:
            print(f"FAIL: {len(missing)} payload(s) missing, e.g. {missing[:3]}", file=sys.stderr)
            return 1

        print(f"OK: {directory.name} matches the generator "
              f"({len(recording.entries)} payloads, no strays)")
        return 0

    write_recording(recording, payloads)
    keep = {e.file for e in recording.entries} | {"manifest.json"}
    swept = sweep_conflict_copies(directory, keep)
    if swept:
        print(f"  swept {len(swept)} iCloud conflict copies, e.g. {swept[:2]}")
    orphans = sweep_orphans(directory, keep)
    if orphans:
        print(f"  swept {len(orphans)} payload(s) from a previous generation, e.g. {orphans[:2]}")
    size = sum(p.stat().st_size for p in directory.iterdir()) / 1e6
    print(summarise(recording, payloads))
    print(f"  written to {directory} ({size:.1f} MB on disk)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
