#!/usr/bin/env python3
"""Replay a Sunday and print the commentary it would have produced.

The 400-line target in the build spec is a guess at how much a bank needs to
survive four hours. This measures it instead: how many lines were said, how many
distinct ones, and how often the same line came round twice.

    python3 tools/transcript.py                 # the whole afternoon
    python3 tools/transcript.py --stats         # just the numbers
    python3 tools/transcript.py --roast 2       # savage
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEMO_RECORDING, PHRASES_DIR  # noqa: E402
from engine.commentary import Commentator, PhraseBank  # noqa: E402
from engine.events import EventEngine  # noqa: E402
from espn.cache import TTLCache  # noqa: E402
from espn.client import EspnClient, LeagueRepository  # noqa: E402
from espn.replay import ReplayTransport  # noqa: E402

FIRST_KICKOFF_OFFSET = 900
DIM, RESET = "\033[2m", "\033[0m"


def clock(position: float) -> str:
    total = int(position) - FIRST_KICKOFF_OFFSET
    return f"{13 + total // 3600:02d}:{(total % 3600) // 60:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recording", default=DEMO_RECORDING)
    parser.add_argument("--poll", type=int, default=60)
    parser.add_argument("--draws", type=int, default=600)
    parser.add_argument("--roast", type=int, default=1, choices=[0, 1, 2])
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    bank = PhraseBank.load(PHRASES_DIR)
    transport = ReplayTransport.load(args.recording, speed=0.0)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    repo = LeagueRepository(client)
    engine = EventEngine(simulate_draws=args.draws)
    commentator = Commentator(bank, roast_level=args.roast)

    used: Counter[str] = Counter()
    #: (phrase id) -> the moment index it was last said at, for measuring how
    #: close together a repeat actually landed.
    last_at: dict[str, int] = {}
    gaps: list[tuple[int, str]] = []
    moments_seen = 0
    week = 0

    for position in range(0, int(transport.recording.duration) + args.poll, args.poll):
        transport.clock.seek(position)
        client.cache.invalidate()
        snapshot = repo.snapshot()
        week = snapshot.scoring_period

        for moment in engine.ingest(snapshot):
            moments_seen += 1
            line = commentator.say(moment, week=week)
            if line is None:
                continue
            if line.phrase_id in last_at:
                gaps.append((moments_seen - last_at[line.phrase_id], line.phrase_id))
            last_at[line.phrase_id] = moments_seen
            used[line.phrase_id] += 1
            if not args.stats:
                print(f"{clock(position)}  {DIM}{line.kind:<15}{RESET}{line.text}")

    print(f"\n{moments_seen} moments, {commentator.said} lines said, "
          f"{len(used)} distinct of {len(bank)} in the bank")
    if commentator.missed:
        print(f"  silent on: {dict(sorted(commentator.missed.items()))}")

    print(f"\n  distinct-line coverage      {len(used) / len(bank) * 100:.0f}% of the bank was used")
    if commentator.said:
        print(f"  repeat rate                 {(commentator.said - len(used)) / commentator.said * 100:.0f}% of lines were a repeat")
    if gaps:
        gaps.sort()
        tight = [g for g in gaps if g[0] <= 10]
        print(f"  closest repeat              {gaps[0][0]} moments apart ({gaps[0][1]})")
        print(f"  repeats within 10 moments   {len(tight)}")
    print("\n  most used:")
    for phrase_id, count in used.most_common(6):
        print(f"    {count:>3}x  {phrase_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
