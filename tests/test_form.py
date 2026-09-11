"""The form rating and the fixture stake.

Both exist because a number that is the same on every row is not information.
The album used to be ordered by the raw score, which at three in the afternoon
mostly ranks teams by how many of their players kicked off at one; the Cheer
panel's league-wide column used to read STAKE on all sixteen rows.
"""

from __future__ import annotations

import pytest

from config import DEMO_RECORDING
from espn.cache import TTLCache
from espn.client import EspnClient, LeagueRepository
from espn.replay import ReplayTransport
from views.viewmodels import (
    FORM_WEIGHTS,
    PACE_CAP,
    _elapsed,
    _fixture_stake,
    _stake_heat,
    album_view,
    cheer_view,
    game_detail,
)


@pytest.fixture
def afternoon():
    """Mid-afternoon, not the opening whistle.

    `repo` is paused at zero, where every score is zero and every rating is the
    same neutral number -- which is correct, and proves nothing about a rating
    whose whole purpose is to separate teams once the games are running.
    """
    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    transport.clock.seek(transport.recording.duration * 0.45)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    return LeagueRepository(client).snapshot()


def test_the_weights_sum_to_one(no_network):
    """The rating is presented out of 100 and a set summing to 0.9 would make
    100 unreachable without anything failing."""
    assert sum(FORM_WEIGHTS.values()) == pytest.approx(1.0)


def test_every_card_is_rated(repo, no_network):
    cards = album_view(repo.snapshot())
    assert cards
    for card in cards:
        assert 0 <= card["form"] <= 100
        assert set(card["form_parts"]) == set(FORM_WEIGHTS)
        assert all(0 <= v <= 1 for v in card["form_parts"].values())


def test_the_album_is_ordered_by_form_and_not_by_score(afternoon, no_network):
    cards = album_view(afternoon)
    assert [c["form"] for c in cards] == sorted((c["form"] for c in cards), reverse=True)
    # The point of the change: on the committed Sunday the two orders differ.
    # If this ever stops being true the rating has collapsed back onto `total`
    # and the ranking is the thing it was supposed to replace.
    by_score = sorted(cards, key=lambda c: c["total"], reverse=True)
    assert [c["id"] for c in cards] != [c["id"] for c in by_score]


def test_pace_does_not_reward_an_early_kick_off(afternoon, no_network):
    """The expectation is prorated, so a team is measured against what its
    starters were due BY NOW rather than against their whole projection.

    Compared against the sum of the raw projections and not against the card's
    `projected`, which is the LIVE projection -- total plus what is left -- and
    sits below the raw sum for anyone having a good afternoon. That caught this
    test out before it caught anything else.
    """
    snap = afternoon
    for card in album_view(snap):
        if card["pace"] is None:
            continue
        side = snap.matchup_for(card["id"]).side_for(card["id"])
        whole = sum(p.projected for p in side.starters)
        assert card["expected"] <= whole + 0.01, card["name"]
        if any(not snap.games[p.pro_team_id].finished
               for p in side.starters if p.pro_team_id in snap.games):
            assert card["expected"] < whole, card["name"]


def test_pace_is_capped(repo, no_network):
    """One kickoff return in the first quarter, when the denominator is tiny,
    must not pin a team at the top of the album until teatime."""
    for card in album_view(repo.snapshot()):
        assert card["form_parts"]["pace"] <= 1.0
    assert PACE_CAP >= 1.0


def test_elapsed_reads_the_game_clock(repo, no_network):
    snap = repo.snapshot()
    for game in snap.games.values():
        share = _elapsed(game)
        assert 0.0 <= share <= 1.0
        if game.finished:
            assert share == 1.0


def test_no_two_stakes_are_the_same(repo, no_network):
    """The complaint that started this: every league-wide row said STAKE."""
    rows = cheer_view(repo.snapshot())
    assert rows
    assert all(row["verdict"] == "" for row in rows)
    assert len({row["at_stake"] for row in rows}) > 1
    assert len({row["heat"] for row in rows}) > 1


def test_a_swing_needs_a_starter_on_both_sides(repo, no_network):
    """A fixture that only one half of a head-to-head is invested in moves both
    totals and no margin, which is not a swing."""
    snap = repo.snapshot()
    for pro_team_id in snap.games:
        stake = _fixture_stake(snap, {pro_team_id})
        for swing in stake["swings"]:
            assert all(side["count"] > 0 for side in swing["sides"])


def test_the_stake_matches_between_the_panel_and_the_sheet(repo, no_network):
    """The sheet explains the figure that was tapped, so it cannot compute a
    second opinion of it."""
    snap = repo.snapshot()
    for row in cheer_view(snap):
        sheet = game_detail(snap, row["pro_team_id"])
        assert sheet["at_stake"] == row["at_stake"]
        assert sheet["stake_kind"] == row["stake_kind"]


def test_a_finished_game_is_never_hot(no_network):
    """Whatever it was worth, it is over: it cannot still decide anything."""
    stake = {"starters": 8, "swings": [{}, {}], "live": 40.0, "scored": 90.0}
    assert _stake_heat(stake, finished=True) == "done"
    assert _stake_heat(stake, finished=False) == "hot"


def test_an_empty_fixture_is_not_a_stake(no_network):
    assert _stake_heat({"starters": 0, "swings": [], "live": 0.0}, finished=False) == "none"
