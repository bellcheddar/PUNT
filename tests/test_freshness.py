"""Does a change upstream actually reach the page?

Every route returns 200 whether or not it is wired to anything, so "it renders"
proves nothing. The dangerous failure in this app is a cache or a memo keyed on
something that does not change: it serves a stale answer forever, at full speed,
with no error anywhere, and the page looks perfectly healthy.

So these tests ask the caches to MISS. A cache tested only for hits is a cache
whose correctness has never been examined, and that is how the season-records
memo shipped keyed on the NUMBER of games in each settled week rather than on
their scores -- which meant an ESPN stat correction, a routine Tuesday event in
fantasy football, left six panels showing the pre-correction season for as long
as the process lived.
"""

from __future__ import annotations

import copy
import hashlib
import re

import pytest

from engine.scoring import season_records
from engine.simulate import probabilities_for
from views.viewmodels import _RECORDS, _records


@pytest.fixture
def afternoon(no_network):
    from config import DEMO_RECORDING
    from espn.cache import TTLCache
    from espn.client import EspnClient, LeagueRepository
    from espn.replay import ReplayTransport

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    transport.clock.seek(int(transport.recording.duration * 0.5))
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    return LeagueRepository(client).snapshot()


def _probs(snap):
    return {k: round(v.home_win, 4) for k, v in probabilities_for(snap).items()}


# -- the win-probability memo ----------------------------------------------

def test_a_score_moving_moves_the_probability(afternoon):
    changed = copy.deepcopy(afternoon)
    side = (changed.live_matchups or changed.matchups)[0].home
    side.total += 20
    side.starters[0].points += 20
    assert _probs(afternoon) != _probs(changed)


def test_a_lineup_change_with_no_score_change_still_misses(afternoon):
    """A manager benches somebody before kickoff. Both totals are identical and
    the distribution is not, so a memo keyed on totals alone would hit and be
    wrong."""
    changed = copy.deepcopy(afternoon)
    side = (changed.live_matchups or changed.matchups)[0].home
    for player in side.players:
        if player.is_starter and player.points == 0:
            player.slot_id = 20
            break
    assert _probs(afternoon) != _probs(changed)


def test_a_game_finishing_misses(afternoon):
    """Same points, nothing left to come: a very different probability."""
    changed = copy.deepcopy(afternoon)
    for player in (changed.live_matchups or changed.matchups)[0].home.starters:
        if player.game_over is False:
            player.game_over = True
            break
    assert _probs(afternoon) != _probs(changed)


def test_an_unchanged_snapshot_hits(afternoon):
    """And it still has to be a cache. A memo that never hits is a slower
    function with extra steps."""
    assert _probs(afternoon) == _probs(copy.deepcopy(afternoon))


# -- the season-records memo -----------------------------------------------

def test_a_corrected_settled_week_reaches_the_season(afternoon):
    """The bug this file exists for.

    ESPN corrects stats retrospectively: a reception reclassified, a fumble
    credited elsewhere. It changes the mean, the spread, the all-play record and
    the luck figure, and it leaves the number of games in the week exactly as it
    was -- which is what the key used to be made of.
    """
    changed = copy.deepcopy(afternoon)
    week = sorted(changed.settled_weeks)[0]
    matchup = changed.settled_weeks[week][0]
    team_id = matchup.home.team_id
    matchup.home.total += 25

    direct = round(season_records(changed)[team_id].mean, 3)
    assert direct != round(season_records(afternoon)[team_id].mean, 3), (
        "the fixture has no settled weeks to correct"
    )
    _RECORDS.clear()
    assert round(_records(afternoon)[team_id].mean, 3) != round(
        _records(changed)[team_id].mean, 3), "the memo is serving a pre-correction season"
    assert round(_records(changed)[team_id].mean, 3) == direct


def test_the_records_memo_still_hits(afternoon):
    _RECORDS.clear()
    first = _records(afternoon)
    assert _records(copy.deepcopy(afternoon)) is first, "a fresh snapshot missed the memo"


# -- and the whole way through, at the fragment ----------------------------

#: Panels that read only SETTLED weeks. These must NOT change as a Sunday
#: progresses: a change would mean they were reading the live week by accident.
SETTLED_ONLY = {"shape", "swap", "volatility", "gauntlet", "luck"}

FRAGMENTS = ["/partials/album", "/partials/scorebar", "/partials/cheer",
             "/partials/watchnow"] + [
    f"/partials/panel/{name}" for name in
    ("ticker", "regret", "trouble", "odds", "allplay", "luck", "shape", "grid",
     "seeds", "gauntlet", "clock", "ledger", "volatility", "swap")]


def _digest(client, path):
    body = client.get(path).get_data(as_text=True)
    # Numbers only: prose and markup are not what a live update changes.
    return hashlib.sha1(" ".join(re.findall(r"-?\d+\.?\d*", body)).encode()).hexdigest()


@pytest.mark.parametrize("path", FRAGMENTS)
def test_the_fragment_changes_when_the_afternoon_does(client, app, transport, path, no_network):
    """The end-to-end version: seek the replay clock, invalidate the feed cache
    the way thirty seconds of wall clock does, and see whether the rendered
    fragment actually moved."""
    with app.app_context():
        from views.state import state

        cache = state().client.cache

    duration = int(transport.recording.duration)
    transport.clock.seek(int(duration * 0.30))
    cache.invalidate()
    early = _digest(client, path)

    transport.clock.seek(int(duration * 0.75))
    cache.invalidate()
    late = _digest(client, path)

    name = path.rsplit("/", 1)[1]
    if name in SETTLED_ONLY:
        assert early == late, (
            f"{name} changed during a Sunday, so it is reading the live week "
            f"when it should read only settled ones"
        )
    else:
        assert early != late, (
            f"{name} rendered identically at two points hours apart: it is not "
            f"wired to the poll, or something upstream of it is serving a "
            f"cached answer"
        )
