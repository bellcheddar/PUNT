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

    def mean_for(self, team_id: int) -> float:
        """The simulated final score for one side. `for_team` gives the odds;
        this gives the number those odds were computed from, which is what a
        reader actually wants when asking how far behind they are."""
        if team_id == self.home_id:
            return self.home_mean
        if team_id == self.away_id:
            return self.away_mean
        return 0.0  # cold: nothing asks a matchup about a team that is not in it

    def for_team(self, team_id: int) -> float:
        if team_id == self.home_id:
            return self.home_win
        if team_id == self.away_id:
            return self.away_win
        return 0.5  # cold: nothing asks a matchup about a team that is not in it

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
        return 0.0  # cold: the callers filter on remaining > 0; this is the guard for anyone who does not

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
            wins += 0.5  # cold: two float draws landing on the same value

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


# --------------------------------------------------------------------------
# playoff odds
# --------------------------------------------------------------------------

@dataclass
class PlayoffOdds:
    """A team's chance of making the playoffs, and what it would take."""

    team_id: int
    odds: float
    seed_odds: dict[int, float] = field(default_factory=dict)
    mean_wins: float = 0.0
    #: Remaining wins after which this team made the playoffs in almost every
    #: simulated season. `None` when no number of wins is enough, or when it is
    #: already through.
    magic_number: int | None = None
    clinched: bool = False
    eliminated: bool = False
    remaining: int = 0

#: Odds beyond which a thing is called rather than reported as a percentage.
#: A "99.9%" on a bar screen invites an argument about the 0.1%; "clinched" does
#: not, and at three thousand draws the two are indistinguishable anyway.
CLINCH_AT = 0.999
ELIMINATED_AT = 0.001

#: How confident a win total has to make a team before it counts as their magic
#: number. Not 1.0: with a finite number of draws, nothing is ever 1.0, and a
#: magic number that never resolves is worse than one that is nearly right.
MAGIC_CONFIDENCE = 0.97


def playoff_odds(snapshot, draws: int = 3000, seed: int = 0) -> dict[int, PlayoffOdds]:
    """Monte Carlo the rest of the season.

    Each simulated season plays out every remaining week from the real schedule
    grid, drawing each team's score from its own distribution of settled weeks.
    That matters more than it sounds: a team averaging 120 with a tight spread is
    a very different playoff proposition from one averaging 120 by alternating
    160 and 80, and a table of records cannot tell them apart.

    The current week, if it is in progress, is seeded from the live projection
    rather than the season mean, with its spread scaled by how much is actually
    left to play. Ignoring that would have the simulator rate a manager's chances
    while pretending the afternoon they are halfway through has not happened.
    """
    from engine.scoring import season_records  # noqa: PLC0415 - avoids a cycle

    records = season_records(snapshot)
    if not records:
        return {}  # cold: a league with no teams

    playoff_places = max(1, snapshot.settings.playoff_team_count or 6)
    current_week = snapshot.settings.current_matchup_period
    weeks = snapshot.season_weeks()
    settled = set(snapshot.settled_weeks)
    future = {week: games for week, games in weeks.items()
              if week not in settled and week >= current_week}
    remaining_count = {tid: 0 for tid in records}
    for games in future.values():
        for matchup in games:
            for side in (matchup.home, matchup.away):
                if side.team_id in remaining_count:
                    remaining_count[side.team_id] += 1

    # Where the current week has already started, the live projection is a far
    # better estimate than the season mean.
    live: dict[int, tuple[float, float]] = {}
    for matchup in snapshot.live_matchups:
        for side in (matchup.home, matchup.away):
            record = records.get(side.team_id)
            if record is None:
                continue  # cold: every side in a live matchup belongs to a team that exists
            fraction_left = side.in_play / max(1, len(side.starters))
            live[side.team_id] = (side.live_projection, record.sigma * fraction_left)

    rng = random.Random(seed or hash_seed(snapshot))
    made = {tid: 0 for tid in records}
    seeds = {tid: {} for tid in records}
    total_wins = {tid: 0.0 for tid in records}
    #: wins_won -> (times seen, times made the playoffs), for the magic number.
    by_wins: dict[int, dict[int, list[int]]] = {tid: {} for tid in records}

    for _ in range(draws):
        wins = {tid: records[tid].wins + 0.5 * records[tid].ties for tid in records}
        points = {tid: records[tid].points_for for tid in records}
        won_remaining = {tid: 0 for tid in records}

        for week in sorted(future):
            for matchup in future[week]:
                scores = {}
                for side in (matchup.home, matchup.away):
                    tid = side.team_id
                    record = records.get(tid)
                    if record is None:
                        continue  # cold: same, in the season loop
                    if week == current_week and tid in live:
                        mean, sigma = live[tid]
                    else:
                        mean, sigma = record.mean, record.sigma
                    scores[tid] = max(0.0, rng.gauss(mean, max(1.0, sigma)))
                if len(scores) != 2:
                    continue  # cold: every pairing in the grid has two teams
                (a, a_score), (b, b_score) = scores.items()
                points[a] += a_score
                points[b] += b_score
                if a_score > b_score:
                    wins[a] += 1
                    won_remaining[a] += 1
                elif b_score > a_score:
                    wins[b] += 1
                    won_remaining[b] += 1
                else:
                    wins[a] += 0.5  # cold: a simulated future week ending level, on two gaussian draws
                    wins[b] += 0.5  # cold: the other half of the same tie

        order = sorted(records, key=lambda tid: (-wins[tid], -points[tid]))
        for position, tid in enumerate(order, start=1):
            total_wins[tid] += wins[tid]
            if position <= playoff_places:
                made[tid] += 1
            seeds[tid][position] = seeds[tid].get(position, 0) + 1

        for tid in records:
            bucket = by_wins[tid].setdefault(won_remaining[tid], [0, 0])
            bucket[0] += 1
            bucket[1] += 1 if order.index(tid) < playoff_places else 0

    out: dict[int, PlayoffOdds] = {}
    for tid in records:
        odds = made[tid] / draws
        out[tid] = PlayoffOdds(
            team_id=tid,
            odds=round(odds, 4),
            seed_odds={seed_no: round(count / draws, 4)
                       for seed_no, count in sorted(seeds[tid].items())},
            mean_wins=total_wins[tid] / draws,
            magic_number=_magic_number(by_wins[tid], remaining_count[tid]),
            clinched=odds >= CLINCH_AT,
            eliminated=odds <= ELIMINATED_AT,
            remaining=remaining_count[tid],
        )
    return out


def _magic_number(by_wins: dict[int, list[int]], remaining: int) -> int | None:
    """The fewest remaining wins that made the playoffs in almost every season.

    Read out of the simulation rather than solved combinatorially. The exact
    answer depends on every other team's results too, which is precisely what the
    simulation already integrated over -- and a number derived from the same runs
    as the odds cannot contradict them, which a separately computed one could.
    """
    for wins in range(remaining + 1):
        seen, made = by_wins.get(wins, [0, 0])
        if seen >= 30 and made / seen >= MAGIC_CONFIDENCE:
            return wins
    return None


def hash_seed(snapshot) -> int:
    """Stable across polls with the same standings, so the odds do not flicker."""
    key = "|".join(
        f"{m.matchup_period}:{m.home.team_id}:{m.home.total:.1f}:{m.away.total:.1f}"
        for m in snapshot.season_schedule
    )
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big")
