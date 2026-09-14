"""The four decision panels: the look-ahead, promise vs delivery, draft receipts
and the move ledger.

Everything else in PUNT is about what happened to the lineups the managers set.
These ask whether they set the right ones, and each reads a feed nothing else
does. Two of those feeds have a trap in them that the real league showed before
any test did: ESPN gives every team defence a NEGATIVE player id, and its player
filter rejects a `limit` with a 400. Both are pinned here.
"""

from __future__ import annotations

import copy
import json

import pytest

from espn import feeds
from espn.cache import TTLCache
from espn.client import EspnClient, LeagueRepository, request_headers
from espn.models import (
    LeagueSettings,
    Player,
    parse_byes,
    parse_draft,
    parse_moves,
    parse_player_history,
)
from views.viewmodels import (
    DRAFT_WINDOW,
    _hole,
    _season_points,
    draft_detail,
    draft_view,
    lookahead_detail,
    lookahead_view,
    moves_detail,
    moves_view,
    trust_detail,
    trust_view,
)


@pytest.fixture
def afternoon(no_network):
    """Mid-Sunday in the demo, where every one of these has something to say."""
    from config import DEMO_RECORDING
    from espn.replay import ReplayTransport

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    transport.clock.seek(int(transport.recording.duration * 0.55))
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    return LeagueRepository(client).snapshot()


# -- the feeds --------------------------------------------------------------

def test_a_team_defence_is_not_thrown_away_for_its_negative_id():
    """The real draft has ten defence picks and every one has an id below zero.
    A parser that asked for `player_id > 0` lost all ten without a word."""
    picks = parse_draft({"draftDetail": {"picks": [
        {"overallPickNumber": 1, "roundId": 1, "roundPickNumber": 1, "teamId": 2, "playerId": 4241},
        {"overallPickNumber": 2, "roundId": 1, "roundPickNumber": 2, "teamId": 3, "playerId": -16012},
    ]}})
    assert [p.player_id for p in picks] == [4241, -16012]
    moves = parse_moves({"transactions": [{
        "id": "t", "type": "FREEAGENT", "status": "EXECUTED", "scoringPeriodId": 3, "teamId": 3,
        "items": [{"type": "DROP", "playerId": -16012, "fromTeamId": 3, "toTeamId": -1}]}]})
    assert moves[0].dropped == (-16012,)


def test_an_empty_draft_slot_is_not_a_player():
    """The real draft's seventeenth round is ten slots nobody filled, each with
    player id -1. Read as picks, they became ten "Unknown player" rows."""
    picks = parse_draft({"draftDetail": {"picks": [
        {"overallPickNumber": 161, "roundId": 17, "roundPickNumber": 1, "teamId": 2, "playerId": -1},
        {"overallPickNumber": 160, "roundId": 16, "roundPickNumber": 10, "teamId": 3, "playerId": -16012},
    ]}})
    assert [p.player_id for p in picks] == [-16012]


def test_a_cut_defence_is_named_from_its_id():
    """ESPN's player feed never returns a defence, so one released since the
    draft has no name anywhere. Its id carries the NFL team: minus (16000 + id),
    checked against every defence on a real roster."""
    from espn.models import defence_team
    from views.viewmodels import _person

    assert defence_team(-16012) == "KC"
    assert defence_team(-1) == "" and defence_team(4241) == ""
    assert _person({}, -16012) == {"name": "KC D/ST", "position": "D/ST"}
    assert _person({4241: {"name": "A Player", "position": "WR"}}, 4241)["name"] == "A Player"


def test_only_executed_roster_moves_are_moves(afternoon):
    """ESPN's transaction list carries draft picks, lineup changes and failed
    claims beside the real moves. None of them changed a roster."""
    kinds = {m.kind for m in afternoon.moves}
    assert kinds == {"waiver", "free agent", "trade"}
    raw = parse_moves({"transactions": [
        {"id": "a", "type": "WAIVER", "status": "FAILED_ROSTERLIMIT", "items": [
            {"type": "ADD", "playerId": 1, "toTeamId": 3, "fromTeamId": -1}]},
        {"id": "b", "type": "ROSTER", "status": "EXECUTED", "items": [
            {"type": "LINEUP", "playerId": 1, "toTeamId": 3, "fromTeamId": 3}]},
        {"id": "c", "type": "DRAFT", "status": "EXECUTED", "items": [
            {"type": "DRAFT", "playerId": 1, "toTeamId": 3, "fromTeamId": -1}]},
    ]})
    assert raw == []


