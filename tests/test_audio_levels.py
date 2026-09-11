"""Gain structure.

Web Audio hard-clips at the destination, so the bus levels have to be checked as
arithmetic rather than trusted as taste. Read straight out of the JavaScript,
because that is the only place they exist and a copy in a test would drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

AUDIO_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "audio.js"


def buses() -> dict[str, dict[str, float]]:
    source = AUDIO_JS.read_text("utf-8")
    block = re.search(r"const BUSES = \{(.*?)\n  \};", source, re.S)
    assert block, "could not find the BUSES table in audio.js"
    out: dict[str, dict[str, float]] = {}
    for name, volume, ducked in re.findall(
        r"(\w+):\s*\{\s*volume:\s*([\d.]+),\s*ducked:\s*([\d.]+)\s*\}", block.group(1)
    ):
        out[name] = {"volume": float(volume), "ducked": float(ducked)}
    return out


def test_every_bus_from_the_spec_exists():
    assert set(buses()) == {"music", "stings", "commentary", "ui"}


def test_the_worst_simultaneous_case_does_not_clip():
    """Everything at once: the bed, a sting, a tap, and a play call over the top.

    They genuinely do coincide. The play call is started 260 ms into a 600 ms
    horn on purpose, so the sting is still ringing, and somebody tapping a tab
    while that happens is a Sunday afternoon. The spec's nominal levels
    (0.35 + 0.80 + 1.00) sum to 1.42 even with music fully ducked, which clips.
    """
    b = buses()
    worst = (b["music"]["ducked"] + b["stings"]["ducked"] + b["ui"]["ducked"]
             + b["commentary"]["volume"])
    assert worst <= 1.0 + 1e-9, f"worst case sums to {worst:.2f}, which clips"


def test_a_sting_over_the_bed_also_fits():
    """The commoner case: a horn with no line behind it."""
    b = buses()
    assert b["music"]["ducked"] + b["stings"]["volume"] <= 1.0 + 1e-9


def test_the_ordering_the_spec_asks_for_is_preserved():
    """Commentary over stings over music, and ducking never raises a bus."""
    b = buses()
    assert b["commentary"]["volume"] > b["stings"]["volume"] > b["music"]["volume"]
    for name, levels in b.items():
        assert levels["ducked"] <= levels["volume"], f"{name} gets louder when ducked"


def test_commentary_ducks_under_nothing():
    b = buses()
    assert b["commentary"]["ducked"] == b["commentary"]["volume"]
