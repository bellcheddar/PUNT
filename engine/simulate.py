"""Monte Carlo win probability for a live matchup.

The question the app keeps asking is "should this manager still be watching?",
and a projection difference cannot answer it: a team trailing by 30 with two
starters yet to play is in better shape than a team trailing by 8 with none, and
a naive projection share says the opposite.

Why simulation rather than a closed form. The sum of a lineup's remaining points
is very nearly normal, so a normal approximation gives the right answer most of
the time and is instant. It is wrong precisely where this app cares: the tails.
Fantasy scoring is lumpy -- a touchdown is a six-point step, not a smooth
increment -- so the probability that a manager needing 22 points from one player
gets them is materially higher than a normal fit suggests. Legendary cards are
minted on wins from under 10%, and DOOM fires below 5%, so the two thresholds in
the product are both in the part of the distribution the approximation is worst
at.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Sequence

from espn.models import Matchup, Player, Side

#: Draws per matchup. 2,000 gives a standard error of about 1 percentage point
#: near 50% and under 0.5 near the tails, which is finer than the number is ever
#: displayed to. Ten matchups at this size is a few hundredths of a second, and
#: the result is cached for the poll anyway.
DEFAULT_DRAWS = 2000

#: Chance that any given remaining chunk of a skill player's projection arrives
#: as a touchdown rather than as accumulated yardage. Deliberately crude: the
#: point is to make the distribution lumpy, not to model football.
TD_RATE = {"QB": 0.30, "RB": 0.24, "WR": 0.24, "TE": 0.18, "K": 0.0, "D/ST": 0.10}

#: Mean fantasy value of one simulated touchdown, six points plus the yardage
#: that came with it. Used as the divisor when converting a share of remaining
#: projected points into a whole number of touchdowns, so that the simulated mean
#: comes out equal to the projection rather than a few percent above it.
TD_VALUE_MEAN = 8.05

#: Multiplier on remaining points to get a standard deviation. A player with 15
#: points still to come routinely finishes anywhere between 3 and 30, and
#: pretending otherwise produces win probabilities that are confidently wrong.
SPREAD = 0.62

#: Floor, in points, so a player with almost nothing left is not treated as
#: certain. A kicker with 1.2 remaining can still miss and can still hit two.
SPREAD_FLOOR = 1.6


@dataclass
class WinProbability:
    """A matchup's odds, and enough context to explain them."""

    home_id: int
    away_id: int
    home_win: float
    draws: int = DEFAULT_DRAWS
    home_mean: float = 0.0
    away_mean: float = 0.0
    home_in_play: int = 0
    away_in_play: int = 0

    @property
    def away_win(self) -> float:
        return round(1.0 - self.home_win, 4)

    def for_team(self, team_id: int) -> float:
        if team_id == self.home_id:
            return self.home_win
        if team_id == self.away_id:
            return self.away_win
        return 0.5

    @property
    def settled(self) -> bool:
        """Nobody left to score on either side: the result is arithmetic, not a
        probability, and the UI should stop pretending otherwise."""
        return self.home_in_play == 0 and self.away_in_play == 0


def _spread(remaining: float) -> float:
    return max(SPREAD_FLOOR, SPREAD * remaining)


def _draw_player(player: Player, rng: random.Random) -> float:
    """One simulated finish for one player, in points added from here."""
    remaining = player.remaining
    if remaining <= 0:
        return 0.0

    td_rate = TD_RATE.get(player.position, 0.15)
    total = 0.0

    # Lumps first: each expected touchdown either lands or does not, which is
    # what puts real weight on both "nothing at all" and "far more than
    # projected" instead of piling it all around the mean.
    expected_tds = remaining * td_rate / TD_VALUE_MEAN
    whole, fraction = divmod(expected_tds, 1.0)
    tds = int(whole) + (1 if rng.random() < fraction else 0)
    total += tds * (6.0 + rng.uniform(0.1, 4.0))

    # Then the smooth remainder, truncated at zero: a player cannot un-score.
    smooth_mean = remaining * (1 - td_rate)
    total += max(0.0, rng.gauss(smooth_mean, _spread(smooth_mean)))
    return total


def _draw_side(starters: Sequence[Player], rng: random.Random) -> float:
    return sum(_draw_player(p, rng) for p in starters)


def win_probability(
    matchup: Matchup, draws: int = DEFAULT_DRAWS, seed: int | None = None
) -> WinProbability:
    """Probability the home side wins, by simulation.

    The RNG is seeded from the matchup and the current scores rather than left to
    chance. Two consecutive polls with identical scores must produce an identical
    number: a win probability that flickers by a point every thirty seconds
    because the simulator reseeded is indistinguishable, to somebody watching,
    from something actually happening.
    """
    home, away = matchup.home, matchup.away
    home_live = [p for p in home.starters if p.remaining > 0]
    away_live = [p for p in away.starters if p.remaining > 0]

    if seed is None:
        # A stable hash, not Python's `hash()`. Tuples of numbers happen to hash
        # deterministically today, but `hash()` is salted per process for strings
        # and is explicitly not a stable API: a golden-file test of the whole
        # afternoon would then pass or fail depending on PYTHONHASHSEED.
        key = f"{matchup.id}|{home.total:.2f}|{away.total:.2f}|{len(home_live)}|{len(away_live)}"
        seed = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big")
    rng = random.Random(seed)

    if not home_live and not away_live:
        decided = 1.0 if home.total > away.total else (0.0 if home.total < away.total else 0.5)
        return WinProbability(
            home_id=home.team_id, away_id=away.team_id, home_win=decided, draws=0,
            home_mean=home.total, away_mean=away.total,
        )

    wins = 0.0
    home_sum = away_sum = 0.0
    for _ in range(draws):
        home_final = home.total + _draw_side(home_live, rng)
        away_final = away.total + _draw_side(away_live, rng)
        home_sum += home_final
        away_sum += away_final
        if home_final > away_final:
            wins += 1
        elif home_final == away_final:
            wins += 0.5

    return WinProbability(
        home_id=home.team_id,
        away_id=away.team_id,
        home_win=round(wins / draws, 4),
        draws=draws,
        home_mean=round(home_sum / draws, 2),
        away_mean=round(away_sum / draws, 2),
        home_in_play=len(home_live),
        away_in_play=len(away_live),
    )


def probabilities_for(snapshot, draws: int = DEFAULT_DRAWS) -> dict[int, WinProbability]:
    """`{matchup_id: WinProbability}` for every live matchup in a snapshot."""
    return {
        m.id: win_probability(m, draws=draws)
        for m in (snapshot.live_matchups or snapshot.matchups)
    }


def team_probabilities(snapshot, draws: int = DEFAULT_DRAWS) -> dict[int, float]:
    """`{team_id: probability of winning this week}`. What DOOM reads."""
    out: dict[int, float] = {}
    for matchup in snapshot.live_matchups or snapshot.matchups:
        result = win_probability(matchup, draws=draws)
        out[matchup.home.team_id] = result.home_win
        out[matchup.away.team_id] = result.away_win
    return out
