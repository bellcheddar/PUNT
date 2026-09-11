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
import copy
import sys
import threading
import trace
from pathlib import Path

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
EXPECTED_COLD = {
    "espn/client.py": "LiveTransport: the real network, unreachable in a replay",
    "espn/replay.py": "RecordingTransport: writes captures, only used with RECORD=1",
    "engine/speech.py": "the TTS backends; exercised by tests/test_speech.py",
    "engine/recap.py": "the model backends; neither ollama nor mlx is installed",
}


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
        lines.add(node.lineno)
    return lines


def _raise() -> None:
    raise RuntimeError("ESPN returned 503")


def drive_a_sunday() -> None:
    """Everything a real afternoon does, in order."""
    from config import DEMO_RECORDING, PHRASES_DIR
    from engine.commentary import Commentator, PhraseBank
    from engine.events import EventEngine
    from engine.factpack import build
    from engine.live import LiveFeed
    from engine.recap import generate, templated, validate
    from engine.scoring import optimal_lineup, standings
    from engine.simulate import playoff_odds, probabilities_for
    from espn.cache import TTLCache
    from espn.client import EspnClient, LeagueRepository
    from espn.replay import ReplayTransport

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    repo = LeagueRepository(client)
    bank = PhraseBank.load(PHRASES_DIR)
    engine = EventEngine(simulate_draws=40)
    commentator = Commentator(bank, roast_level=2)
    feed = LiveFeed(fetch=lambda: (client.cache.invalidate(), repo.snapshot())[1],
                    poll_seconds=30, engine=engine, commentator=commentator)
    feed._broadcast = lambda payload: None

    # The view models are rendered all afternoon, not only at the end of it, so
    # this keeps a mid-afternoon snapshot as well as the settled one. Rendering
    # only at settle reported fifty-three lines of `cheer_view` and `watch_now`
    # as dead when in fact every game had simply finished: no game is in the red
    # zone at midnight, and nothing is "worth looking up for" once it is over.
    # The same trap caught the entity-escaping guard test, which passed because
    # its snapshot was at kickoff and the view returned nothing at all.
    snapshot = None
    afternoon = None
    duration = int(transport.recording.duration)
    for position in range(0, duration + 120, 120):
        transport.clock.seek(position)
        feed.poll_once()
        snapshot = feed.snapshot
        if afternoon is None and position >= duration // 2:
            afternoon = snapshot

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

    for view_of in (snapshot, afternoon):
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
        multiverse_view(view_of, draws=40)
        watch_now(view_of)
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
    try:
        # ESPN blinks. A Sunday afternoon has one of these in it, and the answer
        # is to serve what we had and say how old it is.
        client.cache.get_or_set("blip", ttl=0.0, fetch=_raise)
    except Exception:
        pass
    try:
        # The same blink against a key that was never filled. Nothing to serve,
        # so it propagates and the caller decides.
        client.cache.get_or_set("never-filled", ttl=30.0, fetch=_raise)
    except Exception:
        pass

    pack = build(snapshot, feed.notable)
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
    print(f"{'file':<26}{'lines':>7}{'run':>7}{'cold':>7}   note")
    for path in files:
        relative = str(path.relative_to(ROOT))
        lines = executable_lines(path)
        ran = executed.get(str(path), set()) | executed.get(str(path.resolve()), set())
        cold = sorted(lines - ran)
        total_cold += len(cold)
        note = EXPECTED_COLD.get(relative, "")
        print(f"{relative:<26}{len(lines):>7}{len(ran & lines):>7}{len(cold):>7}   {note}")
        if cold and not note:
            source = path.read_text("utf-8").splitlines()
            for lineno in cold[: args.show if not args.module else len(cold)]:
                text = source[lineno - 1].strip()[:76] if lineno <= len(source) else ""
                print(f"      {lineno:>5}  {text}")
            if not args.module and len(cold) > args.show:
                print(f"      ... and {len(cold) - args.show} more")
    print(f"\n{total_cold} lines never executed during a full replay.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
