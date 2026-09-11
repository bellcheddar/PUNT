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
                    "injuryStatus": "ACTIVE",
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


def team_payload(rng: random.Random) -> dict:
    """`mTeam` plus the members block, which is how owner names reach a card."""
    teams = []
    for i, (team_id, name, abbrev, _manager) in enumerate(TEAMS):
        wins = rng.randint(2, 8)
        losses = 10 - wins
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
                        "ties": 0,
                        "pointsFor": round(rng.uniform(880, 1180), 2),
                        "pointsAgainst": round(rng.uniform(880, 1180), 2),
                    }
                },
            }
        )
    payload = settings_payload()
    payload["teams"] = teams
    return payload


def generate(directory: Path) -> tuple[Recording, dict[str, dict]]:
    rng = random.Random(SEED)
    rosters = build_rosters(rng)

    payloads: dict[str, dict] = {}
    entries: list[Entry] = []
    seq = 0

    def add(feed: str, payload: dict, offset: float, per_week: bool) -> None:
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
                scoring_period=SCORING_PERIOD if per_week else None,
            )
        )
        seq += 1

    add("mSettings", settings_payload(), 0.0, per_week=False)
    add("mTeam", team_payload(rng), 0.0, per_week=False)

    last_signature: str | None = None
    last_nfl_signature: str | None = None
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
            "manager (Wren) and a Sunday-night lead change (Sam)."
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
    swept = sweep_conflict_copies(directory, {e.file for e in recording.entries} | {"manifest.json"})
    if swept:
        print(f"  swept {len(swept)} iCloud conflict copies, e.g. {swept[:2]}")
    size = sum(p.stat().st_size for p in directory.iterdir()) / 1e6
    print(summarise(recording, payloads))
    print(f"  written to {directory} ({size:.1f} MB on disk)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
