#!/usr/bin/env python3
"""Validate the phrase bank and report where it is thin.

Three things go wrong with a bank of hand-written lines and none of them are
visible by reading it:

* A line references a slot the Moment kind never provides, so it is silently
  never selected. This is the commonest error and the hardest to notice, because
  the app just says something else.
* A category has too few lines for how often its Moment fires, so it repeats
  inside one afternoon. That is the failure the spec's 400-line minimum exists
  to prevent.
* A trigger keys on a field that does not exist, which makes the line dead.

    python3 tools/phrase_lint.py
    python3 tools/phrase_lint.py --strict     # exit 1 below the launch target
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import PHRASES_DIR  # noqa: E402
from engine import events  # noqa: E402
from engine.commentary import PhraseBank, PhraseError  # noqa: E402

#: The spec's launch minimum. Below this the repetition becomes obvious inside
#: one afternoon.
TARGET_TOTAL = 400

#: Slots each Moment kind actually provides, taken from what `engine/events.py`
#: puts in `Moment.context` plus the four every Moment has. A line asking for
#: anything outside its kind's set can never be chosen.
UNIVERSAL = {"player", "manager", "opponent", "delta", "kind", "week"}
PLAY = UNIVERSAL | {"slot", "position", "pro_team", "pro_opponent", "total",
                    "starter", "quarter", "clock", "red_zone", "down_distance"}

SLOTS: dict[str, set[str]] = {
    events.TOUCHDOWN: PLAY,
    events.BIG_PLAY: PLAY,
    events.MILESTONE: PLAY | {"threshold", "team"},
    events.GOOSE_EGG: PLAY | {"projected"},
    events.INJURY: PLAY | {"status"},
    events.BENCH_DISASTER: UNIVERSAL | {"slot", "started", "started_points",
                                        "benched", "benched_points", "regret"},
    events.DOOM: UNIVERSAL | {"win_prob", "win_pct", "deficit", "opponent_in_play"},
    events.CLINCH: UNIVERSAL | {"win_prob", "win_pct", "lead"},
    events.LEAD_CHANGE: UNIVERSAL | {"matchup", "margin", "home", "away"},
    "FILLER": {"week"},
}

#: How many lines each category wants, scaled to how often it fires across a
#: recorded Sunday. Touchdowns fire ~74 times and doom four, so they do not need
#: the same depth.
WANTED = {
    events.TOUCHDOWN: 90, events.BIG_PLAY: 70, events.LEAD_CHANGE: 40,
    events.MILESTONE: 35, events.BENCH_DISASTER: 55, events.DOOM: 35,
    events.CLINCH: 25, events.GOOSE_EGG: 25, events.INJURY: 15, "FILLER": 50,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--strict", action="store_true", help="fail below the launch target")
    args = parser.parse_args()

    try:
        bank = PhraseBank.load(PHRASES_DIR)
    except PhraseError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    problems: list[str] = []
    for phrase in bank.phrases:
        for kind in phrase.kind:
            known = SLOTS.get(kind)
            if known is None:
                problems.append(f"{phrase.id}: unknown Moment kind {kind!r}")
                continue
            unfillable = phrase.slots - known
            if unfillable:
                problems.append(
                    f"{phrase.id} ({phrase.source}): {kind} never provides "
                    f"{sorted(unfillable)}, so this line can never be chosen"
                )
            # A trigger on a context field the kind does not carry is dead too.
            dead = {k for k in phrase.trigger
                    if k not in known and k not in ("magnitude", "delta_points", "win_prob_delta")}
            if dead:
                problems.append(f"{phrase.id} ({phrase.source}): trigger keys {sorted(dead)} never match on {kind}")
        if phrase.roast_level not in (0, 1, 2):
            problems.append(f"{phrase.id}: roast_level {phrase.roast_level} is not 0, 1 or 2")
        if phrase.voice not in ("pbp", "colour"):
            problems.append(f"{phrase.id}: unknown voice {phrase.voice!r}")

    counts = bank.counts()
    width = max(len(k) for k in WANTED)
    print(f"{len(bank)} phrases, target {TARGET_TOTAL}\n")
    total_wanted = 0
    for kind, wanted in sorted(WANTED.items()):
        have = counts.get(kind, 0)
        total_wanted += wanted
        bar = "#" * min(40, round(40 * have / wanted))
        flag = "" if have >= wanted else f"  short by {wanted - have}"
        print(f"  {kind:<{width}} {have:>4}/{wanted:<4} {bar}{flag}")

    # A roast level the commissioner can actually turn down: if every line in a
    # category is level 2, setting ROAST_LEVEL=0 makes that category silent.
    print()
    for kind in sorted(counts):
        safe = sum(1 for p in bank.for_kind(kind) if p.roast_level == 0)
        if not safe:
            problems.append(f"{kind}: no lines at roast_level 0, so ROAST_LEVEL=0 silences it entirely")

    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    print("No unfillable slots, no dead triggers, every category has a safe line.")
    if len(bank) < TARGET_TOTAL:
        message = (f"Below the launch target: {len(bank)}/{TARGET_TOTAL}. "
                   f"{TARGET_TOTAL - len(bank)} more lines needed before this is heard "
                   f"in a bar for four hours.")
        print(f"\n{message}")
        return 1 if args.strict else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
