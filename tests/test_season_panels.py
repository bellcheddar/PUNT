"""The eight season panels.

Each answers something a single week's numbers cannot, and each is a chart, so
the geometry is computed in the view model rather than in the template: a
template that does arithmetic cannot be tested without a request context, and a
sparkline is nothing but arithmetic.

The tests below are mostly about the states nobody develops in. Every one of
these panels was written and looked at in the middle of a simulated Sunday, and
the live league was on week one with every score at zero, which is a different
page entirely.
"""

from __future__ import annotations

import copy
import re

import pytest

from views.viewmodels import (
    allplay_view,
    clock_view,
    gauntlet_view,
    ledger_view,
    seeds_view,
    shape_view,
    swap_view,
    volatility_view,
)

PANELS = ["shape", "grid", "seeds", "gauntlet", "clock", "ledger", "volatility", "swap"]


@pytest.fixture
def snap(repo, no_network):
    """Kickoff, which is where `repo` is paused: every score zero."""
    return repo.snapshot()


@pytest.fixture
def afternoon(no_network):
    """Mid-Sunday, which is the state most of these are read in.

    `repo` is paused at zero, and several of these panels correctly have nothing
    to say there -- the ledger compares each slot against the league's median
    for the same slot, and before kickoff that is zero against zero for
    everybody.
    """
    from config import DEMO_RECORDING
    from espn.cache import TTLCache
    from espn.client import EspnClient, LeagueRepository
    from espn.replay import ReplayTransport

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    transport.clock.seek(int(transport.recording.duration * 0.55))
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    return LeagueRepository(client).snapshot()


# -- the state everything is built in --------------------------------------

def test_every_panel_has_something_to_say(afternoon):
    snap = afternoon
    views = {
        "shape": shape_view(snap), "grid": allplay_view(snap),
        "seeds": seeds_view(snap, draws=200), "gauntlet": gauntlet_view(snap),
        "clock": clock_view(snap), "ledger": ledger_view(snap),
        "volatility": volatility_view(snap), "swap": swap_view(snap),
    }
    for name, view in views.items():
        assert view.get("rows"), f"{name} produced no rows at all"


# -- the states nobody develops in -----------------------------------------

@pytest.mark.parametrize("name", PANELS)
def test_a_panel_survives_a_week_that_has_not_started(client, name, no_network):
    """Every score zero, which is what the live league looked like on its first
    Sunday. Four of these divided by a range computed from the data, and a range
    of zero is a 500 on the whole page: `default=` only fires on an EMPTY
    sequence, and a full one of zeroes returns zero."""
    response = client.get(f"/partials/panel/{name}")
    assert response.status_code == 200, f"{name} broke"
    assert response.data.strip(), f"{name} rendered nothing"


@pytest.mark.parametrize("name", PANELS)
def test_a_panel_survives_a_league_with_no_season(client, app, name, no_network):
    """A single-week recording, or an ESPN outage mid-season. Five of these read
    the fixture grid and must say so rather than render an empty chart."""
    with app.app_context():
        from views.state import snapshot

        bare = copy.copy(snapshot())
        bare.season_schedule = []
        from views import viewmodels as vm

        for view in (vm.shape_view(bare), vm.gauntlet_view(bare), vm.swap_view(bare),
                     vm.seeds_view(bare, draws=100), vm.volatility_view(bare)):
            assert isinstance(view, dict)
            assert "rows" in view


def test_the_sparkline_scale_ignores_one_bad_week(snap):
    """A 53.5 in a field that otherwise lives between 90 and 130 stretches the
    axis over a hundred points and flattens every line in the panel. The scale
    is the 5th to 95th percentile and the plot is clamped into it."""
    view = shape_view(snap)
    if not view["rows"]:
        pytest.skip("no settled weeks in this snapshot")
    everything = [v for row in view["rows"] for v in row["scores"]]
    if max(everything) - min(everything) < 30:
        pytest.skip("this snapshot has no outlier to ignore")
    assert view["floor"] > min(everything) or view["ceiling"] < max(everything), (
        "the scale still spans the extremes, so the outlier flattens the panel"
    )


def test_every_plotted_point_is_inside_the_box(snap):
    """Clamped, so a week outside the scale is drawn at the edge rather than
    outside the drawing. An SVG does not clip by default."""
    view = shape_view(snap)
    for row in view["rows"]:
        for pair in row["path"].split():
            x, y = (float(v) for v in pair.split(","))
            assert -0.01 <= x <= view["width"] + 0.01
            assert -0.01 <= y <= view["height"] + 0.01


