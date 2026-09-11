"""The arithmetic a fantasy app should do and ESPN's does not.

Four figures, in increasing order of how much they wind people up:

* **Optimal lineup** -- the best score reachable from a roster under the league's
  actual slot eligibility rules.
* **Bench regret** -- optimal minus what was actually started. The funniest
  number in the app.
* **All-play** -- a team's record against every other team every week, which
  removes the schedule from the standings.
* **Luck index** -- actual wins minus all-play expected wins. Who has been
  carried by the fixture list.

The optimal lineup is the only one that is algorithmically interesting, and it is
the one everything else depends on, so it gets the careful treatment below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from espn.models import LINEUP_SLOTS, LeagueSnapshot, Matchup, Player, Side

#: What a player is allowed to fill when ESPN has not told us. Keyed by the
#: position string in `models.POSITIONS`. Used only as a fallback: the real
#: eligibility comes from `eligibleSlots` on the payload, and inventing it is
#: how a lineup silently becomes optimal-but-illegal.
FALLBACK_ELIGIBILITY: dict[str, tuple[int, ...]] = {
    "QB": (0, 7, 20),
    "RB": (2, 3, 23, 7, 20),
    "WR": (4, 3, 5, 23, 7, 20),
    "TE": (6, 5, 23, 7, 20),
    "K": (17, 20),
    "D/ST": (16, 20),
}


@dataclass
class LineupSlot:
    """One seat in a lineup, and who is in it."""

    slot_id: int
    player: Player | None = None

    @property
    def slot(self) -> str:
        return LINEUP_SLOTS.get(self.slot_id, f"?{self.slot_id}")

    @property
    def points(self) -> float:
        return self.player.points if self.player else 0.0


@dataclass
class OptimalLineup:
    """The best legal lineup, and what it cost not to have played it."""

    seats: list[LineupSlot] = field(default_factory=list)
    total: float = 0.0
    actual: float = 0.0
    #: (started player, the bench player who should have been in that seat)
    swaps: list[tuple[Player, Player]] = field(default_factory=list)
    exact: bool = True

    @property
    def regret(self) -> float:
        """Points left on the bench. Never negative: an optimal lineup is by
        definition at least as good as the one that was played, and a negative
        value here would mean the eligibility data is wrong rather than that a
        manager beat the optimum."""
        return round(max(0.0, self.total - self.actual), 2)

    @property
    def worst_swap(self) -> tuple[Player, Player] | None:
        """The single change that would have gained the most. This is the one
        the commentary names, because "you left 41 points on the bench" is a
        statistic and "you benched Wilder Braithwaite for Ash Greenhalgh" is a
        story."""
        if not self.swaps:
            return None
        return max(self.swaps, key=lambda pair: pair[1].points - pair[0].points)


def eligible_slots(player: Player, declared: Sequence[int] | None = None) -> frozenset[int]:
    """Which seats this player may occupy.

    Prefers what ESPN said. The fallback exists because `eligibleSlots` travels
    with some views and not others, and a player with no declared eligibility
    would otherwise be excluded from the optimal lineup entirely, which quietly
    *understates* bench regret -- the one direction that makes the app less
    funny rather than wrong in a way anyone would notice.
    """
    if declared:
        return frozenset(int(s) for s in declared)
    return frozenset(FALLBACK_ELIGIBILITY.get(player.position, (20,)))


def optimal_lineup(
    players: Sequence[Player],
    starting_slots: Sequence[int],
    eligibility: dict[int, Sequence[int]] | None = None,
) -> OptimalLineup:
    """The highest-scoring legal assignment of players to starting slots.

    Greedy by points does not work: taking the highest scorer first can consume
    the only FLEX seat and strand two running backs who between them were worth
    more. This is a maximum-weight bipartite matching.

    It is solved here by processing players in descending order of points and
    trying to add each to the matching with an augmenting path, keeping it if the
    path is found. That is exact, not a heuristic: the sets of players that can
    be simultaneously seated form a transversal matroid, and the greedy algorithm
    is optimal on a matroid. It is also small and fast -- ten teams of sixteen
    players, nine seats -- where a general Hungarian implementation would be a
    dependency and a lot more code for the same answer.

    Negative-scoring players are never seated: an empty seat scores zero, which
    beats a player who lost you two points on a fumble.
    """
    eligibility = eligibility or {}
    seats = [LineupSlot(slot_id=s) for s in starting_slots]
    if not seats:
        return OptimalLineup(seats=[], total=0.0, actual=_started_total(players))

    ranked = sorted(
        (p for p in players if p.points > 0),
        key=lambda p: (-p.points, p.id),
    )

    # seat index -> player index into `ranked`
    assignment: dict[int, int] = {}
    allowed: list[frozenset[int]] = [
        eligible_slots(p, eligibility.get(p.id)) for p in ranked
    ]

    def augment(player_index: int, visited: set[int]) -> bool:
        """Find a seat for this player, displacing others along the way."""
        for seat_index, seat in enumerate(seats):
            if seat.slot_id not in allowed[player_index] or seat_index in visited:
                continue
            visited.add(seat_index)
            occupant = assignment.get(seat_index)
            if occupant is None or augment(occupant, visited):
                assignment[seat_index] = player_index
                return True
        return False

    for index in range(len(ranked)):
        augment(index, set())

    for seat_index, player_index in assignment.items():
        seats[seat_index].player = ranked[player_index]

    total = round(sum(seat.points for seat in seats), 2)
    actual = _started_total(players)

    return OptimalLineup(
        seats=seats, total=total, actual=actual, swaps=_swaps(players, seats)
    )


def _swaps(players: Sequence[Player], seats: Sequence[LineupSlot]) -> list[tuple[Player, Player]]:
    """The seat-by-seat difference between what was played and what was optimal.

    Pairing the worst starters against the best bench players by rank produces
    swaps that are arithmetically suggestive and *illegal*: the first version of
    this said a manager should have started a quarterback in place of a running
    back, because it sorted both sets and zipped them. A swap that cannot
    actually be made is worse than no swap, because the commentary states it as
    a fact and somebody in the bar will check.

    Pairing within each slot id is correct by construction: the optimal lineup
    fills exactly the same multiset of seats the real one did, so the players who
    differ inside one slot are, by definition, interchangeable in it.
    """
    from collections import defaultdict

    optimal_by_slot: dict[int, list[Player]] = defaultdict(list)
    for seat in seats:
        if seat.player:
            optimal_by_slot[seat.slot_id].append(seat.player)

    actual_by_slot: dict[int, list[Player]] = defaultdict(list)
    for player in players:
        if player.is_starter:
            actual_by_slot[player.slot_id].append(player)

    pairs: list[tuple[Player, Player]] = []
    for slot_id, optimal_players in optimal_by_slot.items():
        actual_players = actual_by_slot.get(slot_id, [])
        kept = {p.id for p in optimal_players} & {p.id for p in actual_players}
        dropped = sorted((p for p in actual_players if p.id not in kept), key=lambda p: p.points)
        added = sorted((p for p in optimal_players if p.id not in kept), key=lambda p: -p.points)
        pairs.extend(zip(dropped, added))
    return pairs


def _started_total(players: Iterable[Player]) -> float:
    return round(sum(p.points for p in players if p.is_starter), 2)


def bench_regret(side: Side, starting_slots: Sequence[int]) -> float:
    """Points left on the bench, exactly."""
    return optimal_lineup(side.players, starting_slots).regret


@dataclass
class AllPlayRecord:
    """How a team would have done against everybody, every week."""

    team_id: int
    wins: int = 0
    losses: int = 0
    ties: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def win_pct(self) -> float:
        return (self.wins + 0.5 * self.ties) / self.games if self.games else 0.0

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}" + (f"-{self.ties}" if self.ties else "")


def all_play(scores: dict[int, float]) -> dict[int, AllPlayRecord]:
    """One week's all-play records, from `{team_id: score}`.

    Ten teams means nine notional games each, which is what makes this worth
    computing: a team can go 8-1 against the league and still lose, and that is
    the single most reliable source of grievance in a fantasy season.
    """
    records = {team_id: AllPlayRecord(team_id=team_id) for team_id in scores}
    for team_id, score in scores.items():
        for other_id, other_score in scores.items():
            if other_id == team_id:
                continue
            if score > other_score:
                records[team_id].wins += 1
            elif score < other_score:
                records[team_id].losses += 1
            else:
                records[team_id].ties += 1
    return records


def luck_index(actual_wins: float, all_play_pct: float, weeks: int) -> float:
    """Actual wins minus the wins an all-play record deserved.

    Positive means the schedule has been kind. It is expressed in wins rather
    than as a percentage because "you are 1.8 wins luckier than you deserve" is a
    sentence somebody will argue with, which is the entire point of the figure.
    """
    return round(actual_wins - all_play_pct * weeks, 2)


@dataclass
class SeasonRecord:
    """A team's season so far, by both the real standings and the all-play one."""

    team_id: int
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    all_play: AllPlayRecord = field(default_factory=lambda: AllPlayRecord(team_id=0))
    weekly: list[float] = field(default_factory=list)

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}" + (f"-{self.ties}" if self.ties else "")

    @property
    def weeks(self) -> int:
        return self.wins + self.losses + self.ties

    @property
    def luck(self) -> float:
        """Actual wins minus what an all-play record deserved, in wins.

        The single most reliable source of grievance in a fantasy season, and it
        cannot be computed from one week: it needs the whole grid."""
        return luck_index(self.wins, self.all_play.win_pct, self.weeks)

    @property
    def mean(self) -> float:
        return sum(self.weekly) / len(self.weekly) if self.weekly else 0.0

    @property
    def sigma(self) -> float:
        """Spread of this team's weekly scores. Feeds the playoff simulator.

        Floored, because a team with two settled weeks has a meaningless sample
        and a near-zero sigma would make the simulator absurdly confident about
        the rest of the season."""
        if len(self.weekly) < 2:
            return 22.0
        mean = self.mean
        variance = sum((score - mean) ** 2 for score in self.weekly) / (len(self.weekly) - 1)
        return max(12.0, variance ** 0.5)


