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


ALLOWED = {"CC0 1.0", "CC-BY 4.0"}


def _is_music(spec: dict, download_key: str) -> bool:
    """A source is music if the only sounds using it are the bed_* entries."""
    users = [name for name, s in spec["sounds"].items() if s["from"] == download_key]
    return bool(users) and all(name.startswith("bed_") for name in users)


def test_sound_effects_are_cc0_and_music_may_be_cc_by(spec):
    """Two rules, because one rule could not produce the app Marc asked for.

    Effects are CC0 only. This repository is public and MIT, so an asset with an
    attribution requirement puts an obligation on everyone who clones it -- one
    they will not know they have -- and a non-commercial clause would make the
    whole thing undistributable.

    Music is allowed CC-BY. The sports-broadcast idiom simply does not exist
    under CC0: it gives you epic percussion, taiko and marching snare, and a CC0
    search for "rock anthem" returns nothing at all. Attribution is acceptable
    there precisely because LICENCES.md is generated from this manifest and
    `make_licences.py --check` fails the build when they disagree -- the
    obligation travels with the repository by construction.
    """
    assert spec["downloads"], "no sources declared"
    for key, entry in spec["downloads"].items():
        assert entry["licence"] in ALLOWED, f"{key} is {entry['licence']}, which is neither"
        assert entry["author"] and entry["page"], f"{key} has no attributable origin"

        if entry["licence"] == "CC0 1.0":
            assert "publicdomain/zero" in entry["licence_url"]
            continue

        assert _is_music(spec, key), (
            f"{key} is CC-BY and is used for a sound effect. Only the bed_* music "
            f"entries may carry an attribution requirement; see _licences in "
            f"data/audio_sources.json"
        )
        assert "licenses/by/" in entry["licence_url"], entry["licence_url"]


def test_the_forbidden_tunes_are_written_down(spec):
    """The one rule that is not about licences at all, kept where it is read."""
    note = spec.get("_forbidden", "")
    assert "copyrighted compositions" in note
    assert "idiom" in note.lower(), "the distinction between the idiom and the tune is the point"


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
