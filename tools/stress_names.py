#!/usr/bin/env python3
"""Serve the demo Sunday with team names as long as a real league's.

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

#: Longer TEAM names, because those are what the app renders. This harness used
#: to lengthen the MANAGER names, and stopped testing anything the day the
#: manager stopped appearing anywhere for privacy: it went on substituting
#: diligently into a field no template reads.
#:
#: Twenty-two characters and up, which is what a real league looks like: the
#: live one runs to "The Last Great LoHo GM" and "What else can Gano wrong".
#: Invented, like everything else here; no real team appears.
LONGER = {
    "Regret Merchants": "The Regrettable Merchants",
    "The Wounded Ferrets": "The Grievously Wounded Ferrets",
    "Vibes Only FC": "Strictly Vibes Only FC",
    "Statistically Irrelevant": "Statistically Irrelevant United",
    "Bench Mob Rule": "The Bench Mob Rules OK",
    "Sunday Roast": "A Proper Sunday Roast",
    "Certified Bottlers": "Fully Certified Bottlers",
    "Late Swap Larry": "Late Swap Larry and Sons",
    "Panic at the Flex": "Panic! At The Flex Position",
    "Fourth and Forever": "Fourth and Forever Amen",
}

_original = models.Team.from_raw


@classmethod
def _with_longer_names(cls, raw, members=None):
    team = _original.__func__(cls, raw, members)
    team.name = LONGER.get(team.name, team.name)
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
