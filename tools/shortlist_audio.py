#!/usr/bin/env python3
"""Search Freesound for every sting that needs replacing, and build a page to pick from.

    python3 tools/shortlist_audio.py                 # all the dull ones
    python3 tools/shortlist_audio.py horn_01 crowd   # just these

Writes docs/shortlist.html: every candidate on a button, with its measurements,
grouped by the sting it is for. Listen, pick, and tell me the Freesound id.

CC0 only, and short only. A sting is an event, not a track: anything over about
six seconds is a field recording somebody would have to edit, and editing is
where "it is CC0" quietly turns into "it is CC0 and also I cut it badly".

The candidates are previews rather than originals, which needs only the API key
and no OAuth2. For sounds that end up inside a 128 kbps sprite the preview is not
the limiting factor -- the sprite's own encoding is.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import wave
import webbrowser
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from freesound import CC0, FFMPEG, ROOT, measure, token  # noqa: E402

OUT = ROOT / "docs" / "shortlist.html"
CACHE = ROOT / "data" / "audio_cache" / "candidates"

#: What to ask for, per sting. Several short queries rather than one long one:
#: Freesound ANDs the words, so "stadium air horn goal" matches nothing at all
#: while "air horn" matches sixty-one CC0 sounds.
QUERIES = {
    "horn_01": ["airhorn", "air horn", "horn blast"],
    "horn_02": ["fanfare", "trumpet fanfare", "brass fanfare"],
    "horn_03": ["fanfare victory", "orchestral hit", "brass stab"],
    "crowd":   ["crowd cheer", "crowd cheering", "applause cheer"],
    "trombone": ["sad trombone", "wah wah", "trombone fail"],
    "whoosh":  ["whoosh", "swoosh", "transition whoosh"],
    "riser":   ["riser", "tension riser", "build up"],
    "chime":   ["success chime", "chime", "notification positive"],
    "rip":     ["paper tear", "paper rip", "tear open"],
    "doom":    ["dark drone", "ominous", "low drone"],
    "buzzer":  ["buzzer", "wrong answer buzzer", "game show buzzer"],
    "scratch": ["record scratch", "vinyl scratch", "scratch dj"],
}

ROLES = {
    "horn_01": "An ordinary touchdown", "horn_02": "A better one, and lead changes",
    "horn_03": "Long touchdowns, clinches, hundred-point milestones",
    "trombone": "Bench disasters and goose eggs. The sad one",
    "whoosh": "Big plays", "riser": "The red-zone countdown",
    "chime": "Clinches and good news", "doom": "Mathematically finished",
    "scratch": "A record stopping: the joke's rimshot",
    "buzzer": "A goose egg from a starter", "crowd": "Under the big moments",
    "rip": "The pack tearing open",
}

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
}


def search(query: str, seconds: float, limit: int) -> list[dict]:
    params = {
        "query": query,
        "filter": f"{CC0} duration:[0.2 TO {seconds}]",
        "fields": "id,name,username,license,duration,channels,samplerate,previews,url",
        # Downloads, not rating. Rating surfaced a Prague metro train horn and a
        # 1941 Buick; downloads surfaced the airhorn with seventeen thousand uses.
        # How often a sound is actually used is a much better quality signal than
        # how a handful of people scored it.
        "sort": "downloads_desc",
        "page_size": limit,
        "token": token(),
    }
    url = "https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "PUNT/1.0 (+https://punt.mdeller.com)"})
    with urllib.request.urlopen(request, timeout=40) as response:
        return json.load(response).get("results", [])


def grab(sound: dict, into: Path) -> Path | None:
    # `into` is the directory, not the file: mkdir on .parent made the sting's
    # own folder never exist and every download failed with ENOENT.
    into.mkdir(parents=True, exist_ok=True)
    target = into / f"{sound['id']}.mp3"
    if target.is_file():
        return target
    try:
        request = urllib.request.Request(sound["previews"]["preview-hq-mp3"],
                                         headers={"User-Agent": "PUNT/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            target.write_bytes(response.read())
        return target
    except Exception as exc:  # noqa: BLE001 - one dud candidate is not fatal
        print(f"    {sound['id']}: {exc}")
        return None


def as_wav_b64(path: Path) -> str:
    with tempfile.TemporaryDirectory() as work:
        wav = Path(work) / "c.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(path),
                        "-ac", "1", "-ar", "44100", "-t", "8", str(wav)], check=True)
        return base64.b64encode(wav.read_bytes()).decode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stings", nargs="*", default=None)
    parser.add_argument("--per-query", type=int, default=5)
    parser.add_argument("--max-seconds", type=float, default=6.0)
    args = parser.parse_args()

    wanted = args.stings or list(QUERIES)
    blocks = []
    for name in wanted:
        if name not in QUERIES:
            print(f"no queries for {name}; known: {', '.join(QUERIES)}")
            continue
        print(f"\n{name}  ({ROLES.get(name, '')})")
        seen: dict[int, dict] = {}
        for query in QUERIES[name]:
            for sound in search(query, args.max_seconds, args.per_query):
                seen.setdefault(sound["id"], sound)

        rows = []
        for sound in seen.values():
            clip = grab(sound, CACHE / name)
            if clip is None:
                continue
            m = measure(clip)
            low, high = BANDS.get(name, (0.0, 1.0))
            ok = low <= m["hf"] <= high
            print(f"    {sound['id']:>8}{m['seconds']:>6.2f}s{m['hf']:>7.1%}"
                  f"{'  in band' if ok else '  out of band'}  {sound['name'][:40]}")
            rows.append((sound, m, ok, as_wav_b64(clip)))

        rows.sort(key=lambda r: (not r[2], -r[1]["hf"]))
        cells = "".join(f"""
      <tr class="{'' if ok else 'off'}">
        <td><button data-id="c{s['id']}">play</button>
            <audio id="c{s['id']}" preload="none" src="data:audio/wav;base64,{b64}"></audio></td>
        <td class="id"><a href="{s['url']}">#{s['id']}</a></td>
        <td class="nm">{s['name'][:52]}</td>
        <td class="num">{m['seconds']:.2f}s</td>
        <td class="num">{m['hf']:.0%}</td>
        <td class="num">{'stereo' if m['true_stereo'] else 'mono'}</td>
        <td class="by">{s['username']}</td>
      </tr>""" for s, m, ok, b64 in rows)
        blocks.append(f"""
  <section>
    <h2>{name} <span class="role">{ROLES.get(name, '')}</span></h2>
    <table><thead><tr><th></th><th>id</th><th>name</th><th>len</th><th>HF</th><th></th><th>by</th></tr></thead>
    <tbody>{cells}</tbody></table>
  </section>""")

    OUT.write_text(f"""<!doctype html>
