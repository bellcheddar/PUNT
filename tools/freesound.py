#!/usr/bin/env python3
"""Find CC0 sounds on Freesound, measure them, and print manifest entries.

    export FREESOUND_TOKEN=...          # from https://freesound.org/apiv2/apply/
    python3 tools/freesound.py horn_01 "stadium air horn"
    python3 tools/freesound.py --audition horn_01   # write the candidates to listen to

Why the API key is enough, and no OAuth2 is needed. The token gets search,
metadata and the HQ preview mp3; downloading the *original* wav is the one thing
that needs the full OAuth2 dance. For fourteen stings that are re-encoded into a
128 kbps sprite anyway, the preview is not the limiting factor -- the sprite's
own encoding is -- so the callback URL on the application form can be left blank.

Nothing here writes to the manifest by itself. It prints candidates with their
measurements and the JSON to paste, because which horn sounds like a touchdown
is a judgement a spectrum cannot make.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
API = "https://freesound.org/apiv2"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"

#: Only this licence. A public MIT repository cannot carry an attribution
#: obligation on behalf of everyone who clones it, and a non-commercial clause
#: would make it undistributable. Freesound's own filter string for CC0.
CC0 = 'license:"Creative Commons 0"'

def token() -> str:
    """The API key, from the environment or from the gitignored .env.

    .env rather than a prompt or an argument, and never pasted into a chat: an
    agent session's `/export` writes a verbatim transcript into this directory,
    and this repository is public. The same reasoning as the ESPN cookies in
    docs/credentials.md, even though this key is only read access to a public
    sound library -- the mechanism that would leak it does not care which.
    """
    value = os.environ.get("FREESOUND_TOKEN", "").strip()
    if not value:
        env = ROOT / ".env"
        if env.is_file():
            for line in env.read_text("utf-8").splitlines():
                if line.strip().startswith("FREESOUND_TOKEN="):
                    value = line.split("=", 1)[1].strip().strip("\"'")
                    break
    if not value:
        raise SystemExit(
            "No FREESOUND_TOKEN.\n\n"
            "  Apply at https://freesound.org/apiv2/apply/ -- log in first, and go\n"
            "  straight to that URL: there is no menu link to it. Leave the Callback\n"
            "  URL blank, it is only for OAuth2, which this does not use.\n\n"
            "  You want the API KEY, not the Client ID. The Client ID is the OAuth2 half.\n\n"
            "  Then, in the repo root:\n"
            "      echo 'FREESOUND_TOKEN=your_key_here' >> .env\n\n"
            "  .env is gitignored. Do not paste the key into a chat: /export writes a\n"
            "  verbatim transcript into this directory and this repository is public."
        )
    return value


def get(path: str, **params) -> dict:
    params["token"] = token()
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "PUNT/1.0 (+https://punt.mdeller.com)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def measure(path: Path) -> dict:
    with tempfile.TemporaryDirectory() as work:
        wav = Path(work) / "s.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(path),
                        "-ar", "44100", str(wav)], check=True)
        with wave.open(str(wav)) as handle:
            channels = handle.getnchannels()
            raw = handle.readframes(handle.getnframes())
    data = np.frombuffer(raw, dtype=np.int16).astype(float).reshape(-1, channels) / 32768
    mono = data.mean(axis=1)
    spectrum = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1 / 44100)
    return {
        "seconds": len(mono) / 44100,
        "channels": channels,
        "true_stereo": channels == 2 and not np.array_equal(data[:, 0], data[:, 1]),
        "hf": float(spectrum[freqs > 2000].sum() / max(spectrum.sum(), 1e-20)),
        "peak": float(np.abs(mono).max()),
    }


def fetch_preview(sound: dict, into: Path) -> Path:
    url = sound["previews"]["preview-hq-mp3"]
    into.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "PUNT/1.0 (+https://punt.mdeller.com)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        into.write_bytes(response.read())
    return into


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="which sting this is for, e.g. horn_01")
    parser.add_argument("query", nargs="?", help="search terms; defaults to the sting name")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-seconds", type=float, default=6.0,
                        help="a sting is an event, not a track")
    parser.add_argument("--out", default=str(ROOT / "data" / "audio_cache" / "candidates"))
    args = parser.parse_args()

    query = args.query or args.name.replace("_", " ")
    found = get("/search/text/", query=query, filter=f'{CC0} duration:[0.1 TO {args.max_seconds}]',
                fields="id,name,username,license,duration,channels,samplerate,previews,url",
                sort="rating_desc", page_size=args.limit)

    results = found.get("results", [])
    if not results:
        print(f"nothing CC0 under {args.max_seconds}s for {query!r}")
        return 1

    out = Path(args.out) / args.name
    print(f"\n{len(results)} CC0 candidates for {args.name} ({query!r})\n")
    print(f"{'id':>9}{'sec':>6}{'ch':>4}{'HF>2kHz':>9}  name")
    entries = []
    for sound in results:
        try:
            preview = fetch_preview(sound, out / f"{sound['id']}.mp3")
            m = measure(preview)
        except Exception as exc:  # noqa: BLE001 - one bad candidate is not fatal
            print(f"{sound['id']:>9}  could not measure: {exc}")
            continue
        print(f"{sound['id']:>9}{m['seconds']:>6.2f}{m['channels']:>4}{m['hf']:>8.1%}"
              f"  {sound['name'][:44]}")
        entries.append({
            "id": sound["id"],
            "url": sound["previews"]["preview-hq-mp3"],
            "sha256": hashlib.sha256(preview.read_bytes()).hexdigest(),
            "bytes": preview.stat().st_size,
            "source": f"Freesound #{sound['id']} — {sound['name']}",
            "page": sound["url"],
            "author": sound["username"],
            "licence": "CC0 1.0",
            "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "recorded": True,
        })

    print(f"\nPreviews written to {out}. Listen, pick one, then paste its block into")
    print("data/audio_sources.json under \"downloads\" and point the sting at it.\n")
    print(json.dumps({e['source'].split()[1].strip('#'): e for e in entries}, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
