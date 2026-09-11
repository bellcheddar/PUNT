"""Parser tolerance.

ESPN changes payload shapes without notice, so every parser here is total: it
accepts any JSON at all and records what it could not understand rather than
raising. These tests are the proof of that, and they are deliberately hostile.
"""

from __future__ import annotations

import pytest

from espn.models import (
    LeagueSettings,
    Matchup,
    Player,
    Side,
    Team,
    parse_matchups,
    parse_members,
    parse_teams,
    sanitise_user_text,
)


@pytest.mark.parametrize("payload", [{}, {"teams": None}, {"teams": "nope"}, {"teams": [None, 3, "x"]}])
def test_parsers_never_raise_on_rubbish(payload):
    assert isinstance(parse_teams(payload), list)
    assert isinstance(parse_matchups(payload, 1), list)
    assert isinstance(parse_members(payload), dict)


def test_a_missing_key_degrades_one_object_not_the_request():
    team = Team.from_raw({"id": 7})
    assert team.id == 7
    assert team.name == "Team 7"
    assert "no team name" in team.problems


def test_team_name_is_treated_as_hostile_user_input():
    team = Team.from_raw({"id": 1, "name": "  <script>alert(1)</script>Ferrets  "})
    assert "<" not in team.name and ">" not in team.name
    assert team.name == "alert(1)Ferrets"


@pytest.mark.parametrize("raw,expected", [
    ("", ""), (None, ""), (42, ""), ("   ", ""), ("a\n\n b", "a b"),
])
def test_sanitise_handles_every_empty_shape(raw, expected):
    assert sanitise_user_text(raw) == expected


def test_emoji_only_team_names_survive():
    """A manager will do this. It must not become an empty card."""
    team = Team.from_raw({"id": 2, "name": "🐐🐐🐐"})
    assert team.name == "🐐🐐🐐"
    assert team.monogram == "?", "a monogram falls back rather than rendering half an emoji"


def test_card_colour_is_stable_across_a_rename():
    """Ten phones agree on a team's colour with no shared state, and the colour
    survives the manager renaming the team mid-season."""
    before = Team.from_raw({"id": 5, "name": "Bench Mob Rule"})
    after = Team.from_raw({"id": 5, "name": "Something Else Entirely"})
    assert before.hue == after.hue
    assert Team.from_raw({"id": 6, "name": "Bench Mob Rule"}).hue != before.hue


