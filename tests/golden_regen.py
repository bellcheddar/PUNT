"""Regenerate the Moment-timeline golden file.

    python3 -m tests.golden_regen

Deliberately a separate entry point rather than an environment variable read by
the test: a golden file that regenerates itself when it fails is not a golden
file. Read the diff before committing it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_phase2_acceptance import GOLDEN, replay_timeline  # noqa: E402


def main() -> int:
    timeline = replay_timeline()
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)

    previous = json.loads(GOLDEN.read_text("utf-8")) if GOLDEN.is_file() else []
    GOLDEN.write_text(json.dumps(timeline, indent=1), encoding="utf-8")

    print(f"{GOLDEN}: {len(previous)} -> {len(timeline)} moments")
    from collections import Counter

    for kind, count in Counter(m["kind"] for m in timeline).most_common():
        print(f"  {kind:<16} {count:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