<meta charset="utf-8"><title>PUNT &middot; shortlist</title>
<style>
 body{{background:#131826;color:#e8ecf5;font:15px/1.5 -apple-system,system-ui,sans-serif;margin:0;padding:30px 20px}}
 main{{max-width:900px;margin:0 auto}} h1{{font-size:22px;margin:0 0 6px}}
 h2{{font-size:16px;margin:30px 0 8px;font-family:ui-monospace,monospace;color:#7aa2ff}}
 .role{{color:#7c8aa5;font:12px -apple-system,sans-serif;font-weight:400}}
 p.lede{{color:#93a0b8;max-width:68ch;margin:0 0 10px}}
 table{{width:100%;border-collapse:collapse;font-size:13.5px}}
 th{{text-align:left;font:10px ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;color:#7c8aa5;padding:0 8px 6px}}
 td{{padding:6px 8px;border-top:1px solid #222b40}}
 td.num{{font-family:ui-monospace,monospace;text-align:right;color:#b9c4d8}}
 td.id{{font-family:ui-monospace,monospace}} td.by,td.nm{{color:#93a0b8}}
 tr.off td{{opacity:.45}}
 a{{color:#7aa2ff}} audio{{display:none}}
 button{{font:600 12px ui-monospace,monospace;background:#1d2740;color:#e8ecf5;border:1px solid #35446a;
   border-radius:6px;padding:5px 12px;cursor:pointer}}
 button:hover{{background:#27355a}} button.on{{background:#e0348b;border-color:#e0348b}}
</style>
<main>
<h1>Shortlist: CC0 candidates</h1>
<p class="lede">All CC0, all under {args.max_seconds:.0f}s. <strong>HF</strong> is energy above
2&nbsp;kHz; rows that are dimmed fall outside the band that sting's role wants, and are
kept only because a number is not the same thing as an ear. Tell me the ids you want.</p>
{''.join(blocks)}
</main>
<script>
document.querySelectorAll('button[data-id]').forEach(b => b.addEventListener('click', () => {{
  const el = document.getElementById(b.dataset.id);
  document.querySelectorAll('audio').forEach(a => {{ a.pause(); }});
  el.currentTime = 0;
  el.play().then(() => {{ b.classList.add('on'); el.onended = () => b.classList.remove('on'); }})
           .catch(e => {{ b.textContent = 'failed'; console.error(e); }});
}}));
</script>
""", encoding="utf-8")
    print(f"\nwrote {OUT.relative_to(ROOT)}")
    webbrowser.open(OUT.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