def test_season_totals_are_never_read_as_a_weekly_score():
    """ESPN's stats array mixes season and weekly, actual and projected, in one
    flat list. Reading it without filtering both discriminators is how a card
    ends up saying 1,400 points."""
    player = Player.from_entry(
        {
            "lineupSlotId": 0,
            "playerPoolEntry": {
                "player": {
                    "id": 1, "fullName": "Dax Ashgrove", "defaultPositionId": 1,
                    "stats": [
                        {"scoringPeriodId": 11, "statSourceId": 0, "statSplitTypeId": 0, "appliedTotal": 1412.0},
                        {"scoringPeriodId": 11, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": 22.4},
                        {"scoringPeriodId": 11, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": 18.1},
                        {"scoringPeriodId": 10, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": 31.0},
                    ],
                }
            },
        },
        scoring_period=11,
    )
    assert player.points == 22.4
    assert player.projected == 18.1


def test_an_unknown_lineup_slot_is_bench_not_a_starter():
    """Guessing wrong in the other direction would silently add points to a
    team's total, which is worse than under-counting and harder to spot."""
    player = Player.from_entry({"lineupSlotId": "???"}, scoring_period=1)
    assert player.is_starter is False
    assert "treated as bench" in " ".join(player.problems)


def test_remaining_never_goes_negative():
    """A player past his projection has nothing left to give. Allowing this to go
    negative makes a live projected total tick *down* as somebody scores."""
    player = Player(id=1, name="x", slot_id=0, position="QB", pro_team="KC", points=30.0, projected=18.0)
    assert player.remaining == 0.0


def test_completed_weeks_fall_back_to_the_settled_roster():
    """`rosterForCurrentScoringPeriod` is the live view and disappears once a
    week settles; without the fallback, last week renders empty."""
    matchup = Matchup.from_raw(
        {
            "id": 1, "matchupPeriodId": 11, "winner": "HOME",
            "home": {"teamId": 1, "totalPoints": 101.2,
                     "rosterForMatchupPeriod": {"entries": [{"lineupSlotId": 0, "appliedStatTotal": 20.0}]}},
            "away": {"teamId": 2, "totalPoints": 99.0},
        },
        scoring_period=11,
    )
    assert matchup.home.players and matchup.home.players[0].points == 20.0
    assert matchup.margin == 2.2


def test_starting_slots_expand_to_one_entry_per_seat():
    settings = LeagueSettings.from_raw(
        {"settings": {"rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1, "20": 7}}}}
    )
    assert settings.starting_slots == [0, 2, 2, 4, 4, 6, 23, 16, 17]
    assert 20 not in settings.starting_slots


def test_game_state_turns_a_zero_into_a_goose_egg():
    """A player who scored nothing and a player who has not kicked off look
    identical on the fantasy feed. Telling a manager they have four players left
    when they have none is the difference between hope and a goose egg."""
    yet_to_start = Player(id=1, name="a", slot_id=0, position="QB", pro_team="SF",
                          pro_team_id=25, points=0.0, projected=19.0, game_over=False)
    finished_flat = Player(id=2, name="b", slot_id=4, position="WR", pro_team="ATL",
                           pro_team_id=1, points=0.0, projected=11.5, game_over=True)
    side = Side(team_id=1, total=0.0, players=[yet_to_start, finished_flat])

    assert side.in_play == 1
    assert yet_to_start.remaining == 19.0
    assert finished_flat.remaining == 0.0, "a finished game cannot still owe you points"
    assert side.live_projection == 19.0


def test_unknown_game_state_falls_back_rather_than_guessing():
    """With no NFL feed, `game_over` is None and nothing claims to know."""
    player = Player(id=1, name="a", slot_id=0, position="QB", pro_team="SF", points=4.0, projected=19.0)
    assert player.game_over is None
    assert player.remaining == 15.0
    assert Side(team_id=1, players=[player]).in_play == 0


def test_nfl_scoreboard_parses_into_fantasy_team_ids():
    from espn.models import parse_game_states

    states = parse_game_states({
        "events": [{
            "status": {"period": 3, "displayClock": "7:12", "type": {"state": "in", "completed": False}},
            "competitions": [{
                "situation": {"isRedZone": True, "possession": "12", "shortDownDistanceText": "2nd & Goal"},
                "competitors": [
                    {"homeAway": "home", "score": "21", "team": {"id": "12", "abbreviation": "KC"}},
                    {"homeAway": "away", "score": "17", "team": {"id": "13", "abbreviation": "LV"}},
                ],
            }],
        }]
    })
    assert states[12].live and states[12].red_zone and states[12].possession
    assert states[12].opponent == "LV"
    assert states[13].possession is False
    assert states[13].opponent == "KC"


def test_a_player_who_is_simply_projected_nothing_is_not_a_degraded_feed():
    """Zero is data. No row is a problem. They are not the same thing.

    The check used to be `projected == 0.0 and points == 0.0`, which describes a
    backup on a bench, somebody ruled out, or a defence on bye -- ESPN projects
    those at zero and they score zero. The first real league put four of them on
    the degradation banner within a minute of the cookies going in, which is how
    a banner stops being read by the time it means something.
    """
    def entry(stats):
        return {"lineupSlotId": 20, "playerId": 1,
                "playerPoolEntry": {"player": {"id": 1, "fullName": "Somebody Quiet",
                                               "defaultPositionId": 2, "stats": stats}}}

    quiet = Player.from_entry(entry([
        {"scoringPeriodId": 1, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": 0.0},
        {"scoringPeriodId": 1, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": 0.0},
    ]), 1)
    assert quiet.points == 0.0 and quiet.projected == 0.0
    assert "no week stats" not in quiet.problems

    # A projection and no actual yet is every player before kickoff.
    pregame = Player.from_entry(entry([
        {"scoringPeriodId": 1, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": 0.0},
    ]), 1)
    assert "no week stats" not in pregame.problems

    # Rows for another week, or a season split, are not this week's numbers.
    elsewhere = Player.from_entry(entry([
        {"scoringPeriodId": 7, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": 19.4},
        {"scoringPeriodId": 1, "statSourceId": 0, "statSplitTypeId": 0, "appliedTotal": 204.0},
    ]), 1)
    assert elsewhere.points == 0.0, "a season total was read as a weekly score"
    assert "no week stats" in elsewhere.problems

    assert "no week stats" in Player.from_entry(entry([]), 1).problems
    assert "no week stats" in Player.from_entry(entry("not a list"), 1).problems
