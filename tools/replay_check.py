#!/usr/bin/env python3
"""Replay a recorded Sunday in the terminal.

This is the Phase 1 acceptance criterion made watchable: a full recorded game day
at any speed, with no network and no cookies, driven through exactly the same
client, cache and parser the web app uses.

    python3 tools/replay_check.py                    # the shipped demo, fast
    python3 tools/replay_check.py --speed 3600       # ten hours in ten seconds
    python3 tools/replay_check.py --recording 2025-11-16-134500 --speed 60
    python3 tools/replay_check.py --list

It is also the quickest way to tell whether a change to the parsers or the event
model did something stupid, because you can see the whole day go past.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEMO_RECORDING  # noqa: E402
from espn.cache import TTLCache  # noqa: E402
from espn.client import EspnClient, LeagueRepository  # noqa: E402
from espn.replay import ReplayTransport, list_recordings  # noqa: E402

BAR = "█"
DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


#: The recording opens fifteen minutes before the first kickoff, so position 0
#: is 12:45 ET rather than 13:00.
FIRST_KICKOFF_OFFSET = 900


def clock(position: float) -> str:
    """Replay position as an Eastern kickoff clock, which is how the day reads."""
    total = int(position) - FIRST_KICKOFF_OFFSET
    return f"{13 + total // 3600:02d}:{(total % 3600) // 60:02d} ET"


def render(snap, position: float, duration: float, width: int) -> str:
    lines = [
        f"{BOLD}{snap.settings.name}{RESET}  week {snap.scoring_period}   "
        f"{clock(position)}   {DIM}{position / 3600:.1f}h / {duration / 3600:.1f}h{RESET}"
    ]
    teams = snap.teams_by_id
    top = max((s.total for m in snap.matchups for s in (m.home, m.away)), default=1.0) or 1.0
    bar_width = max(8, min(28, width - 52))

    for matchup in sorted(snap.matchups, key=lambda m: m.id):
        for side in (matchup.away, matchup.home):
            team = teams.get(side.team_id)
            manager = (team.manager if team else f"team {side.team_id}")[:11]
            name = (team.name if team else "")[:22]
            opponent = matchup.opponent_of(side.team_id)
            leading = opponent is not None and side.total > opponent.total
            colour = GREEN if leading else ""
            filled = int(bar_width * side.total / top)
            bar = BAR * filled + DIM + "─" * (bar_width - filled) + RESET
            yet = sum(1 for p in side.starters if p.remaining > 0)
            lines.append(
                f"  {manager:<11} {name:<22} {colour}{side.total:7.2f}{RESET} {bar} "
                f"{DIM}proj {side.live_projection:6.1f} · {yet} left{RESET}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recording", default=DEMO_RECORDING)
    parser.add_argument("--speed", type=float, default=1800.0, help="game seconds per real second")
    parser.add_argument("--fps", type=float, default=6.0)
    parser.add_argument("--list", action="store_true", help="list available recordings and exit")
    parser.add_argument("--once", action="store_true", help="print the final state and exit")
    args = parser.parse_args()

    if args.list:
        names = list_recordings()
        print("\n".join(names) if names else "no recordings found under data/recordings/")
        return 0

    transport = ReplayTransport.load(args.recording, speed=args.speed)
    client = EspnClient(transport=transport, season=0, league_id="replay", cache=TTLCache())
    repo = LeagueRepository(client)
    duration = transport.recording.duration

    if transport.recording.synthetic:
        print(f"{YELLOW}Synthetic recording: every name and score below is invented.{RESET}")
    print(f"{DIM}{transport.recording.description}{RESET}\n")

    if args.once:
        transport.clock.seek(duration)
        client.cache.invalidate()
        print(render(repo.snapshot(), duration, duration, shutil.get_terminal_size().columns))
        return 0

    started = time.monotonic()
    try:
        while not transport.finished:
            # The cache is dropped every frame because this tool is a poller
            # standing in for the app's own 30 s cycle, and the interesting thing
            # to watch is the upstream state changing, not the cache working.
            client.cache.invalidate()
            snap = repo.snapshot()
            frame = render(snap, transport.position, duration, shutil.get_terminal_size().columns)
            sys.stdout.write("\033[H\033[J" + frame)
            sys.stdout.flush()
            time.sleep(1.0 / max(1.0, args.fps))
    except KeyboardInterrupt:
        print()
        return 130

    client.cache.invalidate()
    transport.clock.seek(duration)
    sys.stdout.write("\033[H\033[J" + render(repo.snapshot(), duration, duration, shutil.get_terminal_size().columns))
    elapsed = time.monotonic() - started
    print(
        f"{DIM}Replayed {duration / 3600:.1f}h of game time in {elapsed:.1f}s "
        f"({duration / max(elapsed, 0.001):.0f}x), {transport.reads} payload reads, "
        f"no network.{RESET}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