def season_records(snapshot: LeagueSnapshot) -> dict[int, SeasonRecord]:
    """Standings and all-play across every *settled* week of the season.

    A week in progress has real scores and no result. Counting it would make
    every standing in the league wrong for four hours every Sunday, so
    `settled_weeks` excludes it and this reads only from there.
    """
    records: dict[int, SeasonRecord] = {
        team.id: SeasonRecord(team_id=team.id, all_play=AllPlayRecord(team_id=team.id))
        for team in snapshot.teams
    }
    if not records:
        return records

    for week, games in sorted(snapshot.settled_weeks.items()):
        scores: dict[int, float] = {}
        for matchup in games:
            for side in (matchup.home, matchup.away):
                scores[side.team_id] = round(side.total, 2)

        for matchup in games:
            home, away = matchup.home, matchup.away
            for side, other in ((home, away), (away, home)):
                record = records.get(side.team_id)
                if record is None:
                    continue
                record.points_for = round(record.points_for + side.total, 2)
                record.weekly.append(round(side.total, 2))
                if side.total > other.total:
                    record.wins += 1
                elif side.total < other.total:
                    record.losses += 1
                else:
                    record.ties += 1

        for team_id, weekly in all_play(scores).items():
            record = records.get(team_id)
            if record is None:
                continue
            record.all_play.wins += weekly.wins
            record.all_play.losses += weekly.losses
            record.all_play.ties += weekly.ties

    return records


def standings(snapshot: LeagueSnapshot) -> list[SeasonRecord]:
    """Ordered the way a league table is: wins, then points for as the tiebreak.

    Which is the tiebreak ESPN uses by default and the one every argument in the
    bar assumes."""
    return sorted(
        season_records(snapshot).values(),
        key=lambda r: (-(r.wins + 0.5 * r.ties), -r.points_for),
    )


def week_scores(snapshot: LeagueSnapshot) -> dict[int, float]:
    """`{team_id: this week's score}` for every team with a matchup."""
    scores: dict[int, float] = {}
    for matchup in snapshot.live_matchups or snapshot.matchups:
        for side in (matchup.home, matchup.away):
            scores[side.team_id] = round(side.total, 2)
    return scores


def side_eligibility(side: Side, raw: dict[int, Sequence[int]] | None = None) -> dict[int, Sequence[int]]:
    """Eligibility map for one side, keyed by player id."""
    return raw or {}
