#!/usr/bin/env python3
"""Shortlist the two music beds, and let Marc pick by ear.

    python3 tools/shortlist_music.py

PUNT wants two beds: a Sunday-night-football theme for the Big Board and the
pack rip, and a DJ watch-party loop for the afternoon.

Music is the one place CC-BY is allowed. The sports-broadcast idiom does not
exist under CC0 -- CC0 gives you epic percussion, taiko and marching snare, but
no melodic brass-rock theme, and a search for "rock anthem" returns literally
nothing. Attribution is safe here because LICENCES.md is generated from the same
manifest the builder reads and a test refuses to let the two drift, so the
obligation travels with the repo by construction rather than by anyone
remembering.

The one thing that is still off limits is not a licensing question: the actual
Sunday Night Football theme and "Gonna Fly Now" are copyrighted compositions
owned by NBC and by Bill Conti's publisher. The idiom is a genre convention; the
tunes are not. No archive here contains them and none may be ripped in.

Auditions are capped at 20 seconds so the page stays openable.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from freesound import FFMPEG, ROOT, measure, token  # noqa: E402

OUT = ROOT / "docs" / "shortlist-music.html"
CACHE = ROOT / "data" / "audio_cache" / "music"
CLIP = 20

QUERIES = {
    "bed_epic": ("The Sunday-night theme. Under the Big Board and the pack rip", [
        "epic orchestral", "epic trailer music", "action rock",
        "brass fanfare music", "orchestral loop", "sports intro",
    ]),
    "bed_party": ("The watch-party bed. Under the afternoon, quietly", [
        "hip hop beat", "funk groove", "house loop",
        "boom bap", "breakbeat", "groove loop",
    ]),
}

#: Both, because the whole point of this pass is that CC0 alone cannot do it.
LICENCES = ['license:"Creative Commons 0"', 'license:"Attribution"']
SHORT = {"Creative Commons 0": "CC0", "Attribution": "CC-BY"}


def polite(request: urllib.request.Request, timeout: int = 40, tries: int = 5):
    """Back off rather than hammer. Freesound answers 503 when it has had enough,
    and an undocumented courtesy endpoint is exactly the wrong thing to retry at
    full speed -- the way that courtesy ends is somebody's script in a loop."""
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 503) or attempt == tries - 1:
                raise
            wait = 2 ** attempt + 1
            print(f"      {exc.code} from Freesound; waiting {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def search(query: str, licence: str, limit: int = 5) -> list[dict]:
    params = {
        "query": query,
        "filter": f"{licence} duration:[8 TO 120]",
        "fields": "id,name,username,license,duration,channels,previews,url,num_downloads",
        "sort": "downloads_desc",
        "page_size": limit,
        "token": token(),
    }
    url = "https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "PUNT/1.0"})
    with polite(request) as response:
        results = json.load(response).get("results", [])
    time.sleep(0.7)          # a request every 0.7 s is well inside the limit
    return results


def clip(sound: dict, folder: Path) -> tuple[Path, str] | None:
    folder.mkdir(parents=True, exist_ok=True)
    raw = folder / f"{sound['id']}.mp3"
    if not raw.is_file():
        try:
            request = urllib.request.Request(sound["previews"]["preview-hq-mp3"],
                                             headers={"User-Agent": "PUNT/1.0"})
            with polite(request, timeout=90) as response:
                raw.write_bytes(response.read())
            time.sleep(0.4)
        except Exception as exc:  # noqa: BLE001
            print(f"    {sound['id']}: {exc}")
            return None
    with tempfile.TemporaryDirectory() as work:
        wav = Path(work) / "c.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(raw),
                        "-ac", "2", "-ar", "32000", "-t", str(CLIP), str(wav)], check=True)
        return raw, base64.b64encode(wav.read_bytes()).decode("ascii")


