"""What has changed since the last poll, as one line at a time.

The Moment engine answers "did something happen": a touchdown, a lead change, a
bench disaster. This answers a different and duller question that turns out to
matter as much in a room -- "what has moved" -- and the difference is that a
number can move a long way without anything happening. Nobody's win probability
falls twelve points in a single play; it falls twelve points over twenty minutes
of the other bloke's running back grinding out first downs, and there is no
Moment anywhere in that.

So this is a differ, not a detector. It keeps the last value of everything worth
watching per team and emits a Change when one of them moves past a threshold.
Moments are folded in as well, because the room wants one ticker and not two.

Direction is the whole point of the colouring, so every Change carries `good`
explicitly rather than letting the template infer it from the sign. Some of
these invert: bench regret going UP is bad, and a rank going DOWN in number is
good. A template that read the sign would get both of those backwards.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: How far a thing has to move before it is worth a line.
#:
#: These are deliberately not tiny. The ticker shows one item at a time and
#: rotates every few seconds, so its real capacity is roughly fifteen lines a
#: minute: anything that fires more often than that is not informing the room,
#: it is evicting the line the room was reading. Every one of these was set by
#: counting what a full replayed Sunday produces, not by taste.
SCORE_DELTA = 3.0        # points. Below this is a two yard carry.
WIN_DELTA = 0.05         # 5 points of win probability.
PLAYOFF_DELTA = 0.03     # 3 points of playoff odds. Moves far more slowly.
BENCH_DELTA = 5.0        # points. A benched player actually doing something.
RANK_DELTA = 2           # album places.

#: A starter is "hot" above this multiple of what he was due by now, and "cold"
#: below the other one. The window matters: at 10% elapsed almost everyone is at
#: zero and the ratio is meaningless, so nothing fires until a quarter has been
#: played.
HOT_RATIO = 1.6
COLD_RATIO = 0.45
MIN_ELAPSED = 0.25

#: The most lines one poll may contribute. A Sunday afternoon can produce forty
#: changes in thirty seconds and the ticker can show about eight in that time,
#: so the rest are not a backlog, they are noise that pushes the good ones off.
#: Kept by magnitude.
PER_POLL = 8

#: The rolling buffer. Forty is about ten minutes of ticker at a four second
#: rotation, which is as far back as "latest" can honestly mean.
BUFFER = 40


@dataclass
class Change:
    """One line of ticker."""

    id: str
    kind: str
    team_id: int
    team: str
    hue: int
    #: The sentence. Written here rather than in the template because the phrasing
    #: depends on the direction and on which number moved.
    text: str
    #: The figure the wheel shows large, already formatted and signed.
    value: str
    #: Green, red, or neither. Never inferred from the sign: see the module note.
    good: bool | None
    magnitude: float = 0.0
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "team_id": self.team_id,
            "team": self.team, "hue": self.hue, "text": self.text,
            "value": self.value, "good": self.good,
            "magnitude": round(self.magnitude, 3),
            "ts": self.ts.isoformat(timespec="seconds"),
        }


def change_id(*parts: Any) -> str:
    """A stable hash, so a re-poll that sees the same move does not repeat it.

    Same rule as `moment_id`: seeded by the facts and never by the clock or the
    process, so a restart mid-afternoon does not replay the whole ticker.
    """
    return hashlib.blake2b("|".join(str(p) for p in parts).encode(), digest_size=8).hexdigest()


@dataclass
class _Seen:
    """The last values for one team. Everything the differ compares against."""

    score: float = 0.0
    win: float | None = None
    playoff: float | None = None
    bench: float | None = None
    rank: int | None = None
    seeded: bool | None = None
    finished: bool = False
    #: player id -> whether he has already been called hot or cold this week, so
    #: a player twenty points clear of his projection is not announced on every
    #: poll for the rest of the afternoon.
    called: dict[int, str] = field(default_factory=dict)


class Ticker:
    """The differ, plus a rolling buffer of what it has produced.

    Per-process and per-week, exactly like the Moment buffer, and cleared by the
    same rollover: a Change about last Sunday under this Sunday's scores would
    be worse than no ticker at all.
    """

    def __init__(self) -> None:
        self.week: int | None = None
        self.changes: list[Change] = []
        self._seen: dict[int, _Seen] = {}
        self._fired: set[str] = set()

    def reset(self) -> None:
        self.changes = []
        self._seen = {}
        self._fired = set()

    def observe(self, snapshot, moments: Iterable = (), *, probabilities=None,
                odds=None, ranks=None, lines=None) -> list[Change]:
        """Diff this snapshot against the last one and return what is new.

        Everything expensive is passed in rather than computed here. The poller
        already has the win probabilities and the playoff odds for its own
        reasons, and recomputing 2,500 simulated seasons inside a differ would
        be the single most expensive thing in the application.
        """
        if snapshot is None or not snapshot.teams:
            return []
        if snapshot.scoring_period != self.week:
            self.week = snapshot.scoring_period
            self.reset()

        found: list[Change] = []
        first_look = not self._seen
        for matchup in snapshot.live_matchups or snapshot.matchups:
            for side in (matchup.home, matchup.away):
                found += self._team(snapshot, side, probabilities, odds, ranks)
        found += self._from_moments(snapshot, moments, lines or {})

        # The first poll of a process sees every number change from nothing, and
        # announcing all of that would open the afternoon with forty lines of
        # things that happened before anybody was watching. Learn the state
        # silently and start reporting from the second poll.
        if first_look:
            return []

        fresh = [c for c in found if c.id not in self._fired]
        self._fired.update(c.id for c in fresh)
        fresh.sort(key=lambda c: -c.magnitude)
        fresh = fresh[:PER_POLL]
        self.changes.extend(fresh)
        del self.changes[:-BUFFER]
        return fresh

    # -- the individual differs ------------------------------------------

    def _team(self, snapshot, side, probabilities, odds, ranks) -> list[Change]:
        team = snapshot.team(side.team_id)
        if team is None:
            return []  # cold: a fixture naming a team mTeam never sent
        name, hue = team.name, team.hue
        was = self._seen.setdefault(side.team_id, _Seen())
        out: list[Change] = []
        week = snapshot.scoring_period

        def add(kind, text, value, good, magnitude, *key):
            out.append(Change(
                id=change_id(kind, week, side.team_id, *key), kind=kind,
                team_id=side.team_id, team=name, hue=hue,
                text=text, value=value, good=good, magnitude=magnitude,
            ))

        # Score. The most common line and the one the room checks against the
        # television, so it says the new total as well as the jump.
        if side.total - was.score >= SCORE_DELTA:
            delta = side.total - was.score
            add("SCORE", f"{name} up to {side.total:.1f}", f"+{delta:.1f}", True,
                min(1.0, delta / 25), round(side.total, 1))

        # Win probability. Signed both ways: a team quietly losing a matchup it
        # was winning is the thing nobody notices until it is over.
        now = (probabilities or {}).get(side.team_id)
        if now is not None:
            if was.win is not None and abs(now - was.win) >= WIN_DELTA:
                up = now > was.win
                add("WIN",
                    f"{name} {'up' if up else 'down'} to {now * 100:.0f}% to win",
                    f"{'+' if up else ''}{(now - was.win) * 100:.0f}%", up,
                    min(1.0, abs(now - was.win) * 6), round(now, 2))
            was.win = now

        # Playoff odds, and separately whether the team is inside the cut. The
        # percentage and the position are different news: drifting from 61% to
        # 58% is a number moving, dropping out of the places is an event.
        chance = (odds or {}).get(side.team_id)
        if chance is not None:
            if was.playoff is not None and abs(chance.odds - was.playoff) >= PLAYOFF_DELTA:
                up = chance.odds > was.playoff
                add("PLAYOFF",
                    f"{name} {'up' if up else 'down'} to {chance.odds * 100:.0f}% for the playoffs",
                    f"{'+' if up else ''}{(chance.odds - was.playoff) * 100:.0f}%", up,
                    min(1.0, abs(chance.odds - was.playoff) * 10), round(chance.odds, 2))
            was.playoff = chance.odds

            # Clinching and elimination rather than a projected cut line. Both
            # are properties the simulator already reports and both are final,
            # which a team drifting across 6th place on a Sunday afternoon is
            # not: that would fire twice an hour and mean nothing either time.
            settled = True if chance.clinched else False if chance.eliminated else None
            if settled is not None and was.seeded != settled:
                add("SEED",
                    f"{name} {'are in the playoffs' if settled else 'are out of it'}",
                    "IN" if settled else "OUT", settled, 1.0, settled)
            was.seeded = settled

        # Album rank. Reported as a movement rather than a position, because the
        # position is on the card two inches above and the movement is not.
        rank = (ranks or {}).get(side.team_id)
        if rank is not None:
            if was.rank is not None and abs(rank - was.rank) >= RANK_DELTA:
                up = rank < was.rank          # #6 -> #2 is up, and is a smaller number
                add("FORM",
                    f"{name} {'up' if up else 'down'} to #{rank} on form",
                    f"{'+' if up else '-'}{abs(rank - was.rank)}", up,
                    min(1.0, abs(rank - was.rank) / 6), rank)
            was.rank = rank

        # Players, hot and cold, once each per week. `game.elapsed` is why this
        # lives on the model: the same prorating the card ratings use.
        out += self._players(snapshot, side, name, hue, was, week)

        # Everyone has played. Worth a line because it is the only one of these
        # that is final, and a settled team is done being interesting.
        if not was.finished and side.in_play == 0 and side.total > 0:
            add("FINAL", f"{name} are done on {side.total:.1f}", "FINAL", None, 0.5, "done")
            was.finished = True

        was.score = side.total
        was.bench = _bench(side, was, out, name, hue, week, self)
        return out

    def _players(self, snapshot, side, name, hue, was, week) -> list[Change]:
        out: list[Change] = []
        for player in side.starters:
            game = snapshot.games.get(player.pro_team_id)
            if game is None or game.elapsed < MIN_ELAPSED:
                continue
            due = player.projected * game.elapsed
            if due < 1.0:
                continue
            ratio = player.points / due
            verdict = "hot" if ratio >= HOT_RATIO else "cold" if ratio <= COLD_RATIO else None
            if verdict is None or was.called.get(player.id) == verdict:
                continue
            was.called[player.id] = verdict
            hot = verdict == "hot"
            out.append(Change(
                id=change_id("PLAYER", week, player.id, verdict),
                kind="HOT" if hot else "COLD", team_id=side.team_id,
                team=name, hue=hue,
                text=(f"{player.name} is carrying {name}" if hot
                      else f"{player.name} is letting {name} down"),
                value=f"{player.points:.1f} v {due:.1f}",
                good=hot, magnitude=min(1.0, abs(ratio - 1) / 2),
            ))
        return out

    def _from_moments(self, snapshot, moments, lines) -> list[Change]:
        """The commentary, folded in, so the room reads one ticker and not two."""
        out = []
        for moment in moments:
            if not moment.team_ids:
                continue  # cold: every kind the engine fires names at least one team
            team = snapshot.team(moment.team_ids[0])
            line = lines.get(moment.id)
            text = line.text if line is not None else None
            if not text:
                continue  # cold: silence is a valid answer from the bank, and 410
                # lines is enough that a whole Sunday never produces one
            # The Moment already carries its own direction in `win_prob_delta`.
            # Zero is genuinely neutral here (a milestone, an injury), so this is
            # a three-way answer and not a boolean.
            good = None
            if moment.win_prob_delta > 0.01:
                good = True
            elif moment.win_prob_delta < -0.01:
                good = False
            out.append(Change(
                id=change_id("SAID", moment.id), kind="SAID",
                team_id=moment.team_ids[0],
                team=team.name if team else (moment.teams[0] if moment.teams else "?"),
                hue=team.hue if team else 0,
                text=text,
                value=f"{moment.delta_points:+.1f}" if moment.delta_points else "",
                good=good, magnitude=max(0.55, moment.magnitude),
            ))
        return out

    def recent(self, limit: int = BUFFER) -> list[Change]:
        """Newest first, which is the order a ticker reads in."""
        return list(reversed(self.changes[-limit:]))


def _bench(side, was, out, name, hue, week, ticker) -> float:
    """Bench regret, which is the one number here that is bad when it goes up.

    Computed from what the side already carries rather than by re-running the
    lineup solver: the poller has done that once already and this runs for
    twenty teams on every poll.
    """
    bench_points = sum(p.points for p in side.bench)
    # `is not None`, not a truth test. Every bench starts the afternoon on zero
    # points, so `if was.bench` was false on the one poll that establishes the
    # baseline AND on every poll after it until somebody benched had scored --
    # which is exactly the moment this is supposed to fire. The first poll is
    # discarded wholesale by `first_look`, so there is nothing else to guard.
    if was.bench is not None and bench_points - was.bench >= BENCH_DELTA:
        delta = bench_points - was.bench
        out.append(Change(
            id=change_id("BENCH", week, side.team_id, round(bench_points, 1)),
            kind="BENCH", team_id=side.team_id, team=name, hue=hue,
            text=f"{name} have {bench_points:.1f} sitting on the bench",
            value=f"+{delta:.1f}", good=False,
            magnitude=min(1.0, delta / 20),
        ))
    return bench_points