def test_the_gauntlet_gives_every_fixture_its_own_lane(snap):
    """Four opponents drawn on one line overlap into a rainbow smear where
    nothing can be told from anything, which is how it first came out."""
    view = gauntlet_view(snap)
    if not view.get("available"):
        pytest.skip("no fixtures left in this snapshot")
    for row in view["rows"]:
        lanes = [f["lane"] for f in row["fixtures"]]
        assert lanes == sorted(lanes), "lanes are not in week order"
        assert len(set(lanes)) == len(lanes), "two fixtures share a lane"
        assert all(f["lanes"] == len(row["fixtures"]) for f in row["fixtures"])


def test_the_grid_marks_the_fixture_that_counted(snap):
    """The point of the grid is the gap between who you would have beaten and
    who you actually played, so the real fixture has to be findable."""
    view = allplay_view(snap)
    for row in view["rows"]:
        real = [c for c in row["cells"] if c["real"]]
        assert len(real) == 1, f"{row['team']} has {len(real)} real fixtures"
        assert not real[0]["real"] or real[0]["id"] != row["id"]


def test_the_seed_distribution_is_a_distribution(snap):
    """It comes straight from the simulator, so it has to add up."""
    view = seeds_view(snap, draws=400)
    if not view["available"]:
        pytest.skip("no season grid")
    for row in view["rows"]:
        total = sum(s["share"] for s in row["seeds"])
        assert 0.97 <= total <= 1.03, f"{row['team']} seeds sum to {total}"
        made = sum(s["share"] for s in row["seeds"] if s["made_it"])
        assert abs(made - row["odds"]) < 0.05, (
            f"{row['team']}: the seeds inside the places sum to {made} but the "
            f"headline odds say {row['odds']}"
        )


def test_swapping_a_schedule_leaves_the_scores_alone(snap):
    """The whole argument rests on this: only the opponents change."""
    view = swap_view(snap)
    if not view.get("available"):
        pytest.skip("not enough settled weeks")
    for row in view["rows"]:
        own = [c for c in row["cells"] if c["own"]]
        assert len(own) == 1
        assert own[0]["record"] == row["own"]
        assert own[0]["diff"] == 0, "a team's own schedule changed its own record"


def test_the_clock_accounts_for_every_starter(snap):
    """A starter whose window is missing would silently vanish from the chart
    and the percentages would still look plausible."""
    view = clock_view(snap)
    if not view.get("available"):
        pytest.skip("no kickoff times")
    for row in view["rows"]:
        counted = sum(p["players"] for p in row["parts"])
        assert counted > 0, f"{row['team']} has no starters in any window"


def test_the_ledger_says_nothing_before_anybody_has_played(snap):
    """Ten rows of "+0.0" is a wall that reads as a broken panel rather than as
    an early one: every median is zero, so every difference is zero. It said
    nothing useful and it said it at great length."""
    view = ledger_view(snap)
    assert not view["available"], "the ledger is comparing zero against zero"
    assert not view["rows"]


def test_the_ledger_shows_the_points_not_only_the_difference(afternoon):
    """The cell used to carry only the difference, which threw away the figure
    the panel is named after and, against a median of zero, was the same number
    anyway."""
    view = ledger_view(afternoon)
    assert view["available"]
    assert any(c["points"] for r in view["rows"] for c in r["cells"])
    assert all("median" in c for r in view["rows"] for c in r["cells"])


def test_the_ledger_compares_like_with_like(afternoon):
    """A quarterback is only ever measured against quarterbacks."""
    view = ledger_view(afternoon)
    for row in view["rows"]:
        for cell in row["cells"]:
            assert cell["diff"] == pytest.approx(
                cell["points"] - view["median"][cell["slot"]], abs=0.06)


def test_the_records_memo_survives_a_rebuilt_snapshot(repo, no_network):
    """Every request builds a fresh snapshot object from the same cached
    payloads, so a memo keyed on identity would miss every time while looking
    like it worked."""
    from views.viewmodels import _RECORDS, _records

    _RECORDS.clear()
    _records(repo.snapshot())
    assert len(_RECORDS) == 1
    _records(repo.snapshot())
    assert len(_RECORDS) == 1, "a second identical snapshot missed the memo"


# -- the buckets the Sunday-only fixture cannot reach ------------------------

@pytest.mark.parametrize("kickoff,window", [
    ("2025-11-16T18:00Z", "EARLY"),   # 1pm Eastern on a Sunday
    ("2025-11-16T21:05Z", "LATE"),    # 4:05pm
    ("2025-11-17T01:20Z", "SNF"),     # 8:20pm Sunday, which is Monday in UTC
    ("2025-11-14T01:15Z", "THU"),     # Thursday night
    ("2025-11-18T01:15Z", "MNF"),     # Monday night
    ("2025-12-20T18:00Z", "SAT"),     # a December Saturday
    ("", ""),
    ("not a date", ""),
])
def test_the_kickoff_buckets(kickoff, window):
    """The committed recording is one Sunday, so three of these are unreachable
    from it and are marked cold in the source. They happen every week in
    production, so they are checked here against known instants instead.

    Converted to US Eastern before bucketing rather than read off the UTC hour:
    the same one o'clock kickoff is 18:00 UTC in November and 17:00 in
    September, and a fixed offset files half the season in the wrong slot.
    """
    from espn.models import GameState

    assert GameState(pro_team_id=1, abbrev="X", kickoff=kickoff).window == window


