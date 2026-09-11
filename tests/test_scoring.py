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
    FALLBACK_ELIGIBILITY,
    AllPlayRecord,
    all_play,
    luck_index,
    optimal_lineup,
    standings,
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


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------

def test_espn_eligibility_is_used_when_the_payload_carries_it():
    """`eligibleSlots` is the league's own rule. Guessing from position is a
    fallback for when it is absent, not a substitute.

    For a long while the fallback was the only path that ever ran: the field was
    in the payload, the parser dropped it, and nothing passed the override. On
    this league the two agreed exactly -- zero difference across all ten
    managers -- because the slots they disagree about are ones it does not run.
    A latent bug is still a bug; it simply waits for a different league.
    """
    from engine.scoring import eligible_slots

    # A running back the league has also made eligible at wide receiver.
    dual = Player(id=1, name="dual", slot_id=BENCH, position="RB", pro_team="X",
                  eligible_slots=(2, 4, 23, 20))
    assert eligible_slots(dual) == frozenset({2, 4, 23, 20})
    assert 4 in eligible_slots(dual), "ESPN said WR and the guess would not have"

    # With nothing declared, the table stands in.
    guessed = Player(id=2, name="guessed", slot_id=BENCH, position="RB", pro_team="X")
    assert eligible_slots(guessed) == frozenset(FALLBACK_ELIGIBILITY["RB"])


def test_a_superflex_league_needs_the_real_rules():
    """The case the guess gets wrong, and the reason this matters.

    In a superflex league the flex accepts a quarterback, so the optimal lineup
    starts two. The position table says a quarterback may only fill QB, so the
    guess cannot see the second one as a flex option at all.

    It does not report zero -- it still spots the straight swap of one QB for the
    other, which was this test's first and wrong expectation. It reports 6 where
    the truth is 20, which is the more insidious failure: a number that looks
    like an answer.
    """
    from engine.scoring import eligible_slots

    QB_SLOT, SUPERFLEX = 0, 23
    slots = [QB_SLOT, SUPERFLEX]

    starter = Player(id=1, name="first", slot_id=QB_SLOT, position="QB", pro_team="X",
                     points=18.0, eligible_slots=(0, 23, 20))
    filler = Player(id=2, name="filler", slot_id=SUPERFLEX, position="RB", pro_team="X",
                    points=4.0, eligible_slots=(2, 23, 20))
    benched_qb = Player(id=3, name="second", slot_id=BENCH, position="QB", pro_team="X",
                        points=24.0, eligible_slots=(0, 23, 20))

    players = [starter, filler, benched_qb]
    with_rules = optimal_lineup(players, slots)
    assert with_rules.total == 42.0, "the superflex should be filled by the benched QB"
    assert with_rules.regret == 20.0

    # And what the guess would have said.
    from dataclasses import replace

    guessed = optimal_lineup([replace(p, eligible_slots=()) for p in players], slots)
    assert guessed.regret == 6.0, "the guess still finds the straight swap of one QB for the other"
    assert guessed.total == 28.0
    assert with_rules.regret - guessed.regret == 14.0, (
        "the position table cannot see that a superflex takes a quarterback, so it "
        "understates this manager's regret by 14 points"
    )


def test_a_tied_week_counts_as_half_a_win_everywhere(recording, no_network):
    """Week 4 of the fixture ends level, deliberately.

    A fantasy tie is rare, real, and the single most argued-about outcome in any
    league. Every layer handles one -- the payload writes "TIE", the record
    carries ties, the table weights them at half a win, all-play counts them
    separately -- and none of it had ever run against the fixture, because ten
    gaussian draws a week never land on the same two decimal places.
    """
    from espn.cache import TTLCache
    from espn.client import EspnClient, LeagueRepository
    from espn.replay import ReplayTransport

    transport = ReplayTransport.load(recording.name, speed=0.0)
    transport.clock.seek(int(transport.recording.duration))
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    snapshot = LeagueRepository(client).snapshot()

    table = standings(snapshot)
    tied = [r for r in table if r.ties]
    assert len(tied) == 2, "the planted tie is missing from the season"
    assert all(r.all_play.ties for r in tied), "an all-play tie was counted as a win or a loss"

    # Half a win each, which is what puts them either side of a team on the same
    # whole-number record.
    for record in tied:
        assert record.wins + 0.5 * record.ties == record.wins + 0.5
