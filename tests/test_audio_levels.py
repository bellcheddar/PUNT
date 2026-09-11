"""Gain structure.

Web Audio hard-clips at the destination, so the bus levels have to be checked as
arithmetic rather than trusted as taste. Read straight out of the JavaScript,
because that is the only place they exist and a copy in a test would drift.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AUDIO_JS = ROOT / "static" / "js" / "audio.js"


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


# --------------------------------------------------------------------------
# the sprite itself, not just the mixer
# --------------------------------------------------------------------------

def _decode_sprite():
    """The shipped mp3 as mono samples, or a skip if ffmpeg is not here."""
    import shutil
    import subprocess
    import tempfile
    import wave

    import numpy as np

    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if not Path(ffmpeg).exists():
        pytest.skip("ffmpeg not installed; cannot decode the sprite")
    mp3 = ROOT / "static" / "audio" / "sprite.mp3"
    with tempfile.TemporaryDirectory() as work:
        out = Path(work) / "sprite.wav"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(mp3),
                        "-ac", "1", "-ar", "44100", str(out)], check=True)
        with wave.open(str(out)) as handle:
            raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(float) / 32768


def test_no_sting_is_cut_off_mid_signal():
    """The window a phone actually plays has to end on silence.

    `fade()` ramps the last 6 ms to zero, and then sprite.json ends each window
    8 ms EARLY so a slow seek cannot run into the next sound. Eight is more than
    six, so playback stopped two milliseconds before the fade began and cut every
    sustained sting off at full amplitude: `riser` ended at 0.23 of full scale,
    which is a step function into the speaker. The pop was not in the sound, it
    was at the edge of the window -- the safety trim was defeating the fade it
    existed to protect.
    """
    import json

    import numpy as np

    audio = _decode_sprite()
    sprite = json.loads((ROOT / "static" / "audio" / "sprite.json").read_text())["sprite"]
    rate = 44_100
    worst = {}
    for name, (start_ms, length_ms) in sprite.items():
        seg = audio[int(start_ms * rate / 1000): int((start_ms + length_ms) * rate / 1000)]
        assert len(seg) > 64, f"{name} is empty in the sprite"
        worst[name] = float(max(abs(seg[0]), abs(seg[-1])))
    loudest = max(worst, key=worst.get)
    assert worst[loudest] < 0.02, (
        f"{loudest} starts or ends at {worst[loudest]:.3f} of full scale, which clicks. "
        f"TAIL in tools/make_audio.py must stay longer than the 8 ms the window trims."
    )


def test_the_silence_appended_outlasts_the_window_trim():
    """The invariant behind the test above, stated where it can be read."""
    source = (ROOT / "tools" / "make_audio.py").read_text("utf-8")
    tail_ms = float(re.search(r"^TAIL = ([\d.]+)", source, re.M).group(1)) * 1000
    trim_ms = float(re.search(r"round\(duration \* 1000\) - (\d+)", source).group(1))
    assert tail_ms > trim_ms, (
        f"{tail_ms:.0f} ms of tail against a {trim_ms:.0f} ms window trim: "
        f"the window ends before the fade does and every sustained sting clicks"
    )
