"""Where the sampled sounds come from, and the proof that they may be here.

The original rule was that every sound is synthesised, so nothing had a licence
to track and the credits file could not drift. That bought real safety and cost
the audio: the horns carried under 8% of their energy above 2 kHz. Sampled sound
is allowed now, and the safety is kept a different way -- the credits are
generated from the same manifest the builder reads, and these tests are what stop
the manifest itself becoming a place where anything is allowed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "audio_sources.json"


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


def test_every_sampled_sound_is_cc0(spec):
    """CC0 only, and not as a matter of taste.

    This repository is public and MIT. An asset with an attribution requirement
    puts an obligation on everyone who clones it -- one they will not know they
    have -- and one with a non-commercial clause makes the whole repository
    undistributable. CC0 is the only licence with neither.
    """
    assert spec["downloads"], "no sources declared"
    for key, entry in spec["downloads"].items():
        assert entry["licence"] == "CC0 1.0", (
            f"{key} is {entry['licence']}. Only CC0 may be committed here; see the "
            f"_licences note in data/audio_sources.json"
        )
        assert entry["licence_url"].startswith("https://creativecommons.org/publicdomain/zero/")
        assert entry["author"] and entry["page"], f"{key} has no attributable origin"


def test_every_source_is_pinned_to_exact_bytes(spec):
    """A checksum, so a silently re-cut upstream file fails the build rather than
    changing what the bar hears."""
    for key, entry in spec["downloads"].items():
        assert len(entry["sha256"]) == 64, f"{key} has no usable sha256"
        assert int(entry["bytes"]) > 0


def test_every_sampled_sound_names_a_source_that_exists(spec):
    for name, sound in spec["sounds"].items():
        assert sound["from"] in spec["downloads"], f"{name} points at a source that is not declared"
        assert sound.get("why"), f"{name} does not say why this sound rather than another"

        # Two shapes of source. A Kenney pack is a zip and the sound has to say
        # which file inside it; a Freesound entry is one file per sound and has
        # nothing to name. Requiring `member` of both was what made this test
        # fail the moment the first Freesound sound arrived.
        download = spec["downloads"][sound["from"]]
        if download["url"].endswith(".zip"):
            assert sound.get("member"), f"{name} comes from an archive but says which file"
        else:
            assert "member" not in sound, f"{name} names a member but its source is a single file"


def test_the_credits_cannot_drift_from_the_manifest():
    """The whole reason sampled audio is allowed at all.

    `LICENCES.md` is generated from the manifest the builder reads, so a sound
    cannot reach the sprite without a licence line. If this fails, run
    `python3 tools/make_licences.py`.
    """
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_licences.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_licence_file_names_every_sound_in_the_sprite():
    sprite = json.loads((ROOT / "static" / "audio" / "sprite.json").read_text())["sprite"]
    credits = (ROOT / "static" / "audio" / "LICENCES.md").read_text("utf-8")
    for name in sprite:
        assert f"`{name}`" in credits, f"{name} is in the sprite with no line in LICENCES.md"
