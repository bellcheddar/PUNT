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
import sys
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

    snapshot = None
    for position in range(0, int(transport.recording.duration) + 120, 120):
        transport.clock.seek(position)
        feed.poll_once()
        snapshot = feed.snapshot

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

    album_view(snapshot)
    matchup_view(snapshot)
    receipts_view(snapshot)
    swing_view(snapshot, feed)
    cheer_view(snapshot, team_id=snapshot.teams[0].id if snapshot.teams else None)
    cheer_view(snapshot, team_id=None)
    multiverse_view(snapshot, draws=40)
    watch_now(snapshot)
    moments_view(feed)

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
    tracer.runfunc(drive_a_sunday)
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
