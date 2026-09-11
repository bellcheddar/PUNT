#!/usr/bin/env python3
"""Build a week's fact pack and write the recap.

    python3 tools/recap.py                 # the recap
    python3 tools/recap.py --facts         # the fact pack it was built from
    python3 tools/recap.py --backend ollama --model qwen2.5:1.5b-instruct

With no model installed this prints the templated recap, which is correct by
construction: it is assembled from the fact pack rather than written about it, so
it cannot fail its own validator.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEMO_RECORDING  # noqa: E402
from engine.events import EventEngine  # noqa: E402
from engine.factpack import build  # noqa: E402
from engine.recap import MLXBackend, OllamaBackend, generate, validate  # noqa: E402
from espn.cache import TTLCache  # noqa: E402
from espn.client import EspnClient, LeagueRepository  # noqa: E402
from espn.replay import ReplayTransport  # noqa: E402

DIM, RESET = "\033[2m", "\033[0m"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--recording", default=DEMO_RECORDING)
    parser.add_argument("--backend", choices=["none", "ollama", "mlx"], default="none")
    parser.add_argument("--model", default="")
    parser.add_argument("--facts", action="store_true", help="print the fact pack instead")
    parser.add_argument("--attempts", type=int, default=3)
    args = parser.parse_args()

    transport = ReplayTransport.load(args.recording, speed=0.0)
    client = EspnClient(transport, 2025, "demo", TTLCache())
    repo = LeagueRepository(client)
    engine = EventEngine(simulate_draws=200)

    moments = []
    snapshot = None
    for position in range(0, int(transport.recording.duration) + 60, 60):
        transport.clock.seek(position)
        client.cache.invalidate()
        snapshot = repo.snapshot()
        moments.extend(engine.ingest(snapshot))

    pack = build(snapshot, moments)
    if args.facts:
        print(pack.dumps())
        return 0

    backend = None
    if args.backend == "ollama":
        backend = OllamaBackend(**({"model": args.model} if args.model else {}))
    elif args.backend == "mlx":
        backend = MLXBackend(**({"model": args.model} if args.model else {}))
    if backend is not None and not backend.available():
        print(f"{DIM}{backend.name} is not available; using the templated recap.{RESET}\n",
              file=sys.stderr)
        backend = None

    recap = generate(pack, backend=backend, attempts=args.attempts)

    print(f"{DIM}week {pack.week} of {pack.league} "
          f"({len(pack.numbers())} sayable numbers, {len(pack.names())} sayable names){RESET}\n")
    print(textwrap.fill(recap.text, 84))
    print(f"\n{DIM}source: {recap.source}, attempts: {recap.attempts}, "
          f"validates: {not validate(recap.text, pack)}{RESET}")
    if recap.rejections:
        print(f"{DIM}{len(recap.rejections)} rejection(s):{RESET}")
        for rejection in recap.rejections[:8]:
            print(f"  {rejection}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