def main() -> int:
    blocks = []
    for bed, (role, queries) in QUERIES.items():
        print(f"\n{bed}  ({role})")
        seen: dict[int, dict] = {}
        for licence in LICENCES:
            for query in queries:
                for sound in search(query, licence):
                    seen.setdefault(sound["id"], sound)

        rows = []
        for sound in sorted(seen.values(), key=lambda s: -s["num_downloads"])[:18]:
            got = clip(sound, CACHE / bed)
            if got is None:
                continue
            raw, b64 = got
            m = measure(raw)
            lic = "CC0" if "zero" in sound["license"] else "CC-BY"
            print(f"    {sound['id']:>8}{m['seconds']:>7.1f}s  {lic:<6} dl={sound['num_downloads']:>6}"
                  f"  {sound['name'][:40]}")
            rows.append((sound, m, lic, b64))

        cells = "".join(f"""
      <tr>
        <td><button data-id="m{s['id']}">play {CLIP}s</button>
            <audio id="m{s['id']}" preload="none" src="data:audio/wav;base64,{b64}"></audio></td>
        <td class="id"><a href="{s['url']}">#{s['id']}</a></td>
        <td class="nm">{s['name'][:50]}</td>
        <td class="num">{m['seconds']:.0f}s</td>
        <td class="lic {'by' if lic == 'CC-BY' else ''}">{lic}</td>
        <td class="num">{s['num_downloads']:,}</td>
        <td class="by">{s['username']}</td>
      </tr>""" for s, m, lic, b64 in rows)
        blocks.append(f"""
  <section><h2>{bed} <span class="role">{role}</span></h2>
  <table><thead><tr><th></th><th>id</th><th>name</th><th>full</th><th>licence</th><th>uses</th><th>by</th></tr></thead>
  <tbody>{cells}</tbody></table></section>""")

    OUT.write_text(f"""<!doctype html>
<meta charset="utf-8"><title>PUNT &middot; music shortlist</title>
<style>
 body{{background:#131826;color:#e8ecf5;font:15px/1.5 -apple-system,system-ui,sans-serif;margin:0;padding:30px 20px}}
 main{{max-width:920px;margin:0 auto}} h1{{font-size:22px;margin:0 0 6px}}
 h2{{font-size:16px;margin:32px 0 8px;font-family:ui-monospace,monospace;color:#7aa2ff}}
 .role{{color:#7c8aa5;font:12px -apple-system,sans-serif;font-weight:400}}
 p.lede{{color:#93a0b8;max-width:70ch}} p.warn{{color:#ffb38a;max-width:70ch;font-size:13.5px}}
 table{{width:100%;border-collapse:collapse;font-size:13.5px}}
 th{{text-align:left;font:10px ui-monospace,monospace;letter-spacing:.1em;text-transform:uppercase;color:#7c8aa5;padding:0 8px 6px}}
 td{{padding:6px 8px;border-top:1px solid #222b40}}
 td.num{{font-family:ui-monospace,monospace;text-align:right;color:#b9c4d8}}
 td.id{{font-family:ui-monospace,monospace}} td.by,td.nm{{color:#93a0b8}}
 td.lic{{font:11px ui-monospace,monospace;color:#4ad991}} td.lic.by{{color:#fcb900}}
 a{{color:#7aa2ff}} audio{{display:none}}
 button{{font:600 12px ui-monospace,monospace;background:#1d2740;color:#e8ecf5;border:1px solid #35446a;
   border-radius:6px;padding:5px 12px;cursor:pointer;min-width:92px}}
 button:hover{{background:#27355a}} button.on{{background:#e0348b;border-color:#e0348b}}
</style>
<main>
<h1>Music shortlist: the two beds</h1>
<p class="lede">Twenty seconds of each. <span style="color:#4ad991">CC0</span> needs nothing;
<span style="color:#fcb900">CC-BY</span> needs a credit line, which <code>LICENCES.md</code>
generates from the manifest automatically. <strong>Uses</strong> is the download count, which
turned out to be the best quality signal there is.</p>
<p class="warn">Not on this page and never will be: the real Sunday Night Football theme and
"Gonna Fly Now". Those are copyrighted compositions owned by NBC and by Bill Conti's
publisher. The <em>idiom</em> is a genre convention and is fair game; the tunes are not.</p>
{''.join(blocks)}
</main>
<script>
document.querySelectorAll('button[data-id]').forEach(b => b.addEventListener('click', () => {{
  const el = document.getElementById(b.dataset.id);
  document.querySelectorAll('audio').forEach(a => a.pause());
  document.querySelectorAll('button').forEach(x => x.classList.remove('on'));
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
