#!/usr/bin/env python3
"""Fetch the sampled sounds named in data/audio_sources.json, and measure them.

    python3 tools/fetch_audio.py            # download, verify, decode, measure
    python3 tools/fetch_audio.py --check    # measure what is cached, fetch nothing

Downloads land in data/audio_cache/ (gitignored) and are decoded to 44.1 kHz wav
next to them. The sprite is built from those by tools/make_audio.py.

Two things this does that a plain download script does not.

**It checks the bytes.** Every entry carries a sha256, so a silently re-cut
upstream file fails here rather than changing what the bar hears. Kenney's URLs
carry a content hash in the path and are stable, but that is a courtesy, not a
guarantee.

**It measures.** Swapping synthesis for an archive is not automatically an
improvement: three of Kenney's own interface sounds have exactly 0.0% of their
energy above 2 kHz, which is the same dullness that prompted this work. So each
sound declares the band its role needs and is reported against it. A sound that
fails is a sound to replace, not to ship.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
import wave
import zipfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "data" / "audio_sources.json"
CACHE = ROOT / "data" / "audio_cache"
RATE = 44_100

#: What each role needs above 2 kHz, as a fraction of total energy. Not taste:
#: a scratch that is mostly low frequency is not a scratch, and a doom sting
#: that is bright is not ominous. The synthesised horns sat at 2.7-8%, which is
#: what "dull, like a tone through a blanket" measures as.
BANDS = {
    # Measured from the real recordings, not from the oscillators they replaced.
    # The first version of this table was calibrated on the synthesised sounds
    # and therefore failed the genuine article every time: synthetic "crowd" was
    # shaped white noise at 51% high-frequency energy, where a real crowd is 6%;
    # synthetic "whoosh" was filtered noise at 52%, where the recording Marc
    # picked is a bass swoosh with 97% of its energy under 300 Hz. A band set
    # from a synthetic sample describes the synthesis, not the role.
    #
    # Kept deliberately wide. This is a net for "somebody pasted in the wrong
    # file", not a judgement about whether a horn sounds like a touchdown.
    "tap": (0.01, 1.0), "flip": (0.005, 1.0), "rip": (0.05, 1.0),
    "scratch": (0.05, 1.0), "buzzer": (0.05, 1.0), "chime": (0.02, 1.0),
    "horn_01": (0.10, 0.80), "horn_02": (0.10, 0.80), "horn_03": (0.10, 0.80),
    "crowd": (0.02, 0.60), "trombone": (0.005, 0.40), "whoosh": (0.0, 0.95),
    "riser": (0.0, 0.90), "doom": (0.0, 0.15),
    # The beds are music. A band would be meaningless: an orchestral loop and a
    # 128 bpm dance loop have nothing in common spectrally and both are correct.
    "bed_epic": (0.0, 1.0), "bed_party": (0.0, 1.0),
}

FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"


def manifest() -> dict:
    return json.loads(MANIFEST.read_text("utf-8"))


def download(key: str, entry: dict) -> Path:
    """Fetch once, and refuse anything whose bytes are not what we agreed."""
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / f"{key}{Path(entry['url']).suffix or '.bin'}"
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != entry["sha256"]:
        print(f"  fetching {key} from {entry['source']}")
        request = urllib.request.Request(
            entry["url"], headers={"User-Agent": "PUNT/1.0 (+https://punt.mdeller.com)"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            target.write_bytes(response.read())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != entry["sha256"]:
        raise SystemExit(
            f"{key}: sha256 is {digest}, the manifest says {entry['sha256']}.\n"
            f"  Upstream changed the file. Listen to the new one before updating the manifest."
        )
    return target


def extract(archive: Path, member: str, out: Path) -> Path:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            out.write_bytes(zf.read(member))
        return out
    return archive


def to_wav(source: Path, out: Path) -> Path:
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(source),
         "-ar", str(RATE), "-ac", "2", str(out)],
        check=True,
    )
    return out


def measure(path: Path) -> dict:
    with wave.open(str(path)) as w:
        channels = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float) / 32768
    data = data.reshape(-1, channels)
    mono = data.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1 / RATE)
    return {
        "seconds": len(mono) / RATE,
        "channels": channels,
        "true_stereo": channels == 2 and not np.array_equal(data[:, 0], data[:, 1]),
        "hf": float(spectrum[freqs > 2000].sum() / max(spectrum.sum(), 1e-20)),
        "peak": float(np.abs(mono).max()),
        "edge": float(max(abs(mono[0]), abs(mono[-1]))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="measure what is cached, fetch nothing")
    args = parser.parse_args()

    spec = manifest()
    archives: dict[str, Path] = {}
    if not args.check:
        for key, entry in spec["downloads"].items():
            archives[key] = download(key, entry)

    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"\n{'sound':<11}{'sec':>6}{'ch':>4}{'HF>2kHz':>9}{'wants':>13}{'edge':>8}  source")
    failures = 0
    for name, sound in spec["sounds"].items():
        wav = CACHE / f"{name}.wav"
        if not args.check:
            archive = archives[sound["from"]]
            member = sound.get("member")
            # A Freesound entry is one file per sound, so there is nothing to
            # extract; only the Kenney pack is an archive with members in it.
            raw = (extract(archive, member, CACHE / f"{name}{Path(member).suffix}")
                   if member else archive)
            to_wav(raw, wav)
        if not wav.is_file():
            print(f"{name:<11} not fetched yet")
            failures += 1
            continue
        m = measure(wav)
        low, high = BANDS.get(name, (0.0, 1.0))
        ok = low <= m["hf"] <= high
        failures += 0 if ok else 1
        print(f"{name:<11}{m['seconds']:>6.2f}{m['channels']:>4}{m['hf']:>8.1%}"
              f"{f'{low:.0%}-{high:.0%}':>13}{m['edge']:>8.4f}"
              f"  {spec['downloads'][sound['from']]['source']}{'' if ok else '   <-- OUT OF BAND'}")

    missing = sorted(set(BANDS) - set(spec["sounds"]))
    if missing:
        print(f"\nStill synthesised, no CC0 source yet: {', '.join(missing)}")
    print(f"\n{len(spec['sounds'])} sampled, {failures} failing their band.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
