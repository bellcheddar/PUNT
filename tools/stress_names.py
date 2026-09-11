#!/usr/bin/env python3
"""Serve the demo Sunday with names as long as a real league's.

    python3 tools/stress_names.py &
    python3 tools/screenshot.py --check-overflow
    python3 tools/screenshot.py --out /tmp/longnames

The fixture's managers are Bex, Gus, Sam: median four characters, longest ten.
The first real league PUNT connected to had a median of fourteen and a longest
of sixteen. Every layout decision, every screenshot and every overflow check in
this repository was made against the easy case, and two defects were sitting in
the committed captures the whole time:

* `best 80…` in the bench-regret table -- a number cut in half, which is worse
  than no number, because 80.4 and 809 look identical truncated.
* the swap line reading `Zeke Applewhite (19.3) for Delr…`, losing the benched
  player, his score and the slot: the entire half of the sentence the Receipts
  tab exists to show.

Neither is a long-name bug. Long names are just what made me look.

Deliberately a separate process rather than a flag on the app: nothing in
`app.py` should know that a test harness exists, and a fixture with realistic
names would rewrite the golden timeline and every test that names a manager.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import espn.models as models  # noqa: E402

#: Fifteen characters each, which is what ESPN display names actually look like.
#: Invented, like everything else in the fixture -- no real manager appears here.
LONGER = {
    "Bex": "Bartholomew Q.",
    "Chidi": "Chidinma Okeke",
    "Noor": "Noor Al-Rashidi",
    "Ollie": "Oliver Pemberly",
    "Priya": "Priya Ramanathan",
    "Gus": "Augustus Fairly",
    "Wren": "Wren Castellano",
    "Sam": "Samantha Brooke",
    "Theo": "Theodore Ashcro",
    "Marguerite": "Marguerite Vale",
}

_original = models.Team.from_raw


@classmethod
def _with_longer_names(cls, raw, members=None):
    team = _original.__func__(cls, raw, members)
    # `manager` is a read-only property over `owners`, so lengthen the owner.
    if team.owners:
        team.owners = [LONGER.get(team.owners[0], team.owners[0]), *team.owners[1:]]
    return team


models.Team.from_raw = _with_longer_names

from app import create_app  # noqa: E402


def main() -> int:
    app = create_app()
    print("Demo Sunday with real-length manager names on http://127.0.0.1:8019")
    app.run(host="127.0.0.1", port=8019, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
