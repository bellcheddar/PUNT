"""Win probability.

The two thresholds the product actually uses -- 5% for DOOM and 10% for a
Legendary card -- are both in the tail, which is where a normal approximation is
worst and where these tests therefore concentrate.
"""

from __future__ import annotations

import pytest

from engine.simulate import win_probability
from espn.models import Matchup, Player, Side

QB, RB, BENCH = 0, 2, 20


def make_player(pid, points, projected, game_over=False, slot=RB):
    return Player(id=pid, name=f"p{pid}", slot_id=slot, position="RB", pro_team="KC",
                  pro_team_id=12, points=points, projected=projected, game_over=game_over)


def make_matchup(home_players, away_players, home_total, away_total):
    return Matchup(
        id=1, matchup_period=11,
        home=Side(team_id=1, total=home_total, players=home_players),
        away=Side(team_id=2, total=away_total, players=away_players),
    )


def test_a_finished_matchup_is_arithmetic_not_a_probability():
    """Once nobody can score, the UI must stop hedging."""
    matchup = make_matchup(
        [make_player(1, 100.0, 100.0, game_over=True)],
        [make_player(2, 90.0, 90.0, game_over=True)],
        home_total=100.0, away_total=90.0,
    )
    result = win_probability(matchup)
    assert result.home_win == 1.0
    assert result.settled is True
    assert result.draws == 0


def test_players_still_to_play_beat_a_smaller_deficit_with_none():
    """The judgement a projection difference cannot make: trailing by 30 with two
    starters left is a better position than trailing by 8 with none."""
    trailing_with_players = win_probability(make_matchup(
        [make_player(1, 20.0, 45.0), make_player(2, 10.0, 35.0)],
        [make_player(3, 60.0, 60.0, game_over=True)],
        home_total=30.0, away_total=60.0,
    )).home_win

    trailing_with_nobody = win_probability(make_matchup(
        [make_player(1, 52.0, 52.0, game_over=True)],
        [make_player(3, 60.0, 60.0, game_over=True)],
        home_total=52.0, away_total=60.0,
    )).home_win

    assert trailing_with_players > trailing_with_nobody
    assert trailing_with_nobody == 0.0


def test_two_identical_polls_return_an_identical_number():
    """A win probability that flickers by a point every thirty seconds because the
    simulator reseeded is indistinguishable, to somebody watching, from something
    actually happening."""
    matchup = make_matchup(
        [make_player(1, 40.0, 70.0)], [make_player(2, 44.0, 68.0)],
        home_total=40.0, away_total=44.0,
    )
    assert win_probability(matchup).home_win == win_probability(matchup).home_win


def test_probabilities_are_complementary_and_bounded():
    matchup = make_matchup(
        [make_player(1, 40.0, 70.0)], [make_player(2, 44.0, 68.0)],
        home_total=40.0, away_total=44.0,
    )
    result = win_probability(matchup)
    assert 0.0 <= result.home_win <= 1.0
    assert result.home_win + result.away_win == pytest.approx(1.0, abs=1e-4)
    assert result.for_team(1) == result.home_win
    assert result.for_team(2) == result.away_win


def test_a_player_projected_for_what_is_needed_is_a_coin_flip():
    """The sanity check that stops the tail test below from measuring nothing.

    The first version of that test called "needing 22 points from one player" a
    long shot, and the simulator returned 53%. It was right and the test was
    wrong: the player was projected for 22. A long shot needs the projection to
    be short of what is required, not merely the current score.
    """
    matchup = make_matchup(
        [make_player(1, 3.0, 25.1)],
        [make_player(2, 25.0, 25.0, game_over=True)],
        home_total=3.0, away_total=25.0,
    )
    assert 0.35 < win_probability(matchup, draws=4000).home_win < 0.65


def test_the_tail_is_fat_enough_for_a_comeback_to_exist():
    """A manager needing 22 from a player projected for 8 is a long shot, not zero.

    Fantasy scoring is lumpy: a touchdown is a six-point step, so three of them
    from a player projected for eight points is unlikely rather than impossible.
    A normal fit puts almost no weight here, which would make the Legendary card
    (a win from under 10%) impossible to mint and would fire DOOM far too early.
    """
    matchup = make_matchup(
        [make_player(1, 3.0, 11.0)],
        [make_player(2, 25.0, 25.0, game_over=True)],
        home_total=3.0, away_total=25.0,
    )
    probability = win_probability(matchup, draws=8000).home_win
    assert 0.002 < probability < 0.25, probability


def test_more_players_left_means_more_uncertainty():
    lopsided = win_probability(make_matchup(
        [make_player(1, 60.0, 61.0)], [make_player(2, 55.0, 56.0)],
        home_total=60.0, away_total=55.0,
    )).home_win
    wide_open = win_probability(make_matchup(
        [make_player(1, 60.0, 110.0)], [make_player(2, 55.0, 115.0)],
        home_total=60.0, away_total=55.0,
    )).home_win
    assert lopsided > wide_open, "a five point lead is safer with nothing left to play"


def test_a_side_ahead_of_projection_mid_game_is_not_yet_decided():
    """Every starter past his projection at half time used to read as nobody
    left to play, so the matchup was reported as settled arithmetic while all
    four games were still on. They owe the second half of their projection."""
    def live(pid, points, projected):
        player = make_player(pid, points, projected, game_over=False)
        player.game_elapsed = 0.5
        return player

    matchup = make_matchup(
        [live(1, 30.0, 20.0), live(2, 25.0, 15.0)],
        [live(3, 20.0, 30.0), live(4, 20.0, 30.0)],
        home_total=55.0, away_total=40.0,
    )
    result = win_probability(matchup)
    assert result.settled is False
    assert result.home_in_play == 2
    assert 0.5 < result.home_win < 1.0


def test_the_clock_running_down_moves_the_number_without_a_score():
    """Same score, less time: the leader's chances must rise, and the memo must
    not hand back the earlier answer."""
    def matchup_at(elapsed):
        home = make_player(1, 20.0, 20.0, game_over=False)
        away = make_player(2, 14.0, 20.0, game_over=False)
        home.game_elapsed = away.game_elapsed = elapsed
        return make_matchup([home], [away], home_total=20.0, away_total=14.0)

    early = win_probability(matchup_at(0.25)).home_win
    late = win_probability(matchup_at(0.9)).home_win
    assert late > early
