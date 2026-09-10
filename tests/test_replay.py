"""The replay harness. Everything downstream of Phase 1 depends on this working."""

from __future__ import annotations

from pathlib import Path

import pytest

from espn import feeds
from espn.client import UpstreamError
from espn.models import parse_matchups


def test_fixture_is_synthetic_and_declares_itself(recording):
    """A recording of the real league carries ten people's ESPN account GUIDs.
    The committed one must always be the invented Sunday."""
    assert recording.synthetic is True
    assert recording.duration > 6 * 3600, "a Sunday slate is longer than six hours"
    assert len(recording.entries) > 100


def test_settings_are_available_before_their_first_capture(transport, recording):
    """Feeds captured once at the top of a recording must still answer at t=0.

    Without the first-entry fallback the opening seconds of every replay would
    have no league settings, and every panel would render its empty state for no
    reason at all."""
    transport.clock.seek(0)
    payload = transport.fetch(feeds.SETTINGS, 2025, "demo", None)
    assert payload["settings"]["name"]


#: The most a team's total can legitimately fall in one poll. Fantasy scores are
#: not monotonic -- an interception, a fumble or a sack for a loss all subtract --
#: so the invariant is "never falls by more than one bad play", not "never falls".
#: The first version of this test asserted monotonicity and immediately caught a
#: -2.0 turnover in the fixture, which was the test being wrong and the fixture
#: being right. The engine's event detection has to survive the same thing.
MAX_SINGLE_PLAY_PENALTY = 6.0


def test_scores_move_forward_without_impossible_jumps(transport, recording, no_network):
    """Walk the whole day and check every team's total against what football can do.

    A payload served out of order reads downstream as points being taken away in
    bulk, which is both wrong and, in an app built around horns, extremely loud.
    """
    previous: dict[int, float] = {}
    opening: dict[int, float] = {}
    for position in range(0, int(recording.duration) + 600, 600):
        transport.clock.seek(position)
        payload = transport.fetch(feeds.SCOREBOARD, 2025, "demo", 11)
        for matchup in parse_matchups(payload, 11):
            for side in (matchup.home, matchup.away):
                before = previous.get(side.team_id, 0.0)
                assert side.total >= before - MAX_SINGLE_PLAY_PENALTY, (
                    f"team {side.team_id} lost {before - side.total:.1f} points at "
                    f"{position}s, which is more than one bad play"
                )
                previous[side.team_id] = side.total
                opening.setdefault(side.team_id, side.total)

    assert all(total > 20 for total in previous.values()), "nobody scored all day"
    assert all(previous[t] > opening[t] for t in previous), "a team ended where it started"


def test_unknown_recording_is_a_clear_error():
    from espn.replay import Recording

    with pytest.raises(FileNotFoundError, match="manifest"):
        Recording.load("no-such-sunday")


def test_missing_feed_names_itself(transport, recording):
    with pytest.raises(UpstreamError, match="kona_player_info"):
        transport.fetch(feeds.PLAYERS, 2025, "demo", None)


def test_the_day_starts_pre_game_and_ends_settled(transport, recording, no_network):
    """The fixture must contain a whole Sunday, not a slice of one: everything
    pre-game at kickoff, everything settled at the end. Without that arc the
    engine's end-of-day events (goose eggs, clinches) have nothing to fire on."""
    from espn.models import parse_game_states

    transport.clock.seek(0)
    opening = parse_game_states(transport.fetch(feeds.NFL, 2025, "demo", None))
    assert opening and all(g.state == "pre" for g in opening.values())

    transport.clock.seek(recording.duration)
    closing = parse_game_states(transport.fetch(feeds.NFL, 2025, "demo", None))
    assert all(g.finished for g in closing.values()), "a game was still running at the end of the day"


def test_every_rostered_player_has_a_real_game(transport, recording, no_network):
    """A player whose pro team is missing from the scoreboard never settles: his
    projection stands all night and his manager is told he is still to play.
    This is how a nineteen-team early window was caught."""
    from espn.models import parse_game_states, parse_matchups

    transport.clock.seek(recording.duration)
    games = parse_game_states(transport.fetch(feeds.NFL, 2025, "demo", None))
    matchups = parse_matchups(transport.fetch(feeds.SCOREBOARD, 2025, "demo", 11), 11)

    missing = {
        p.pro_team
        for m in matchups for side in (m.home, m.away) for p in side.players
        if p.pro_team_id not in games
    }
    assert not missing, f"no game on the scoreboard for {sorted(missing)}"


def test_game_states_settle_promptly_after_the_last_whistle(transport, recording, no_network):
    """A finished game must read finished within a poll or two of finishing.

    The two feeds are polled independently, and the fixture generator originally
    wrote the NFL payload only when the fantasy scores had also changed. A minute
    in which nobody scored therefore dropped the game-state change in the same
    minute, so the last game of the night stayed "in progress" for ten minutes
    after it ended: every player on it kept a live projection, and the simulator
    returned 91% for a matchup that was arithmetically over.
    """
    from espn.models import parse_game_states

    # Five minutes before the recording ends, everything should be settled: the
    # final whistle is well before that.
    transport.clock.seek(recording.duration - 300)
    states = parse_game_states(transport.fetch(feeds.NFL, 2025, "demo", None))
    unfinished = sorted(g.abbrev for g in states.values() if not g.finished)
    assert not unfinished, f"still in progress five minutes from the end: {unfinished}"


def test_no_unreferenced_file_is_tracked_in_the_fixture(recording):
    """Git must track the manifest and exactly the payloads it names.

    This repository lives under an iCloud-synced Documents folder. Regenerating
    the fixture in place makes iCloud resurrect the previous generation as
    conflict copies -- "0002_mMatchupScore 2.json.gz" -- and 99 of them reached a
    commit before anybody looked at a file listing. Nothing reads them, the
    manifest does not mention them, and every other test passed throughout.

    Asserted against the git index rather than the directory on purpose: new
    conflict copies can appear on disk at any moment (218 did during one editing
    session) and they are gitignored, so a test of the working tree would fail
    for a reason that does not matter. What matters is that none is committed.
    """
    import subprocess

    result = subprocess.run(
        ["git", "ls-files", "data/recordings/demo-2025-11-16"],
        capture_output=True, text=True, cwd=recording.directory.parent.parent.parent,
    )
    if result.returncode != 0:
        pytest.skip("not a git checkout")

    tracked = {Path(line).name for line in result.stdout.splitlines() if line.strip()}
    expected = {e.file for e in recording.entries} | {"manifest.json"}

    assert tracked, "the fixture is not committed"
    strays = sorted(tracked - expected)
    assert not strays, f"{len(strays)} unreferenced file(s) tracked, e.g. {strays[:3]}"
    assert not sorted(expected - tracked), "a payload named by the manifest is not committed"
