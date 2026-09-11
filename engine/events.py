"""Polls produce state. This turns state into Moments.

A poll says "Dax Ashgrove now has 18.4 points". Nobody cheers at that. What the
app needs is "Dax Ashgrove just scored from 40 yards out, and Priya has gone from
losing to winning", which is a *difference* between two polls plus enough context
to know whether it deserves a horn.

Three properties matter and each is a design constraint rather than a nicety:

* **Idempotent.** The same underlying play must never fire twice, including
  across a process restart. Moment ids are hashes of the play's own facts (the
  player and his cumulative total after it), not of the poll that observed it, so
  re-observing the same state produces the same id and is dropped.
* **Magnitude-scaled.** A two-point reception and a sixty-yard touchdown must not
  get the same horn. Every Moment carries a 0-1 magnitude that the audio and
  animation layers scale off.
* **Bench-aware.** `BENCH_DISASTER` is the funniest event in the app and it is
  invisible in the score, so it gets its own detection path rather than falling
  out of a points delta.

Moments feed three consumers at once: the SSE stream, the commentary engine and
the audio bus. Nothing here knows about any of them.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from engine.scoring import optimal_lineup
from engine.simulate import team_probabilities
from espn.models import LeagueSnapshot, Player, Side

log = logging.getLogger(__name__)

TOUCHDOWN = "TOUCHDOWN"
BIG_PLAY = "BIG_PLAY"
LEAD_CHANGE = "LEAD_CHANGE"
MILESTONE = "MILESTONE"
BENCH_DISASTER = "BENCH_DISASTER"
INJURY = "INJURY"
DOOM = "DOOM"
CLINCH = "CLINCH"
GOOSE_EGG = "GOOSE_EGG"

#: A touchdown is six points plus whatever the yardage was worth, so anything at
#: or above this in one poll is one. Below it and above `BIG_PLAY_POINTS` is a
#: chunk play. This is a heuristic on purpose: ESPN's fantasy feed carries totals,
#: not plays, and asking it what happened is not an option.
TOUCHDOWN_POINTS = 5.9
BIG_PLAY_POINTS = 2.4

#: Player and team totals worth announcing as they are crossed.
PLAYER_MILESTONES = (20.0, 30.0, 40.0)
TEAM_MILESTONES = (100.0, 125.0, 150.0)

#: How far a benched player must be ahead of the starter he should have replaced
#: before it is worth saying out loud. Below this it is noise; a manager whose
#: bench beat his lineup by four points does not need to hear about it.
BENCH_DISASTER_MARGIN = 15.0

#: Bench disasters re-fire as the gap widens, because the joke genuinely gets
#: worse, but only once per band of this many points. At five, one manager fired
#: five times in an afternoon -- twice within four minutes, because the *starter*
#: in the worst swap changed while the benched player stayed the same, which
#: reads as a repeat rather than as an escalation.
BENCH_DISASTER_BAND = 10.0

#: A lead change below this leader's total is not a lead change, it is the first
#: two players of the afternoon trading catches. Six of them fired in the opening
#: eleven minutes of the demo Sunday, when nobody had scored twenty points.
LEAD_CHANGE_MIN_TOTAL = 25.0

#: Win probability below which a manager is told, in as many words, that it is
#: over -- but only while the opponent still has players on the field, because
#: being told you are doomed after the games have finished is just the score.
DOOM_THRESHOLD = 0.05
CLINCH_THRESHOLD = 0.98

INJURED_STATUSES = frozenset({"OUT", "INJURY_RESERVE", "DOUBTFUL", "SUSPENSION"})


@dataclass
class Moment:
    """One thing that happened, ready for a horn, a line and a card."""

    id: str
    kind: str
    magnitude: float
    managers: list[str] = field(default_factory=list)
    team_ids: list[int] = field(default_factory=list)
    player: str | None = None
    delta_points: float = 0.0
    win_prob_delta: float = 0.0
    context: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "magnitude": round(self.magnitude, 3),
            "managers": self.managers,
            "team_ids": self.team_ids,
            "player": self.player,
            "delta_points": round(self.delta_points, 2),
            "win_prob_delta": round(self.win_prob_delta, 4),
            "context": self.context,
            "ts": self.ts.isoformat(timespec="seconds"),
        }


def moment_id(*parts: Any) -> str:
    """A stable hash of a Moment's own facts.

    Deliberately not seeded by time, poll number or process. Two different
    processes observing the same play, a week apart, must agree on the id, or the
    dedupe does nothing after a restart -- which is the exact failure the spec
    calls out, and which in this app sounds like every touchdown of the afternoon
    firing again at once.
    """
    payload = "|".join(str(p) for p in parts)
    return hashlib.blake2b(payload.encode(), digest_size=10).hexdigest()


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass
class _PlayerState:
    points: float = 0.0
    injury: str = ""
    game_over: bool | None = None


class EventEngine:
    """Diffs consecutive snapshots into Moments.

    Stateful by necessity: an event is a difference, so something has to remember
    the previous poll. Everything it remembers is small and reconstructible, which
    is what lets `seen_path` be an optional convenience rather than a database.
    """

    def __init__(
        self,
        bench_margin: float = BENCH_DISASTER_MARGIN,
        seen_path: Path | None = None,
        simulate_draws: int = 1500,
    ) -> None:
        self.bench_margin = bench_margin
        self.simulate_draws = simulate_draws
        self.seen: set[str] = set()
        self.seen_path = seen_path
        self._players: dict[int, _PlayerState] = {}
        self._leader: dict[int, int | None] = {}
        self._team_totals: dict[int, float] = {}
        self._win_prob: dict[int, float] = {}
        self._started = False

        if seen_path and seen_path.is_file():
            try:
                self.seen = set(json.loads(seen_path.read_text("utf-8")))
                log.info("loaded %d previously fired moment ids", len(self.seen))
            except (OSError, ValueError) as exc:
                log.warning("could not read %s, starting with an empty dedupe set: %s", seen_path, exc)

    # -- public ------------------------------------------------------------

    def ingest(self, snapshot: LeagueSnapshot) -> list[Moment]:
        """Diff this snapshot against the last one and return what is new.

        The first snapshot after a cold start emits nothing. That is deliberate:
        joining a Sunday already in progress, every player already has points and
        every matchup already has a leader, so diffing against an empty baseline
        would fire the entire afternoon at once. Losing the moments that happened
        while the process was down is the correct trade against replaying them
        all through a bar's PA at 4pm.
        """
        probabilities = team_probabilities(snapshot, draws=self.simulate_draws)
        moments: list[Moment] = []

        if self._started:
            moments.extend(self._player_moments(snapshot))
            moments.extend(self._matchup_moments(snapshot, probabilities))
            moments.extend(self._bench_moments(snapshot))
            moments.extend(self._doom_moments(snapshot, probabilities))

        # Every Moment that belongs to a team carries what it did to that team's
        # chances. Set centrally rather than per detector, because it was set in
        # three of the nine and the other six left it at zero -- which meant the
        # Swing tab's "biggest swing of the day" could never be a touchdown, and
        # the biggest swing of a Sunday is almost always a touchdown. Measured:
        # 0 of 73 touchdowns carried one, and 7 of 236 Moments in total.
        self._attach_win_prob_deltas(moments, probabilities)

        self._remember(snapshot, probabilities)
        self._started = True

        fresh = [m for m in moments if m.id not in self.seen]
        self.seen.update(m.id for m in fresh)
        fresh.sort(key=lambda m: -m.magnitude)
        return fresh

    #: Kinds that represent something actually happening on a field. A bench
    #: disaster and a goose egg are observations about a state, not causes of a
    #: change in it, so attributing a poll's swing to them would credit the wrong
    #: event -- the scoring that moved the number is a separate Moment in the
    #: same poll.
    CAUSAL = frozenset({TOUCHDOWN, BIG_PLAY})

    def _attach_win_prob_deltas(self, moments: list[Moment], probabilities: dict[int, float]) -> None:
        """Give each team's swing this poll to the one play that best explains it.

        One poll produces one net change per team, and several Moments can land
        inside it. Giving all of them the same number puts three entries reading
        +33.4% at the top of the Swing tab and says nothing about which one did
        it; giving it to the loudest causal play in that poll is both truthful
        and the sentence a person would write.
        """
        by_team: dict[int, list[Moment]] = {}
        for moment in moments:
            if moment.win_prob_delta or moment.kind not in self.CAUSAL or not moment.team_ids:
                continue
            by_team.setdefault(moment.team_ids[0], []).append(moment)

        for team_id, candidates in by_team.items():
            now = probabilities.get(team_id)
            before = self._win_prob.get(team_id)
            if now is None or before is None:
                continue
            swing = round(now - before, 4)
            # Only ever in the play's own favour. A touchdown that coincided with
            # the opponent scoring more leaves the team's probability *down* for
            # the poll, and crediting the touchdown with that produced a Swing
            # tab reading "touchdown, -43%" -- arithmetically true and obvious
            # nonsense. The cause of a fall is something on the other side, and
            # that side's own play picks it up as a positive in the same poll.
            if swing <= 0:
                continue
            loudest = max(candidates, key=lambda m: (m.magnitude, m.delta_points))
            loudest.win_prob_delta = swing

    def persist(self) -> None:
        """Write the dedupe set so a restart does not replay the afternoon."""
        if not self.seen_path:
            return
        try:
            self.seen_path.parent.mkdir(parents=True, exist_ok=True)
            self.seen_path.write_text(json.dumps(sorted(self.seen)), encoding="utf-8")
        except OSError as exc:
            log.warning("could not persist moment ids: %s", exc)

    # -- detection ---------------------------------------------------------

    def _player_moments(self, snapshot: LeagueSnapshot) -> list[Moment]:
        out: list[Moment] = []
        week = snapshot.scoring_period

        for matchup in snapshot.matchups:
            for side in (matchup.home, matchup.away):
                team = snapshot.team(side.team_id)
                manager = team.manager if team else f"team {side.team_id}"

                for player in side.players:
                    previous = self._players.get(player.id)
                    if previous is None:
                        continue

                    delta = round(player.points - previous.points, 2)
                    context = {
                        "week": week,
                        "slot": player.slot,
                        "position": player.position,
                        "pro_team": player.pro_team,
                        # `pro_opponent` rather than `opponent`: a commentary
                        # line's `{opponent}` means the rival manager, and having
                        # the NFL opponent silently take that name produced lines
                        # addressed to a football team.
                        "pro_opponent": player.opponent,
                        "total": round(player.points, 2),
                        "starter": player.is_starter,
                    }
                    game = snapshot.games.get(player.pro_team_id)
                    if game:
                        context.update(
                            quarter=game.period, clock=game.clock,
                            red_zone=game.red_zone, down_distance=game.down_distance,
                        )

                    if delta >= TOUCHDOWN_POINTS:
                        out.append(Moment(
                            # Keyed on the cumulative total *after* the play, so
                            # the same play always hashes the same way no matter
                            # which poll or which process saw it.
                            id=moment_id(TOUCHDOWN, week, player.id, round(player.points, 2)),
                            kind=TOUCHDOWN,
                            magnitude=_clamp(0.55 + (delta - 6.0) / 12.0),
                            managers=[manager], team_ids=[side.team_id],
                            player=player.name, delta_points=delta, context=context,
                        ))
                    elif delta >= BIG_PLAY_POINTS:
                        out.append(Moment(
                            id=moment_id(BIG_PLAY, week, player.id, round(player.points, 2)),
                            kind=BIG_PLAY,
                            magnitude=_clamp(0.2 + delta / 18.0),
                            managers=[manager], team_ids=[side.team_id],
                            player=player.name, delta_points=delta, context=context,
                        ))

                    for threshold in PLAYER_MILESTONES:
                        if previous.points < threshold <= player.points:
                            out.append(Moment(
                                id=moment_id(MILESTONE, week, player.id, threshold),
                                kind=MILESTONE,
                                magnitude=_clamp(0.25 + threshold / 100.0),
                                managers=[manager], team_ids=[side.team_id],
                                player=player.name, delta_points=delta,
                                context={**context, "threshold": threshold},
                            ))

                    if (
                        player.injury in INJURED_STATUSES
                        and previous.injury not in INJURED_STATUSES
                        and player.is_starter
                    ):
                        out.append(Moment(
                            id=moment_id(INJURY, week, player.id, player.injury),
                            kind=INJURY, magnitude=0.6,
                            managers=[manager], team_ids=[side.team_id],
                            player=player.name,
                            context={**context, "status": player.injury},
                        ))

                    # A goose egg is only a goose egg once the game is over.
                    # Firing it on a scoreless first quarter would be both wrong
                    # and, given what it sounds like, unkind.
                    if (
                        player.is_starter
                        and player.game_over
                        and previous.game_over is False
                        and player.points == 0.0
                    ):
                        out.append(Moment(
                            id=moment_id(GOOSE_EGG, week, player.id),
                            kind=GOOSE_EGG, magnitude=0.7,
                            managers=[manager], team_ids=[side.team_id],
                            player=player.name, delta_points=0.0,
                            context={**context, "projected": round(player.projected, 2)},
                        ))
        return out

    def _matchup_moments(
        self, snapshot: LeagueSnapshot, probabilities: dict[int, float]
    ) -> list[Moment]:
        out: list[Moment] = []
        week = snapshot.scoring_period

        for matchup in snapshot.matchups:
            home, away = matchup.home, matchup.away
            leader = _leader_of(matchup)
            previous_leader = self._leader.get(matchup.id, "unset")

            if (
                previous_leader != "unset"
                and leader != previous_leader
                and leader is not None
                and max(home.total, away.total) >= LEAD_CHANGE_MIN_TOTAL
            ):
                team = snapshot.team(leader)
                loser_id = away.team_id if leader == home.team_id else home.team_id
                loser = snapshot.team(loser_id)
                swing = abs(probabilities.get(leader, 0.5) - self._win_prob.get(leader, 0.5))
                out.append(Moment(
                    # The margin at the moment of the change identifies it: the
                    # lead can change back and forth, and each crossing is its own
                    # event rather than a repeat of the first.
                    id=moment_id(LEAD_CHANGE, week, matchup.id, leader, round(matchup.margin, 2)),
                    kind=LEAD_CHANGE,
                    magnitude=_clamp(0.5 + swing),
                    managers=[team.manager if team else "?", loser.manager if loser else "?"],
                    team_ids=[leader, loser_id],
                    win_prob_delta=round(probabilities.get(leader, 0.5) - self._win_prob.get(leader, 0.5), 4),
                    context={
                        "week": week, "matchup": matchup.id,
                        "margin": abs(round(matchup.margin, 2)),
                        "home": round(home.total, 2), "away": round(away.total, 2),
                    },
                ))

            for side in (home, away):
                before = self._team_totals.get(side.team_id)
                if before is None:
                    continue
                team = snapshot.team(side.team_id)
                for threshold in TEAM_MILESTONES:
                    if before < threshold <= side.total:
                        out.append(Moment(
                            id=moment_id(MILESTONE, week, side.team_id, threshold),
                            kind=MILESTONE,
                            magnitude=_clamp(0.35 + threshold / 400.0),
                            managers=[team.manager if team else "?"],
                            team_ids=[side.team_id],
                            context={"week": week, "threshold": threshold,
                                     "total": round(side.total, 2), "team": True},
                        ))
        return out

    def _bench_moments(self, snapshot: LeagueSnapshot) -> list[Moment]:
        """The funniest event in the app, and the only one the score never shows."""
        out: list[Moment] = []
        week = snapshot.scoring_period
        slots = snapshot.settings.starting_slots
        if not slots:
            return out

        for matchup in snapshot.matchups:
            for side in (matchup.home, matchup.away):
                lineup = optimal_lineup(side.players, slots)
                swap = lineup.worst_swap
                if swap is None:
                    continue
                started, benched = swap
                margin = round(benched.points - started.points, 2)
                if margin < self.bench_margin:
                    continue

                team = snapshot.team(side.team_id)
                out.append(Moment(
                    # Keyed on the team, the benched player and the band the gap
                    # falls in -- deliberately *not* on the starter. The starter in
                    # the worst swap changes as the afternoon goes on, and keying
                    # on him fired the same disaster twice in four minutes with a
                    # different name attached.
                    id=moment_id(BENCH_DISASTER, week, side.team_id, benched.id,
                                 int(benched.points // BENCH_DISASTER_BAND)),
                    kind=BENCH_DISASTER,
                    magnitude=_clamp(0.4 + margin / 60.0),
                    managers=[team.manager if team else "?"],
                    team_ids=[side.team_id],
                    player=benched.name,
                    delta_points=margin,
                    context={
                        "week": week, "slot": started.slot,
                        "started": started.name, "started_points": round(started.points, 2),
                        "benched": benched.name, "benched_points": round(benched.points, 2),
                        "regret": lineup.regret,
                    },
                ))
        return out

    def _doom_moments(
        self, snapshot: LeagueSnapshot, probabilities: dict[int, float]
    ) -> list[Moment]:
        out: list[Moment] = []
        week = snapshot.scoring_period

        for matchup in snapshot.matchups:
            for side, opponent in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
                probability = probabilities.get(side.team_id)
                if probability is None:
                    continue
                team = snapshot.team(side.team_id)
                manager = team.manager if team else f"team {side.team_id}"

                # Only while the opponent can still add to their score. Below the
                # threshold with everybody finished is not doom, it is the result.
                if probability < DOOM_THRESHOLD and opponent.in_play > 0:
                    out.append(Moment(
                        id=moment_id(DOOM, week, side.team_id),
                        kind=DOOM, magnitude=0.85,
                        managers=[manager], team_ids=[side.team_id],
                        win_prob_delta=round(probability - self._win_prob.get(side.team_id, probability), 4),
                        context={
                            "week": week, "win_prob": probability,
                            "deficit": round(opponent.total - side.total, 2),
                            "opponent_in_play": opponent.in_play,
                        },
                    ))

                # Symmetric with DOOM: it is a clinch while the opponent can
                # still theoretically score, and merely the result afterwards.
                # Requiring the clinching side to be finished too (the first
                # version) meant this never fired at all across a whole Sunday.
                if probability > CLINCH_THRESHOLD and opponent.in_play > 0:
                    out.append(Moment(
                        id=moment_id(CLINCH, week, side.team_id),
                        kind=CLINCH, magnitude=0.75,
                        managers=[manager], team_ids=[side.team_id],
                        win_prob_delta=round(probability - self._win_prob.get(side.team_id, probability), 4),
                        context={"week": week, "win_prob": probability,
                                 "lead": round(side.total - opponent.total, 2),
                                 "opponent_in_play": opponent.in_play},
                    ))
        return out

    # -- state -------------------------------------------------------------

    def _remember(self, snapshot: LeagueSnapshot, probabilities: dict[int, float]) -> None:
        for matchup in snapshot.matchups:
            self._leader[matchup.id] = _leader_of(matchup)
            for side in (matchup.home, matchup.away):
                self._team_totals[side.team_id] = side.total
                for player in side.players:
                    self._players[player.id] = _PlayerState(
                        points=player.points, injury=player.injury, game_over=player.game_over
                    )
        self._win_prob.update(probabilities)


def _leader_of(matchup) -> int | None:
    if matchup.home.total > matchup.away.total:
        return matchup.home.team_id
    if matchup.away.total > matchup.home.total:
        return matchup.away.team_id
    return None
