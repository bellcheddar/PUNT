"""Scoring maths.

The optimal lineup is the only algorithmically interesting piece and everything
else depends on it, so it is checked against brute force rather than against
hand-picked examples: a greedy bug shows up on the awkward roster nobody thought
to write a fixture for.
"""

from __future__ import annotations

import itertools
import random

import pytest

from engine.scoring import (
    AllPlayRecord,
    all_play,
    bench_regret,
    luck_index,
    optimal_lineup,
)
from espn.models import Player

QB, RB, WR, TE, FLEX, BENCH = 0, 2, 4, 6, 23, 20
POSITION_OF_SLOT = {QB: "QB", RB: "RB", WR: "WR", TE: "TE"}


def player(pid: int, points: float, position: str, slot: int) -> Player:
    return Player(id=pid, name=f"p{pid}", slot_id=slot, position=position, pro_team="X", points=points)


def brute_force_best(players, slots) -> float:
    """Every legal assignment, exhaustively. Correct by construction and far too
    slow for production, which is exactly what makes it a good oracle.

    The pool is padded with `None`s so that seats may be left empty. Without the
    padding the oracle only considers lineups in which *every* seat is filled,
    and returns zero for any roster that cannot fill them all -- which is both
    wrong and, since the solver handles partial lineups correctly, a test failure
    that blames the wrong side.
    """
    from engine.scoring import eligible_slots

    best = 0.0
    allowed = {p.id: eligible_slots(p) for p in players}
    pool = list(players) + [None] * len(slots)
    for chosen in itertools.permutations(pool, len(slots)):
        if any(p is not None and slots[i] not in allowed[p.id] for i, p in enumerate(chosen)):
            continue
        best = max(best, sum(p.points for p in chosen if p is not None and p.points > 0))
    return round(best, 2)


def test_greedy_by_points_is_not_enough():
    """The case that rules out the obvious implementation.

    Taking the highest scorer first puts the running back in the FLEX, which
    strands the better of the two wide receivers. A correct solver seats the
    receiver in FLEX and both running backs in their own slots."""
    players = [
        player(1, 25.0, "RB", BENCH),   # tempting, but there are two RB slots
        player(2, 20.0, "RB", RB),
        player(3, 19.0, "RB", RB),
        player(4, 18.0, "WR", WR),
        player(5, 17.0, "WR", WR),
        player(6, 16.0, "WR", BENCH),
    ]
    slots = [RB, RB, WR, WR, FLEX]
    result = optimal_lineup(players, slots)
    assert result.total == 25.0 + 20.0 + 19.0 + 18.0 + 17.0
    assert result.total == brute_force_best(players, slots)


@pytest.mark.parametrize("seed", range(30))
def test_matches_brute_force_on_random_rosters(seed):
    """Thirty random rosters, each checked exhaustively.

    Kept small (six players, at most four seats) because the oracle is factorial
    in the pool size; the awkward cases the solver could get wrong -- a scarce
    FLEX, a roster that cannot fill every seat, negative scores -- all fit
    comfortably inside that.
    """
    rng = random.Random(seed)
    slots = [QB, RB, RB, WR, FLEX][: rng.randint(2, 4)]
    positions = ["QB", "RB", "WR", "TE"]
    players = [
        player(i, round(rng.uniform(-2, 30), 1), rng.choice(positions),
               rng.choice([QB, RB, WR, TE, FLEX, BENCH]))
        for i in range(1, 7)
    ]
    assert optimal_lineup(players, slots).total == brute_force_best(players, slots)


def test_a_negative_scoring_player_is_never_seated():
    """An empty seat scores zero, which beats a player who cost you two points."""
    players = [player(1, -2.0, "WR", WR), player(2, -5.0, "WR", BENCH)]
    result = optimal_lineup(players, [WR])
    assert result.total == 0.0
    assert result.seats[0].player is None


def test_regret_is_never_negative():
    """A manager cannot beat the optimum. A negative value here would mean the
    eligibility data is wrong, not that somebody got lucky."""
    players = [player(1, 20.0, "QB", QB), player(2, 5.0, "QB", BENCH)]
    assert optimal_lineup(players, [QB]).regret == 0.0


def test_every_named_swap_is_legal():
    """The narrative half of bench regret.

    The first implementation sorted the worst starters and the best bench players
    and zipped them, which produced "you should have started your backup
    quarterback instead of your running back". Arithmetically suggestive and
    impossible, and the commentary would have said it out loud.
    """
    players = [
        player(1, 20.0, "QB", QB), player(2, 18.0, "RB", RB), player(3, 4.0, "RB", RB),
        player(4, 12.0, "WR", WR), player(5, 9.0, "WR", WR), player(6, 7.0, "TE", TE),
        player(7, 6.0, "RB", FLEX),
        player(8, 22.0, "RB", BENCH), player(9, 15.0, "WR", BENCH), player(10, 25.0, "QB", BENCH),
    ]
    slots = [QB, RB, RB, WR, WR, TE, FLEX]
    result = optimal_lineup(players, slots)

    from engine.scoring import eligible_slots

    for started, benched in result.swaps:
        assert benched.points > started.points, "a swap that loses points is not a swap"
        # Both must be able to occupy the same seat, or the swap cannot be made.
        assert eligible_slots(started) & eligible_slots(benched) - {BENCH}, (
            f"{benched.name} cannot play where {started.name} played"
        )
    assert result.worst_swap is not None
    assert result.worst_swap[1].points - result.worst_swap[0].points == 18.0


def test_all_play_removes_the_schedule_from_the_standings():
    records = all_play({1: 120.0, 2: 110.0, 3: 100.0, 4: 100.0})
    assert records[1].record == "3-0"
    # Three notional games each, not four: a team does not play itself.
    assert (records[3].wins, records[3].losses, records[3].ties) == (0, 2, 1)
    assert records[3].record == "0-2-1"
    assert records[3].win_pct == pytest.approx(0.5 / 3)
    assert records[2].record == "2-1"


def test_luck_is_expressed_in_wins():
    """"You are 1.8 wins luckier than you deserve" is a sentence somebody will
    argue with, which is the entire point of the figure."""
    assert luck_index(actual_wins=7, all_play_pct=0.52, weeks=10) == 1.8
    assert luck_index(actual_wins=3, all_play_pct=0.52, weeks=10) == -2.2
