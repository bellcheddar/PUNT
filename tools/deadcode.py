#!/usr/bin/env python3
"""Which lines never run during a whole simulated Sunday.

Not test coverage. This drives the app the way a real afternoon does -- a full
replay through the client, the cache, the parsers, the event engine, the
commentary and the fact pack -- and reports what never executed.

The distinction matters. INJURY had a detector, fifteen phrase lines and a
passing unit test, and had never once fired against the fixture: its test
constructed a Moment by hand, so coverage looked fine and the feature did not
exist. A test can keep a line warm without the application ever reaching it.

Uses `trace` and `co_lines` from the standard library, because neither coverage
nor pytest-cov is installed here and this needs no more than they provide.

    python3 tools/deadcode.py
    python3 tools/deadcode.py --module engine/events.py
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import logging
import sys
import tempfile
import threading
import time
import trace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent

#: What a Sunday should exercise. `views/viewmodels.py` is included because it is
#: where the data actually reaches the page, and a field nothing renders is dead
#: however many engines compute it. The blueprint modules are excluded: they need
#: a request context and the route tests cover them.
WATCHED = ["engine", "espn"]
EXTRA_FILES = ["views/viewmodels.py"]

#: Lines that are *meant* never to run in a replay, with the reason. Anything
#: here is excluded from the report rather than silently tolerated, so the list
#: is the honest inventory of what a replay cannot reach.
EXPECTED_COLD: dict[str, str] = {}

#: A line carrying this marker is cold on purpose, and the rest of the comment
#: says why. Written in the source rather than in a list of line numbers here,
#: because line numbers rot on the first edit and because the reason belongs
#: where the reader of that branch is standing. Excused lines are counted and
#: reported separately: the point is an honest inventory, not a smaller number.
COLD_MARKER = "# cold:"


def executable_lines(path: Path) -> set[int]:
    """Statement lines, from the AST.

    `co_lines()` was the first attempt and reported every line the interpreter
    can stop on, which includes each line of a multi-line function signature. A
    parameter annotation showing up as "never executed" is noise, and a tool that
    reports noise does not get run twice.

    Docstrings and bare annotations are excluded for the same reason: they are
    not decisions, so they cannot be dead.
    """
    import ast

    tree = ast.parse(path.read_text("utf-8"))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.stmt):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # The `def` line itself runs at import; the body is what matters.
            continue
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue          # a docstring
        if isinstance(node, ast.AnnAssign) and node.value is None:
            continue          # a bare annotation
        if isinstance(node, (ast.If, ast.While)) and node.test.lineno != node.lineno:
            # `if (` on its own line never gets a trace event: the interpreter
            # reports the line of the condition's first operand. Three detectors
            # -- injury, goose egg, lead change -- were reported as never having
            # run while the golden file counted the moments they had produced.
            lines.add(node.test.lineno)
            continue
        lines.add(node.lineno)
    return lines


def _raise() -> None:
    raise RuntimeError("ESPN returned 503")


@contextlib.contextmanager
def quiet(*names: str):
    """Silence a logger while something is broken on purpose.

    Three of the paths driven here are failures -- an ESPN blip, a poll that
    raises, a phrase file that says something impossible -- and each logs a
    traceback the tool's reader has no reason to read. A report with three
    stack traces in it does not get run twice.
    """
    loggers = [logging.getLogger(n) for n in names]
    levels = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        for lg, level in zip(loggers, levels):
            lg.setLevel(level)


def _drive_a_bad_afternoon() -> None:
    """What the parsers see when ESPN is having a day.

    Every parser here is total: it accepts any JSON at all and records what it
    could not understand rather than raising. That is forty-nine lines of
    deliberate degradation, and a well-formed fixture reaches none of it -- so
    the report called the whole tolerance layer dead while tests/test_models.py
    was proving it worked. A degraded response is not a hypothetical: it is what
    a Sunday looks like when ESPN ships a schema change at one o'clock.
    """
    from espn.models import (
        GameState, LeagueSettings, LeagueSnapshot, Matchup, Player, Side, Team,
        parse_game_states, parse_matchups, parse_members, parse_teams,
    )

    for rubbish in ({}, {"teams": None}, {"teams": "nope"}, {"teams": [None, 3, "x"]},
                    {"schedule": [None, {}, {"home": "no"}]}, {"members": 7}):
        parse_teams(rubbish)
        parse_matchups(rubbish, 11)
        parse_members(rubbish)
        parse_game_states(rubbish)
        LeagueSettings.from_raw(rubbish)

    # One object at a time, each missing exactly one thing, because that is how
    # a schema change arrives: everything still parses and one field is gone.
    Player.from_entry(None, 11)
    Player.from_entry({}, 11)
    Player.from_entry({"playerPoolEntry": {"player": {}}}, 11)
    Player.from_entry({"playerPoolEntry": {"player": {
        "id": 1, "fullName": "", "firstName": "", "lastName": "",
        "proTeamId": "not a number", "stats": "not a list",
    }}}, 11)
    Player.from_entry({"playerPoolEntry": {"player": {
        "id": 2, "firstName": "Otis", "lastName": "Danforth",
        "stats": [None, 3, {"scoringPeriodId": 99}],
    }}}, 11)

    Team.from_raw({})
    Team.from_raw({"id": "not a number"})
    Team.from_raw({"id": 7})
    Team.from_raw({"id": 8, "owners": ["{nobody}"]}, members={})
    Side.from_raw(None, 11)
    Side.from_raw({"teamId": "x", "rosterForCurrentScoringPeriod": {"entries": "no"}}, 11)
    # `True` is the one worth naming: bool is an int subclass, so an unguarded
    # coercion turns a flag into a one-point score. And a dict where a number
    # belongs is what a schema change looks like before anyone has read it.
    Side.from_raw({"teamId": 3, "totalPoints": True,
                   "totalProjectedPointsLive": "ninety"}, 11)
    Matchup.from_raw({}, 11)
    Matchup.from_raw({"id": "x", "matchupPeriodId": "y"}, 11)

    # Numbers that are not numbers. `True` is the one worth naming: bool is an
    # int subclass, so an unguarded coercion turns a flag into a one-point score.
    Player.from_entry({"playerId": "abc", "playerPoolEntry": {"player": {
        "fullName": "Rennie Ravensworth",
        "eligibleSlots": [2, "flex", None],
        "stats": [{"scoringPeriodId": 11, "statSplitTypeId": 0, "appliedTotal": True},
                  {"scoringPeriodId": 11, "statSplitTypeId": 1, "appliedTotal": {}}],
    }}}, 11)

    # A scoreboard whose fixtures do not name teams the league knows, a member
    # with no display name, and a lineup-slot block that counts nothing.
    parse_game_states({"events": [None, {}, {"competitions": [{"competitors": []}]}]})
    parse_game_states({"events": [{"competitions": [{"competitors": [
        {"team": {"id": "not a number", "abbreviation": "SF"}},
        {"team": {"id": "also not", "abbreviation": "ZZZ"}},
    ]}]}]})
    # A franchise ESPN renumbered: the id parses, it is nobody we know, and the
    # abbreviation is the only thing that still identifies the team.
    parse_game_states({"events": [{"competitions": [{"competitors": [
        {"team": {"id": 9001, "abbreviation": "SF"}},
        {"team": {"id": 9002, "abbreviation": "PHI"}},
    ]}]}]})
    parse_members({"members": [{"id": "{guid}", "firstName": "Wren", "lastName": "Ashby"},
                               {"id": "{other}"}]})
    LeagueSettings.from_raw({"settings": {"rosterSettings": {"lineupSlotCounts": "no"}}})
    LeagueSettings.from_raw({"settings": {"rosterSettings": {
        "lineupSlotCounts": {"0": 1, "bad": "x"}}}})
    # A slot ESPN invented that is not in the display order and is not one of the
    # non-scoring ones: it still has to end up in the starting lineup.
    LeagueSettings.from_raw({"settings": {"rosterSettings": {
        "lineupSlotCounts": {"0": 1, "88": 1}}}}).starting_slots

    # A side with no live projection of its own, so it has to be reconstructed.
    bare = Side.from_raw({"teamId": 1, "rosterForCurrentScoringPeriod": {"entries": [
        {"lineupSlotId": 0, "playerId": 9, "playerPoolEntry": {"player": {
            "id": 9, "fullName": "Pax Yarrowmead",
            "stats": [{"scoringPeriodId": 11, "statSplitTypeId": 1,
                       "statSourceId": 1, "appliedTotal": 14.0}]}}},
    ]}}, 11)

    # Asking a matchup about somebody who is not in it, and a snapshot about a
    # team with no matchup at all.
    pair = Matchup.from_raw({"id": 1, "matchupPeriodId": 11,
                             "home": {"teamId": 1}, "away": {"teamId": 2}}, 11)
    pair.side_for(99)
    pair.opponent_of(99)

    empty = LeagueSnapshot(season=2025, scoring_period=11,
                           settings=LeagueSettings.from_raw({}))
    empty.matchups = [pair]
    empty.matchup_for(99)
    empty.apply_game_states({})                 # no scoreboard: nothing to join on
    pair.home.players = list(bare.players)
    empty.apply_game_states({99: GameState(pro_team_id=99, abbrev="ZZZ")})


def _drive_the_live_transport() -> None:
    """The code that only ever runs against ESPN.

    Every line here executes exclusively in production, against an undocumented
    endpoint that changes without notice, and a replay reaches none of it -- so
    it was excused wholesale as "the real network" and never looked at again.
    That is backwards: code that only runs where nobody is watching is the code
    most worth driving.

    No socket is opened. The session is built for real, because that is where the
    cookie handling lives, and then the response objects are stubbed.
    """
    import requests

    from espn import feeds
    from espn.client import AuthExpired, LiveTransport, UpstreamError

    # The SWID brace dance: ESPN sets the cookie wrapped in braces and rejects it
    # without them, and a value pasted out of a browser's storage inspector has
    # them stripped about half the time.
    transport = LiveTransport(espn_s2="s2", espn_swid="1234-5678")
    session = transport._get_session()
    assert session.cookies.get("SWID", domain=".espn.com") == "{1234-5678}"
    transport._get_session()            # cached: built once per process

    class Response:
        def __init__(self, status=200, payload=None, raises=None):
            self.status_code = status
            self._payload = payload
            self._raises = raises

        def json(self):
            if self._raises:
                raise self._raises
            return self._payload

    class Session:
        """Stands in for `requests.Session`, one queued response at a time."""

        def __init__(self, outcomes):
            self.outcomes = list(outcomes)

        def get(self, url, params=None, timeout=None, headers=None):
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    outcomes = [
        requests.ConnectionError("name resolution failed"),   # the venue's wifi
        requests.RequestException("something else entirely"),
        Response(401),                                        # the cookies expired
        Response(500),
        Response(200, raises=ValueError("not json")),
        Response(200, payload=[{"id": 1}]),                   # ESPN's list envelope
        Response(200, payload=[]),                            # which, empty, means logged out
        Response(200, payload="a string"),
        Response(200, payload={"id": 1}),
    ]
    transport._session = Session(outcomes)
    for _ in range(len(outcomes)):
        try:
            transport.fetch(feeds.TEAM, 2025, "1", 11)
        except (UpstreamError, AuthExpired):
            pass


def _drive_the_replay_harness() -> None:
    """The recorder, the clock, and a recording that is not the demo one.

    espn/replay.py was excused as "RecordingTransport: writes captures, only used
    with RECORD=1", and two thirds of what that hid is not the recorder: it is
    the clock the demo mode runs on -- paused, resumed, advanced, at a speed
    other than zero -- the uncompressed payload path, the slot fallback for a
    week the recording does not contain, and the error for a feed it has none of.
    The driver seeks a stopped clock all afternoon and reaches none of it.
    """
    import gzip
    import json as _json

    from espn import feeds
    from espn.client import UpstreamError
    from espn.replay import (
        Entry, Recording, RecordingTransport, ReplayTransport, list_recordings,
        resolve_recording_dir, write_recording,
    )

    list_recordings()
    resolve_recording_dir(str(ROOT / "data" / "recordings"))

    # A two-payload recording written out and read back, one gzipped and one
    # not. Real captures are uncompressed; only the committed fixture is gzipped,
    # so the plain path is the one an actual RECORD=1 session produces.
    directory = Path(tempfile.mkdtemp(prefix="punt-deadcode-rec-")) / "tiny"
    tiny = Recording(
        name="tiny", directory=directory, season=2025, league_id="1",
        scoring_period=11, synthetic=True, description="", created="",
        entries=[
            Entry(seq=0, feed="mSettings", offset=0.0, file="0000_mSettings.json"),
            Entry(seq=1, feed="mTeam", offset=10.0, file="0001_mTeam.json.gz"),
            Entry(seq=2, feed="mMatchupScore", offset=10.0,
                  file="0002_mMatchupScore.json", scoring_period=11),
        ],
    )
    write_recording(tiny, {"0000_mSettings.json": {"settings": {}},
                           "0001_mTeam.json.gz": {"teams": []},
                           "0002_mMatchupScore.json": {"schedule": []}})
    reloaded = Recording.load(str(directory))
    reloaded.to_manifest()
    reloaded.slots

    served = ReplayTransport(reloaded, speed=0.0)
    served.fetch(feeds.SETTINGS, 2025, "1", None)
    served.fetch(feeds.TEAM, 2025, "1", None)
    # A week this recording does not contain: fall back to what was captured
    # rather than rendering an empty page, because `scoringPeriodId` drifts.
    served.fetch(feeds.SCOREBOARD, 2025, "1", 99)
    try:
        served.fetch(feeds.ROSTER, 2025, "1", 11)      # a feed it has none of
    except UpstreamError:
        pass
    served.describe()

    # A payload file that is not readable JSON. A half-written capture is a
    # normal outcome of stopping a recording with ctrl-C.
    (directory / "0000_mSettings.json").write_text("{half", encoding="utf-8")
    try:
        ReplayTransport(Recording.load(str(directory)), speed=0.0).fetch(
            feeds.SETTINGS, 2025, "1", None)
    except UpstreamError:
        pass
    # And ESPN's list envelope, which survives into a capture.
    with gzip.GzipFile(directory / "0001_mTeam.json.gz", "wb", mtime=0) as raw:
        raw.write(_json.dumps([{"teams": []}]).encode("utf-8"))
    ReplayTransport(Recording.load(str(directory)), speed=0.0).fetch(
        feeds.TEAM, 2025, "1", None)

    # A recording directory with no manifest in it.
    try:
        Recording.load(str(Path(tempfile.mkdtemp(prefix="punt-deadcode-empty-"))))
    except FileNotFoundError:
        pass

    # The clock the demo mode actually runs on. The driver seeks a stopped one
    # all afternoon, so running, paused, resumed and advanced had never run.
    moving = ReplayTransport(reloaded, speed=60.0)
    moving.clock.position
    moving.clock.advance(5.0)
    moving.clock.pause()
    moving.clock.position
    moving.clock.resume()
    moving.finished, moving.progress

    # The recorder, wrapping something that answers, and then something that
    # cannot be written: a full disk must not take the live poll down with it.
    class Inner:
        def fetch(self, feed, season, league_id, scoring_period=None):
            return {"teams": []}

    capture_dir = Path(tempfile.mkdtemp(prefix="punt-deadcode-capture-"))
    recorder = RecordingTransport(inner=Inner(), directory=capture_dir, name="capture")
    recorder.fetch(feeds.TEAM, 2025, "1", None)
    recorder.fetch(feeds.SCOREBOARD, 2025, "1", 11)
    with quiet("espn.replay"):
        recorder.directory = Path("/dev/null/nowhere")
        recorder.fetch(feeds.TEAM, 2025, "1", None)


def _drive_a_bad_upstream() -> None:
    """ESPN having a bad afternoon, and the app being started without cookies.

    The backoff ladder, the degraded-snapshot reporting and `build_client` are
    all application code that a clean replay never touches, and the file-level
    exclusion for espn/client.py was hiding all three behind "the live network".
    The backoff has had two real bugs in it -- a local wifi drop treated like an
    ESPN outage, and a ceiling that was not one because the jitter was applied
    after the clamp -- and neither was reachable from this tool.
    """
    from config import DEMO_RECORDING, Config
    from espn import feeds
    from espn.cache import TTLCache
    from espn.client import (
        AuthExpired, EspnClient, LeagueRepository, UpstreamError, build_client,
    )
    from espn.replay import ReplayTransport

    # The factory the app actually uses, on all three of its branches. The first
    # is what a fresh clone takes: no LEAGUE_ID, no cookies, falling back to the
    # shipped demo recording, which is the Phase 1 acceptance criterion.
    with quiet("espn.client"):
        build_client(Config())
        build_client(Config(replay=DEMO_RECORDING, replay_speed=0.0))
        # Constructing a LiveTransport opens nothing: the session is lazy, which
        # is the whole reason `requests` is imported inside the method.
        build_client(Config(league_id="1", espn_s2="s2", espn_swid="swid"))

    _drive_the_live_transport()

    class Broken:
        """A transport that fails the way ESPN does."""

        def __init__(self) -> None:
            self.calls = 0

        def fetch(self, feed, season, league_id, scoring_period=None):
            self.calls += 1
            if feed.name == "nfl_scoreboard":
                raise UpstreamError("nfl: HTTP 500", status=500)
            raise UpstreamError("the cable came out", local=True)

    with quiet("espn.client", "espn.cache"):
        broken = EspnClient(transport=Broken(), season=2025, league_id="demo",
                            cache=TTLCache())
        # Every feed fails with a cold cache, so the snapshot comes back as a
        # list of problems rather than as an exception. This is the banner the
        # bar screen shows when ESPN is the thing that is broken.
        LeagueRepository(broken).snapshot()

        # The ladder: a local failure and a remote one have different ceilings,
        # because plugging the cable back in should not cost five minutes.
        for _ in range(4):
            broken._record_failure(local=True)
        for _ in range(4):
            broken._record_failure(local=False)
        assert broken.backing_off
        broken.backoff_remaining
        # And a request made while backing off never reaches the network.
        broken.cache.invalidate()
        broken.get(feeds.TEAM)
        broken.clear_backoff()

        # Expired cookies. The auth state flips once and remembers when, the
        # failure counter moves, and every snapshot from then on carries the
        # reason on the stale banner rather than just going quiet.
        class Expired:
            def fetch(self, feed, season, league_id, scoring_period=None):
                raise AuthExpired("mTeam: ESPN returned an empty list")

        locked = EspnClient(transport=Expired(), season=2025, league_id="demo",
                            cache=TTLCache())
        LeagueRepository(locked).snapshot()
        locked.auth.mark_expired("still expired")   # second time: already known

        # And an upstream that fails in a way nobody anticipated, which must
        # still be recorded as a failure rather than escaping the poll.
        class Weird:
            def fetch(self, feed, season, league_id, scoring_period=None):
                raise ZeroDivisionError("a library did something surprising")

        odd = EspnClient(transport=Weird(), season=2025, league_id="demo", cache=TTLCache())
        odd.get(feeds.TEAM)

        # Then it comes back. A success clears the counter, so the next blip
        # starts the ladder from the bottom rather than from where it left off.
        good = EspnClient(transport=ReplayTransport.load(DEMO_RECORDING, speed=0.0),
                          season=2025, league_id="demo", cache=TTLCache())
        good._record_failure()
        good.get(feeds.SETTINGS)
        good.stats()


def drive_a_sunday() -> None:
    """Everything a real afternoon does, in order."""
    from config import DEMO_RECORDING, PHRASES_DIR
    from engine.commentary import Commentator, PhraseBank
    from engine.events import EventEngine
    from engine.factpack import build
    from engine.live import LiveFeed
    from engine.recap import build_prompt, generate, templated, validate
    from engine.speech import SpeechCache
    from engine.scoring import optimal_lineup, standings
    from engine.simulate import playoff_odds, probabilities_for
    from espn.cache import TTLCache
    from espn.client import EspnClient, LeagueRepository
    from espn.replay import ReplayTransport

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    repo = LeagueRepository(client)
    bank = PhraseBank.load(PHRASES_DIR)
    state_dir = Path(tempfile.mkdtemp(prefix="punt-deadcode-state-"))
    engine = EventEngine(simulate_draws=40, seen_path=state_dir / "seen-moments.json")
    commentator = Commentator(bank, roast_level=2)
    # A real speech cache, with the backend switched off. The shipping path asks
    # for a URL as soon as the server picks the line, which is what hides the
    # synthesis behind the SSE round trip -- and with no backend the honest
    # answer is no audio and a line that goes out anyway. Leaving `speech=None`
    # here skipped the asking entirely, and skipped it on the one machine where
    # macOS `say` would otherwise synthesise two hundred and thirty-six phrases
    # every time somebody ran this tool.
    speech = SpeechCache(directory=Path(tempfile.mkdtemp(prefix="punt-deadcode-")))
    speech.backend.kind = "none"
    feed = LiveFeed(fetch=lambda: (client.cache.invalidate(), repo.snapshot())[1],
                    poll_seconds=30, engine=engine, commentator=commentator,
                    speech=speech)

    # The view models are rendered all afternoon, not only at the end of it, so
    # this keeps eight snapshots spread across the day as well as the settled
    # one. Rendering only at settle reported fifty-three lines of `cheer_view`
    # and `watch_now` as dead when in fact every game had simply finished: no
    # game is in the red zone at midnight, and nothing is "worth looking up for"
    # once it is over. Two points were not enough either -- "LAST MAN", the
    # tensest state in fantasy football, exists for about two hours of a Sunday
    # and neither sample landed in them. The same trap caught the
    # entity-escaping guard test, which passed because its snapshot was at
    # kickoff and the view returned nothing at all.
    snapshot = None
    duration = int(transport.recording.duration)
    marks = [duration * n // 9 for n in range(1, 9)]
    afternoons = []
    for position in range(0, duration + 120, 120):
        transport.clock.seek(position)
        feed.poll_once()
        snapshot = feed.snapshot
        if marks and position >= marks[0]:
            marks.pop(0)
            afternoons.append(snapshot)

    # The surfaces a replay does not reach on its own.
    probabilities_for(snapshot, draws=40)
    playoff_odds(snapshot, draws=40)
    standings(snapshot)
    for matchup in snapshot.matchups:
        for side in (matchup.home, matchup.away):
            optimal_lineup(side.players, snapshot.settings.starting_slots)
    # The view models too: this is where the data reaches the page, and a field
    # nothing renders is dead however many engines compute it. Driving only the
    # engine produced a report full of false positives -- `Team.monogram` and
    # `Side.bench` looked dead and are used on every card.
    from views.viewmodels import (
        album_view, cheer_view, matchup_view, moments_view, multiverse_view,
        receipts_view, swing_view, watch_now,
    )

    for view_of in [snapshot] + afternoons:
        if view_of is None:
            continue
        album_view(view_of, feed)
        matchup_view(view_of)
        receipts_view(view_of)
        swing_view(view_of, feed)
        # Every team, not just the first: "CONFLICTED" needs a fixture where one
        # manager and his opponent both have a starter, which is a property of
        # the pairing rather than of the afternoon.
        for team in view_of.teams:
            cheer_view(view_of, team_id=team.id)
        cheer_view(view_of, team_id=None)
        watch_now(view_of)
    # Once, and at a realistic draw count. Forty draws is enough to prove the
    # plumbing and not enough to produce a magic number: `_magic_number` wants
    # thirty seasons in a bucket before it will commit, so every playoff verdict
    # came back "NEEDS HELP" and three of the five phrases the tab can print had
    # never been printed.
    multiverse_view(snapshot, draws=400)
    # And once while the games are still on. The playoff simulator blends each
    # team's live score into the current week instead of drawing it from the
    # season mean, and that blend is only reachable before the final whistle.
    if afternoons:
        multiverse_view(afternoons[len(afternoons) // 2], draws=400)
    moments_view(feed)
    moments_view(None)          # the tab before the first poll returns

    # A league with no `mSchedule` -- a single-week recording, or an ESPN outage
    # mid-season. The multiverse tab has to say so rather than render zeros.
    # The URL builders. A replay answers by feed name and never constructs a URL,
    # so these are cold for the transport's sake rather than their own.
    from espn.feeds import ALL_FEEDS, params_for, url_for
    for espn_feed in ALL_FEEDS:
        url_for(espn_feed, season=2025, league_id="demo")
        params_for(espn_feed, scoring_period=11)

    without_schedule = copy.copy(snapshot)
    without_schedule.season_schedule = []
    multiverse_view(without_schedule, draws=40)

    # Ten phones on one poll. The driver invalidates before each seek so the
    # replay clock can advance, which means the cache never serves a hit -- and
    # the single-flight double-check under the lock, which is the entire reason
    # the cache exists, had never run here. This is what the bar actually does.
    # Cold first, so all ten miss together and nine of them queue behind the one
    # that fetches. That queue is the single-flight double-check, and it is only
    # reachable under contention.
    client.cache.invalidate()
    threads = [threading.Thread(target=repo.snapshot) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    repo.snapshot()             # and now warm: the fast path, no lock taken
    client.cache.invalidate("nfl_scoreboard")

    client.cache.get_or_set("blip", ttl=0.0, fetch=lambda: "first")
    with quiet("espn.cache"):
        try:
            # ESPN blinks. A Sunday afternoon has one of these in it, and the
            # answer is to serve what we had and say how old it is.
            client.cache.get_or_set("blip", ttl=0.0, fetch=_raise)
        except Exception:
            pass
        try:
            # The same blink against a key that was never filled. Nothing to
            # serve, so it propagates and the caller decides.
            client.cache.get_or_set("never-filled", ttl=30.0, fetch=_raise)
        except Exception:
            pass

    # The SSE fan-out and the background poller. `poll_once` is the seam a test
    # drives; the app runs a thread and pushes to every connected phone, and
    # forty-five lines of that -- start, stop, the listener set, the backlog a
    # phone gets when it unlocks mid-afternoon, the drop rule for a connection
    # that stopped reading -- had never run here at all.
    stream = feed.listen()
    next(stream)                        # the backlog, delivered on connect
    transport.clock.seek(duration)
    feed.poll_once()                    # and now a live broadcast, to a real listener
    stream.close()                      # the finally that discards the listener

    crashed = []

    def _flaky() -> Any:
        # One poll that raises. The loop has to outlive it: a single bad response
        # from ESPN must not end the afternoon for the whole bar.
        if not crashed:
            crashed.append(True)
            raise RuntimeError("ESPN returned nonsense")
        return repo.snapshot()

    feed.fetch = _flaky
    feed.poll_seconds = 1.0
    with quiet("engine.live"):
        feed.start()
        feed.start()                    # already running: the second call is a no-op
        deadline = time.monotonic() + 6.0
        while feed.failures == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        feed.stop()
        if feed._thread is not None:
            feed._thread.join(timeout=3.0)

    # A phone that stopped reading. Blocking the poller on it would stop the
    # whole room's updates for one dead connection, so it is dropped instead.
    from engine.live import LISTENER_BACKLOG, Listener
    deaf = Listener()
    for _ in range(LISTENER_BACKLOG + 2):
        deaf.offer({"event": "moment", "data": {}})

    # A phrase that blows up mid-poll. The line is dropped and the Moment still
    # reaches the stream: a bad phrase must not stop the afternoon.
    def _explode(*_args, **_kwargs):
        raise ValueError("a phrase file said something impossible")

    was = feed.commentator.say
    feed.commentator.say = _explode
    with quiet("engine.live"):
        if feed.moments:
            feed._commentate(feed.moments[-1], week=11)
    feed.commentator.say = was

    # What happens on the next start. The poller writes the dedupe set every time
    # it fires something, so a deploy at four o'clock does not hand the bar the
    # whole afternoon again.
    EventEngine(simulate_draws=40, seen_path=state_dir / "seen-moments.json")
    corrupt = state_dir / "half-written.json"
    corrupt.write_text("{not json", encoding="utf-8")
    with quiet("engine.events"):
        EventEngine(simulate_draws=40, seen_path=corrupt)
        # And a state directory that cannot be written -- a read-only volume, or
        # the wrong owner after a deploy. It must not take the poll down with it.
        unwritable = EventEngine(simulate_draws=40, seen_path=Path("/dev/null/seen.json"))
        unwritable.persist()

    # Startup logs the size of the bank, and the diagnostics page and the linter
    # both ask it what it covers.
    len(bank), bank.kinds, bank.counts()

    # A league that asked for the safe setting. Every line above the configured
    # roast level has to be filtered out, and the driver's own commentator runs
    # at the top level where nothing is.
    polite = Commentator(bank, roast_level=0)
    for moment_ in feed.recent(limit=40):
        polite.eligible(moment_)

    _drive_a_bad_afternoon()
    _drive_a_bad_upstream()
    _drive_the_replay_harness()

    pack = build(snapshot, feed.notable)
    build_prompt(pack)          # what a model would be handed, backend or not
    recap = generate(pack, backend=None)
    validate(recap.text, pack)
    templated(pack)
    feed.factpack(snapshot)
    feed.recent(10)
    feed.stats()
    client.stats()
    snapshot.all_problems()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--module", help="report one file in full")
    parser.add_argument("--show", type=int, default=6, help="cold lines to print per file")
    args = parser.parse_args()

    tracer = trace.Trace(count=1, trace=0, ignoredirs=[sys.prefix, sys.exec_prefix])
    # `sys.settrace` is per-thread, so without this the ten concurrent readers
    # run untraced and the cache's whole reason for existing reports as dead.
    threading.settrace(tracer.globaltrace)
    tracer.runfunc(drive_a_sunday)
    threading.settrace(None)
    executed: dict[str, set[int]] = {}
    for (filename, lineno), _count in tracer.results().counts.items():
        executed.setdefault(filename, set()).add(lineno)

    files = sorted(
        p for directory in WATCHED for p in (ROOT / directory).glob("*.py")
        if p.name != "__init__.py"
    ) + [ROOT / name for name in EXTRA_FILES]
    if args.module:
        files = [ROOT / args.module]

    total_cold = 0
    total_excused = 0
    print(f"{'file':<26}{'lines':>7}{'run':>7}{'cold':>7}{'why':>6}   note")
    for path in files:
        relative = str(path.relative_to(ROOT))
        source = path.read_text("utf-8").splitlines()
        lines = executable_lines(path)
        ran = executed.get(str(path), set()) | executed.get(str(path.resolve()), set())
        excused = {n for n in lines - ran
                   if n <= len(source) and COLD_MARKER in source[n - 1]}
        cold = sorted(lines - ran - excused)
        total_cold += len(cold)
        total_excused += len(excused)
        note = EXPECTED_COLD.get(relative, "")
        print(f"{relative:<26}{len(lines):>7}{len(ran & lines):>7}"
              f"{len(cold):>7}{len(excused) or '':>6}   {note}")
        if cold and not note:
            for lineno in cold[: args.show if not args.module else len(cold)]:
                text = source[lineno - 1].strip()[:76] if lineno <= len(source) else ""
                print(f"      {lineno:>5}  {text}")
            if not args.module and len(cold) > args.show:
                print(f"      ... and {len(cold) - args.show} more")
        if args.module and excused:
            for lineno in sorted(excused):
                reason = source[lineno - 1].split(COLD_MARKER, 1)[1].strip()
                print(f"    ok{lineno:>5}  {reason[:70]}")
    print(f"\n{total_cold} lines never executed during a full replay"
          f"{f', plus {total_excused} documented as unreachable' if total_excused else ''}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