def test_a_trade_is_a_move_for_both_sides(afternoon):
    trade = [m for m in afternoon.moves if m.kind == "trade"]
    assert len(trade) == 2
    first, second = trade
    assert first.added == second.dropped and first.dropped == second.added


def test_player_history_reads_only_this_seasons_weeks():
    """The unfiltered list carries a season total and last year's weeks next to
    this year's, and either would double a player's points."""
    history = parse_player_history({"players": [{"id": 7, "onTeamId": 4, "player": {
        "id": 7, "fullName": "A Player", "defaultPositionId": 2, "proTeamId": 12, "stats": [
            {"seasonId": 2026, "scoringPeriodId": 1, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": 12.5},
            {"seasonId": 2026, "scoringPeriodId": 1, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": 10.0},
            {"seasonId": 2026, "scoringPeriodId": 0, "statSourceId": 0, "statSplitTypeId": 0, "appliedTotal": 99.0},
            {"seasonId": 2025, "scoringPeriodId": 2, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": 30.0},
        ]}}]}, season=2026)
    assert history[7].points == {1: 12.5}
    assert history[7].projected == {1: 10.0}
    assert history[7].on_team_id == 4


def test_byes_and_the_final_week_are_read():
    assert parse_byes({"settings": {"proTeams": [{"id": 12, "byeWeek": 6}, {"id": 0, "byeWeek": 0}]}}) == {12: 6}
    settings = LeagueSettings.from_raw({"status": {"finalScoringPeriod": 18}, "settings": {}})
    assert settings.final_scoring_period == 18


def test_the_player_filter_is_one_espn_accepts():
    """Measured on the real league: adding `limit` turns the request into a 400,
    and a season filter returns no weekly lines at all."""
    headers = request_headers(feeds.PLAYER_HISTORY, 2026, (4241, -16012))
    body = json.loads(headers["x-fantasy-filter"])["players"]
    assert body["filterIds"]["value"] == [4241, -16012]
    assert body["filterStatsForSplitTypeIds"]["value"] == [1]
    assert "limit" not in body and "filterStatsForExternalIds" not in body
    assert "x-fantasy-filter" not in request_headers(feeds.DRAFT, 2026, None)


def test_a_new_player_set_is_a_new_cache_entry():
    """Asked to miss: the set of tracked players grows with every move, and an
    answer cached for yesterday's set is missing the player just dropped."""
    class Counting:
        calls = 0

        def fetch(self, feed, season, league_id, scoring_period=None, player_ids=None):
            Counting.calls += 1
            return {"players": [{"id": pid} for pid in player_ids or ()]}

    client = EspnClient(transport=Counting(), season=2026, league_id="1", cache=TTLCache())
    client.get(feeds.PLAYER_HISTORY, player_ids=(1, 2))
    client.get(feeds.PLAYER_HISTORY, player_ids=(1, 2))
    assert Counting.calls == 1
    client.get(feeds.PLAYER_HISTORY, player_ids=(1, 2, 3))
    assert Counting.calls == 2


def test_a_front_office_feed_failing_costs_only_its_own_panel(no_network):
    """None of these is what a Sunday runs on. A draft feed that 500s must not
    put a problem on the stale banner or take the scores with it."""
    from config import DEMO_RECORDING
    from espn.client import UpstreamError
    from espn.replay import ReplayTransport

    inner = ReplayTransport.load(DEMO_RECORDING, speed=0.0)

    class NoDraft:
        draft_calls = 0

        def fetch(self, feed, season, league_id, scoring_period=None, player_ids=None):
            if feed.name == feeds.DRAFT.name:
                NoDraft.draft_calls += 1
                raise UpstreamError("draft: HTTP 500", status=500)
            extra = {"player_ids": player_ids} if player_ids else {}
            return inner.fetch(feed, season, league_id, scoring_period, **extra)

    client = EspnClient(transport=NoDraft(), season=2025, league_id="demo", cache=TTLCache())
    repo = LeagueRepository(client)
    snap = repo.snapshot()
    assert snap.draft == []
    # The first version of this test failed exactly here: the draft's 500 had
    # started the backoff every feed shares, so the moves were refused next.
    assert snap.moves and snap.matchups
    assert not client.backing_off, "an optional feed put the live feeds into backoff"
    assert client.auth.ok
    assert not any("draft" in p for p in snap.problems)
    assert draft_view(snap)["reason"] == "draft"
    # And a broken view is not asked for again on the very next request.
    repo.snapshot()
    assert NoDraft.draft_calls == 1


def test_a_parsed_feed_is_reused_until_its_payload_changes(repo, no_network):
    snap = repo.snapshot()
    again = repo.snapshot()
    assert snap.draft is again.draft, "the same payload should not be parsed twice"
    repo.client.cache.invalidate()
    fresh = repo.snapshot()
    assert fresh.draft == snap.draft


# -- the look-ahead ---------------------------------------------------------

def _player(pid, slot, projected, injury="ACTIVE", pro_team_id=1, eligible=(2, 23, 20)):
    return Player(id=pid, name=f"p{pid}", slot_id=slot, position="RB", pro_team="ATL",
                  pro_team_id=pro_team_id, eligible_slots=eligible, projected=projected, injury=injury)


def test_every_kind_of_hole_is_named():
    week, byes = 12, {9: 12}
    assert _hole(_player(1, 2, 10.0, pro_team_id=9), week, byes) == ("BYE", "out")
    assert _hole(_player(2, 2, 10.0, "OUT"), week, byes) == ("OUT", "out")
    assert _hole(_player(3, 2, 10.0, "INJURY_RESERVE"), week, byes) == ("IR", "out")
    assert _hole(_player(4, 2, 0.0), week, byes) == ("ZERO", "out")
    assert _hole(_player(5, 2, 8.0, "QUESTIONABLE"), week, byes) == ("Q", "doubt")
    assert _hole(_player(6, 2, 8.0, "DOUBTFUL"), week, byes) == ("D", "doubt")
    assert _hole(_player(7, 2, 8.0), week, byes) is None


def test_the_look_ahead_finds_the_planted_holes(afternoon):
    view = lookahead_view(afternoon)
    assert view["available"] and len(view["games"]) == 5
    labels = {h["label"] for s in view["rows"] for h in s["holes"]}
    assert {"BYE", "OUT", "IR", "Q", "D", "ZERO"} <= labels
    unfixable = [h for s in view["rows"] for h in s["holes"] if h["fix"] is None]
    assert unfixable, "a kicker with no kicker on the bench has no fix, and has to say so"


def test_an_injured_backup_is_never_the_fix(afternoon):
    roster = afternoon.next_rosters[1]
    injured_bench = {p.name for p in roster if not p.is_starter and p.injury == "OUT"}
    assert injured_bench, "the fixture plants an injured backup on team 1"
    fixes = {h["fix"]["player"] for s in lookahead_view(afternoon)["rows"] for h in s["holes"] if h["fix"]}
    assert not injured_bench & fixes


def test_a_fix_is_worth_what_it_adds(afternoon):
    for side in lookahead_view(afternoon)["rows"]:
        gains = sum(h["fix"]["gain"] for h in side["holes"] if h["fix"])
        assert side["fixed"] == pytest.approx(side["projected"] + gains, abs=0.15)
        assert all(h["fix"]["gain"] > 0 for h in side["holes"] if h["fix"])
        if side["gain"] > 3:
            assert side["win_fixed"] >= side["win"], f"{side['team']}: fixing holes cannot lower the odds"


def test_the_look_ahead_only_reads_from_the_live_week(afternoon):
    archived = copy.copy(afternoon)
    archived.scoring_period = afternoon.scoring_period - 3
    assert lookahead_view(archived)["reason"] == "archive"
    bare = copy.copy(afternoon)
    bare.next_rosters = {}
    assert lookahead_view(bare)["reason"] == "none"
    assert lookahead_detail(afternoon, 999) == {}
    detail = lookahead_detail(afternoon, 1)
    assert detail["opponent"]["id"] != 1 and detail["row"]["lineup"]


# -- promise vs delivery ----------------------------------------------------

def test_delivery_is_actual_over_projected_across_settled_weeks(afternoon):
    view = trust_view(afternoon)
    assert view["available"] and view["weeks"] == len(afternoon.archive)
    row = view["rows"][0]
    projected = actual = 0.0
    for week in afternoon.archive.values():
        for matchup in week:
            for side in (matchup.home, matchup.away):
                if side.team_id == row["id"]:
                    projected += sum(p.projected for p in side.starters)
                    actual += sum(p.points for p in side.starters)
    assert row["delivered"] == pytest.approx(actual / projected * 100, abs=0.2)
    assert row["delivered"] >= view["rows"][-1]["delivered"]


def test_the_win_chance_error_has_the_sign_of_the_gap(afternoon):
    for row in trust_view(afternoon)["rows"]:
        if abs(row["bias"]) > 1:
            assert (row["win_error"] > 0) == (row["bias"] > 0)


def test_trust_needs_a_finished_week(afternoon):
    bare = copy.copy(afternoon)
    bare.archive = {}
    assert trust_view(bare)["available"] is False
    detail = trust_detail(afternoon, 1)
    assert len(detail["row"]["per_week"]) == len(afternoon.archive)
    assert all(p["diff"] > 0 for p in detail["over"]) and all(p["diff"] < 0 for p in detail["under"])


# -- draft receipts ---------------------------------------------------------

def test_a_pick_is_measured_against_the_picks_around_it(afternoon):
    view = draft_view(afternoon)
    assert view["available"] and len(view["picks"]) == 170
    totals = [p["points"] for p in view["picks"]]
    i = 40
    neighbours = totals[i - DRAFT_WINDOW:i] + totals[i + 1:i + 1 + DRAFT_WINDOW]
    assert view["picks"][i]["expected"] == pytest.approx(sum(neighbours) / len(neighbours), abs=0.1)
    assert all(r["picks"] == 17 for r in view["rows"])


def test_draft_points_include_the_live_week(afternoon):
    points = _season_points(afternoon)
    live = {p.id for m in afternoon.matchups for s in (m.home, m.away) for p in s.players if p.points}
    assert live and all(afternoon.scoring_period in points[pid] for pid in live)


def test_draft_statuses_add_up(afternoon):
    view = draft_view(afternoon)
    statuses = {p["status"] for p in view["picks"]}
    assert statuses == {"kept", "elsewhere", "released"}
    detail = draft_detail(afternoon, view["rows"][0]["id"])
    assert len(detail["picks"]) == 17 and detail["row"]["rank"] == 1
    empty = copy.copy(afternoon)
    empty.player_history, empty.matchups = {}, []
    assert draft_view(empty)["reason"] == "points"


# -- the move ledger --------------------------------------------------------

def test_net_is_what_came_in_minus_what_went_out_since(afternoon):
    view = moves_view(afternoon)
    points = _season_points(afternoon)
    move = next(m for m in afternoon.moves if m.added and m.dropped)
    line = next(x for x in view["ledger"] if x["id"] == move.id)
    since = lambda pid: sum(v for w, v in points.get(pid, {}).items() if w >= move.week)  # noqa: E731
    expected = sum(map(since, move.added)) - sum(map(since, move.dropped))
    assert line["net"] == pytest.approx(expected, abs=0.2)


def test_every_team_has_a_ledger_row_even_with_no_moves(afternoon):
    view = moves_view(afternoon)
    assert len(view["rows"]) == len(afternoon.teams)
    detail = moves_detail(afternoon, view["rows"][0]["id"])
    assert detail["moves"] and detail["moves"][0]["week"] >= detail["moves"][-1]["week"]
    quiet = copy.copy(afternoon)
    quiet.moves = []
    assert moves_view(quiet)["available"] is False
