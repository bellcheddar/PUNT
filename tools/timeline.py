#!/usr/bin/env python3
"""Replay a Sunday through the event engine and print the Moment timeline.

The Phase 2 acceptance criterion is "replaying a recorded Sunday emits a
plausible Moment timeline". Plausible is a judgement, so this exists to make the
judgement possible: the whole day, every Moment, in the order it fired.

    python3 tools/timeline.py                      # the shipped demo Sunday
    python3 tools/timeline.py --kinds DOOM,BENCH_DISASTER
    python3 tools/timeline.py --poll 30 --draws 2000
    python3 tools/timeline.py --summary            # counts only
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEMO_RECORDING  # noqa: E402
from engine.events import EventEngine  # noqa: E402
from espn.cache import TTLCache  # noqa: E402
from espn.client import EspnClient, LeagueRepository  # noqa: E402
from espn.replay import ReplayTransport  # noqa: E402

KIND_COLOUR = {
    "TOUCHDOWN": "\033[93m", "BIG_PLAY": "\033[96m", "LEAD_CHANGE": "\033[95m",
    "MILESTONE": "\033[94m", "BENCH_DISASTER": "\033[91m", "INJURY": "\033[90m",
    "DOOM": "\033[31m", "CLINCH": "\033[92m", "GOOSE_EGG": "\033[90m",
}
RESET = "\033[0m"
FIRST_KICKOFF_OFFSET = 900


def clock(position: float) -> str:
    total = int(position) - FIRST_KICKOFF_OFFSET
    return f"{13 + total // 3600:02d}:{(total % 3600) // 60:02d}"


def describe(moment) -> str:
    context = moment.context
    if moment.kind == "BENCH_DISASTER":
        return (f"{context['benched']} ({context['benched_points']}) on the bench, "
                f"{context['started']} ({context['started_points']}) in the {context['slot']}")
    if moment.kind == "DOOM":
        return f"{context['win_prob'] * 100:.1f}% with {context['deficit']:.1f} to find"
    if moment.kind == "CLINCH":
        return f"{context['lead']:.1f} up, {context['win_prob'] * 100:.0f}% safe"
    if moment.kind == "LEAD_CHANGE":
        return f"in front by {context['margin']:.1f}"
    if moment.kind == "MILESTONE":
        return f"through {context['threshold']:.0f}"
    if moment.kind == "GOOSE_EGG":
        return f"finished on nothing, projected {context['projected']:.1f}"
    if moment.kind == "INJURY":
        return f"{context['status'].lower().replace('_', ' ')}"
    return f"+{moment.delta_points:.1f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recording", default=DEMO_RECORDING)
    parser.add_argument("--poll", type=int, default=60, help="seconds of game time per poll")
    parser.add_argument("--draws", type=int, default=800, help="Monte Carlo draws per matchup")
    parser.add_argument("--kinds", default="", help="comma-separated kinds to show")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    wanted = {k.strip().upper() for k in args.kinds.split(",") if k.strip()}

    transport = ReplayTransport.load(args.recording, speed=0.0)
    client = EspnClient(transport=transport, season=0, league_id="replay", cache=TTLCache())
    repo = LeagueRepository(client)
    engine = EventEngine(simulate_draws=args.draws)

    counts: Counter[str] = Counter()
    total = 0
    for position in range(0, int(transport.recording.duration) + args.poll, args.poll):
        transport.clock.seek(position)
        client.cache.invalidate()
        snapshot = repo.snapshot()

        for moment in engine.ingest(snapshot):
            counts[moment.kind] += 1
            total += 1
            if args.summary or (wanted and moment.kind not in wanted):
                continue
            colour = KIND_COLOUR.get(moment.kind, "")
            who = ", ".join(moment.teams) or "-"
            subject = moment.player or who
            print(
                f"{clock(position)}  {colour}{moment.kind:<15}{RESET}"
                f"{moment.magnitude:4.2f}  {who:<12} {subject:<22} {describe(moment)}"
            )

    print(f"\n{total} moments across {transport.recording.duration / 3600:.1f}h")
    for kind, count in counts.most_common():
        print(f"  {kind:<16} {count:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
