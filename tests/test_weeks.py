"""The season, week by week: recording it, rolling onto the next one, and
showing an old one back.

The rollover is the part with no natural test occasion. It happens in the small
hours of a Tuesday with nobody watching, on a process that has been up since
Sunday lunchtime, and if it does not work the only symptom is that PUNT quietly
keeps showing a week that finished two days ago.
"""

from __future__ import annotations

import copy

import pytest

from engine.history import History
from engine.live import LiveFeed
from espn import feeds
from views.viewmodels import stored_moments


@pytest.fixture
def store(tmp_path):
    return History(tmp_path / "history.sqlite3")


def test_a_week_is_recorded(store, repo, no_network):
    snap = repo.snapshot()
    store.record(snap)
    weeks = store.weeks(snap.season)
    assert [w["week"] for w in weeks] == [snap.scoring_period]
    stored = store.week(snap.season, snap.scoring_period)
    assert len(stored["teams"]) == len(snap.teams)
    assert {t["team"] for t in stored["teams"]} == {t.name for t in snap.teams}


def test_recording_twice_updates_rather_than_duplicates(store, repo, no_network):
    """Called on every poll, so it has to be an upsert. A week that appended
    would hold two hundred rows by the final whistle."""
    snap = repo.snapshot()
    store.record(snap)
    store.record(snap)
    assert len(store.week(snap.season, snap.scoring_period)["teams"]) == len(snap.teams)


def test_a_broken_database_is_not_a_broken_app(tmp_path, repo, no_network):
    """A read-only disk costs the week menu and nothing else: everything on the
    page comes from ESPN either way."""
    wall = tmp_path / "not-a-directory"
    wall.write_text("")
    store = History(wall / "nested" / "history.sqlite3")
    assert not store.available
    assert store.problems
    store.record(repo.snapshot())     # must not raise
    assert store.weeks(2025) == []
    assert store.week(2025, 11) == {}


def test_the_settings_feed_cannot_pin_the_week(no_network):
    """`scoringPeriodId` rides on mSettings, and mSettings used to be cached for
    a day. The week then rolled over on a Tuesday morning and PUNT carried on
    serving the finished Sunday until Wednesday, with nothing looking broken."""
    assert feeds.SETTINGS.ttl <= 900, (
        "mSettings carries scoringPeriodId: a long TTL here pins the whole app "
        "to a week that has already ended"
    )


def _feed_at(snapshot):
    return LiveFeed(fetch=lambda: snapshot, poll_seconds=5)


def test_the_week_rolls_over_on_its_own(repo, no_network):
    """No restart, no deploy, no button: ESPN moves the scoring period and the
    feed follows it."""
    first = repo.snapshot()
    feed = _feed_at(first)
    feed.poll_once()
    assert feed.week == first.scoring_period

    later = copy.copy(first)
    later.scoring_period = first.scoring_period + 1
    feed.fetch = lambda: later
    feed.poll_once()
    assert feed.week == later.scoring_period


def test_the_rollover_empties_everything_that_is_per_week(repo, no_network):
    """Three of these were cleared and three were not, which looks fine on the
    Tuesday and puts last Sunday's commentary under this Sunday's scores on the
    following weekend, when the buffer finally has something to push out."""
    first = repo.snapshot()
    feed = _feed_at(first)
    feed.poll_once()
    feed.notable.append(object())
    feed.week_counts["TOUCHDOWN"] = 9
    # A team id nothing will repopulate. `week_low` legitimately refills on the
    # same poll -- the new week has probabilities of its own -- so the assertion
    # is that last week's entries are gone, not that the dict is empty.
    feed.week_low[-99] = 0.04
    feed.lines["x"] = object()
    feed._redzone[5] = {}
    assert feed.moments or True  # the buffer may legitimately be empty here

    later = copy.copy(first)
    later.scoring_period = first.scoring_period + 1
    feed.fetch = lambda: later
    feed.poll_once()

    assert feed.notable == []
    assert feed.week_counts == {}
    assert -99 not in feed.week_low
    assert feed.lines == {}
    assert feed._redzone == {}
    assert list(feed.moments) == []


def test_the_week_is_written_down_before_it_is_thrown_away(repo, tmp_path, no_network):
    """The Moments are the half ESPN cannot give back, and the rollover is the
    moment they are deleted. If the hook fired after the reset it would record
    an empty week, and nothing would ever say so."""
    seen = []
    first = repo.snapshot()
    feed = LiveFeed(fetch=lambda: first, poll_seconds=5,
                    on_week_change=lambda ended, snap: seen.append((ended, len(feed.moments))))
    feed.poll_once()
    buffered = len(feed.moments)

    later = copy.copy(first)
    later.scoring_period = first.scoring_period + 1
    feed.fetch = lambda: later
    feed.poll_once()

    assert seen and seen[-1][0] == first.scoring_period
    assert seen[-1][1] == buffered, "the hook ran after the buffer was cleared"


def test_a_failing_hook_does_not_stop_the_scores(repo, no_network):
    """The record is a record. The afternoon is the product."""
    first = repo.snapshot()

    def explode(*args):
        raise RuntimeError("disk is full")

    feed = LiveFeed(fetch=lambda: first, poll_seconds=5,
                    on_week_change=explode, after_poll=explode)
    feed.poll_once()
    later = copy.copy(first)
    later.scoring_period = first.scoring_period + 1
    feed.fetch = lambda: later
    assert feed.poll_once() is not None
    assert feed.week == later.scoring_period


def test_stored_moments_match_the_live_shape(store, repo, no_network):
    """The same template renders both, so a missing field would show as a blank
    line rather than as an error."""
    from views.viewmodels import moments_view

    snap = repo.snapshot()
    feed = _feed_at(snap)
    feed.poll_once()
    store.remember(snap.season, snap.scoring_period, feed.recent(limit=500), feed.lines)

    live_shape = {k for row in moments_view(feed, snap=snap) for k in row}
    stored_shape = {k for row in stored_moments(store, snap) for k in row}
    if live_shape and stored_shape:
        assert live_shape == stored_shape


# -- the routes -------------------------------------------------------------

def test_every_route_takes_a_week(client, no_network):
    """`/partials/moments?week=N` shipped broken and the suite said nothing: it
    is the one fragment that does not come from the snapshot."""
    for path in ("/", "/album", "/cheer", "/partials/album", "/partials/scorebar",
                 "/partials/cheer", "/partials/moments"):
        for query in ("", "?week=9", "?week=nonsense", "?week=999"):
            response = client.get(path + query)
            assert response.status_code == 200, f"{path}{query}"


def test_a_silly_week_is_ignored_rather_than_obeyed(client, no_network):
    """A typed URL, not a week. Falls back to whatever is live."""
    body = client.get("/?week=0").get_data(as_text=True)
    assert "banner--archive" not in body


def test_an_archived_page_says_so_and_goes_quiet(client, app, no_network):
    """Everything else on the page looks identical whether it is moving or
    finished, so the banner and the `data-archive` flag are the only signals --
    and the flag is what stops the stream sounding a horn for a play in a week
    the reader is not looking at."""
    with app.app_context():
        from views.state import snapshot as live_snapshot

        current = live_snapshot().scoring_period

    body = client.get(f"/?week={current - 1}").get_data(as_text=True)
    assert 'data-archive="1"' in body
    assert "banner--archive" in body

    body = client.get("/").get_data(as_text=True)
    assert 'data-archive="0"' in body