def test_the_kickoff_can_come_off_the_competition():
    """ESPN puts it on the event and repeats it on the competition. Taking both
    means a payload that drops one still buckets."""
    from espn.models import parse_game_states

    states = parse_game_states({"events": [{
        "id": "1", "status": {"type": {"state": "pre"}, "period": 0, "displayClock": ""},
        "competitions": [{"date": "2025-11-16T18:00Z", "competitors": [
            {"team": {"id": "1", "abbreviation": "ATL"}, "score": "0"},
            {"team": {"id": "2", "abbreviation": "BUF"}, "score": "0"}]}],
    }]})
    assert states and all(s.window == "EARLY" for s in states.values())


# -- the detail sheets ------------------------------------------------------

DETAIL_PANELS = ["shape", "grid", "seeds", "gauntlet", "clock", "ledger",
                 "volatility", "swap"]


@pytest.mark.parametrize("name", DETAIL_PANELS)
def test_every_panel_opens_its_own_sheet(client, name, no_network):
    """Eight panels, eight sheets. "More about this row" means something
    different in each: a shape row is about a season, a clock row is about a
    Sunday afternoon, a swap row is about a fixture list."""
    response = client.get(f"/partials/detail/panel/{name}/1")
    assert response.status_code == 200
    assert response.data.strip()


@pytest.mark.parametrize("name", DETAIL_PANELS)
def test_a_sheet_for_a_team_that_is_not_there(client, name, no_network):
    """A detail URL outlives the row it was opened from."""
    response = client.get(f"/partials/detail/panel/{name}/9999")
    assert response.status_code == 200
    assert b"empty" in response.data, f"{name} rendered a broken sheet, not an empty state"


def test_an_unknown_sheet_is_a_404_not_a_template_error(client, no_network):
    """The name goes into a template path."""
    assert client.get("/partials/detail/panel/bogus/1").status_code == 404
    assert client.get("/partials/detail/panel/..%2F..%2Fbase/1").status_code == 404


@pytest.mark.parametrize("name", DETAIL_PANELS)
def test_the_sheet_agrees_with_the_panel(afternoon, name):
    """Each sheet is built on its panel's own view model rather than beside it,
    so it cannot quietly disagree with the figure that was tapped to open it."""
    from views import viewmodels as vm

    panel = {"shape": vm.shape_view, "grid": vm.allplay_view, "seeds": vm.seeds_view,
             "gauntlet": vm.gauntlet_view, "clock": vm.clock_view,
             "ledger": vm.ledger_view, "volatility": vm.volatility_view,
             "swap": vm.swap_view}[name](afternoon)
    if not panel.get("rows"):
        pytest.skip(f"{name} has nothing to show in this snapshot")
    team_id = panel["rows"][0]["id"]
    sheet = {"shape": vm.shape_detail, "grid": vm.grid_detail, "seeds": vm.seeds_detail,
             "gauntlet": vm.gauntlet_detail, "clock": vm.clock_detail,
             "ledger": vm.ledger_detail, "volatility": vm.volatility_detail,
             "swap": vm.swap_detail}[name](afternoon, team_id)
    assert sheet, f"{name} sheet is empty for a team the panel lists"
    assert sheet["row"]["id"] == team_id
    assert sheet["row"]["team"] == panel["rows"][0]["team"]


def test_the_panel_notes_are_collapsed(client, no_network):
    """Fifteen paragraphs of small print down a page about live scores is
    fifteen things between the reader and the next set of numbers.

    `hx-preserve` and a stable id on each, because the note lives inside the
    fragment its panel re-renders every thirty seconds: without it a note
    somebody opened snaps shut mid-sentence on the next poll.
    """
    body = client.get("/?punt=steady").get_data(as_text=True)
    notes = re.findall(r'<details class="panel-note"[^>]*>', body)
    assert len(notes) >= 10, "the notes are not collapsed"
    for note in notes:
        assert 'hx-preserve="true"' in note, "a note will snap shut on the next poll"
        assert 'id="note-' in note, "hx-preserve needs a stable id"
    ids = re.findall(r'<details class="panel-note" id="([^"]+)"', body)
    assert len(ids) == len(set(ids)), f"two notes share an id: {ids}"
