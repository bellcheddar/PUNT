#!/usr/bin/env python3
"""A page for listening to every sting, because measurement cannot do this bit.

    python3 tools/audition.py            # writes docs/audition.html and opens it

The spectrum says whether a sound is dull, whether it clicks, and whether it is
loud enough. It cannot say whether a horn sounds like a touchdown. That is the
only question that actually matters here and the only one a person has to
answer, so this builds a page with every sting on a button, its measurements
beside it, and the role it plays in the app.

Reads the built sprite, so what you hear is exactly what the bar hears --
including the mp3 encoding, which is where a sting with too much high end starts
to sound like a splash of gravel.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import wave
import webbrowser
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
AUDIO = ROOT / "static" / "audio"
OUT = ROOT / "docs" / "audition.html"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"

ROLES = {
    "horn_01": "An ordinary touchdown", "horn_02": "A better one, and lead changes",
    "horn_03": "Long touchdowns, clinches, hundred-point milestones",
    "trombone": "Bench disasters and goose eggs. The sad one",
    "whoosh": "Big plays", "riser": "The red-zone countdown",
    "chime": "Clinches and good news", "doom": "Mathematically finished",
    "scratch": "A record stopping: the joke's rimshot",
    "buzzer": "A goose egg from a starter", "crowd": "Under the big moments",
    "tap": "A finger on a control", "flip": "A card turning over",
    "rip": "The pack tearing open",
}


def measurements() -> dict[str, dict]:
    sprite = json.loads((AUDIO / "sprite.json").read_text())["sprite"]
    with tempfile.TemporaryDirectory() as work:
        wav = Path(work) / "s.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(AUDIO / "sprite.mp3"),
                        "-ac", "1", "-ar", "44100", str(wav)], check=True)
        with wave.open(str(wav)) as handle:
            audio = np.frombuffer(handle.readframes(handle.getnframes()),
                                  dtype=np.int16).astype(float) / 32768
    out = {}
    for name, (start, length) in sprite.items():
        seg = audio[int(start * 44.1): int((start + length) * 44.1)]
        spectrum = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
        freqs = np.fft.rfftfreq(len(seg), 1 / 44100)
        out[name] = {
            "ms": length,
            "hf": float(spectrum[freqs > 2000].sum() / max(spectrum.sum(), 1e-20)),
            "edge": float(max(abs(seg[0]), abs(seg[-1]))),
            "peak": float(np.abs(seg).max()),
        }
    return out


def slices() -> dict[str, str]:
    """Each sting as its own base64 wav, cut from the built sprite.

    One file per button, played by an ordinary <audio> element. The first
    version of this page used Web Audio: inline the whole sprite, decode it once
    with `decodeAudioData`, and play slices out of the buffer. Every button did
    nothing, and it took three wrong diagnoses to find out why.

    `fetch()` from a file:// page is a CORS error, which was real and fixed by
    inlining. Then the inlined version still did nothing, and the probe said
    `bytes.length === 0` -- which is not a bug either: `decodeAudioData` DETACHES
    the ArrayBuffer you hand it, so reading the array afterwards always says
    zero. The actual fault was that `decodeAudioData` never called back at all on
    the 223 kB sprite, while decoding a 3 kB file in the same browser worked.

    So: no Web Audio. An <audio> element with a data: URI needs no fetch, no
    decode call, no ArrayBuffer, and no AudioContext waiting on a user gesture to
    leave `suspended`. It plays in a click handler, which is the only thing this
    page has to do. WAV rather than mp3 so there is no second encode between the
    sprite and your ears.
    """
    sprite = json.loads((AUDIO / "sprite.json").read_text())["sprite"]
    out = {}
    with tempfile.TemporaryDirectory() as work:
        decoded = Path(work) / "s.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(AUDIO / "sprite.mp3"),
                        "-ac", "1", "-ar", "44100", str(decoded)], check=True)
        for name, (start_ms, length_ms) in sprite.items():
            cut = Path(work) / f"{name}.wav"
            subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(decoded),
                            "-ss", f"{start_ms / 1000:.3f}", "-t", f"{length_ms / 1000:.3f}",
                            str(cut)], check=True)
            out[name] = base64.b64encode(cut.read_bytes()).decode("ascii")
    return out


def render() -> str:
    sprite = json.loads((AUDIO / "sprite.json").read_text())["sprite"]
    stats = measurements()
    clips = slices()
    sources = json.loads((ROOT / "data" / "audio_sources.json").read_text())
    sampled = sources.get("sounds", {})
    downloads = sources.get("downloads", {})

    rows = []
    for name in sorted(sprite, key=lambda n: (n not in sampled, n)):
        m = stats[name]
        if name in sampled:
            origin = downloads[sampled[name]["from"]]
            where = f'<a href="{origin["page"]}">{origin["source"]}</a>'
            tag = '<span class="new">sampled</span>'
        else:
            where = "synthesised in <code>tools/make_audio.py</code>"
            tag = '<span class="old">synth</span>'
        dull = ' class="num dull"' if (m["hf"] < 0.10 and name not in ("doom", "trombone", "flip", "tap")) else ' class="num"'
        rows.append(f"""
    <tr>
      <td><button data-s="{name}">{name}</button> {tag}
          <audio id="a-{name}" preload="auto" src="data:audio/wav;base64,{clips[name]}"></audio></td>
      <td class="role">{ROLES.get(name, "")}</td>
      <td class="num">{m['ms']} ms</td>
      <td{dull}>{m['hf']:.1%}</td>
      <td class="num">{m['edge']:.4f}</td>
      <td class="src">{where}</td>
    </tr>""")

    return f"""<!doctype html>
<meta charset="utf-8"><title>PUNT &middot; audition the stings</title>
<style>
 body {{ background:#131826; color:#e8ecf5; font:15px/1.5 -apple-system,system-ui,sans-serif;
        margin:0; padding:32px 20px; }}
 main {{ max-width:940px; margin:0 auto; }}
 h1 {{ font-size:22px; margin:0 0 4px; }}
 p.lede {{ color:#93a0b8; margin:0 0 24px; max-width:68ch; }}
 table {{ width:100%; border-collapse:collapse; font-size:14px; }}
 th {{ text-align:left; font:11px/1 ui-monospace,monospace; letter-spacing:.1em;
       text-transform:uppercase; color:#7c8aa5; padding:0 10px 8px; }}
 td {{ padding:9px 10px; border-top:1px solid #222b40; vertical-align:middle; }}
 td.num {{ font-family:ui-monospace,monospace; text-align:right; color:#b9c4d8; }}
 td.dull {{ color:#ff8f6b; font-weight:600; }}
 td.role {{ color:#93a0b8; }}
 td.src {{ color:#7c8aa5; font-size:12.5px; }}
 a {{ color:#7aa2ff; }}
 audio {{ display:none; }}
 button {{ font:600 14px ui-monospace,monospace; background:#1d2740; color:#e8ecf5;
           border:1px solid #35446a; border-radius:7px; padding:7px 13px; cursor:pointer;
           min-width:104px; text-align:left; }}
 button:hover {{ background:#27355a; }}
 button.on {{ background:#e0348b; border-color:#e0348b; color:#fff; }}
 button.bad {{ background:#5a1d2a; border-color:#a03050; }}
 .new {{ color:#4ad991; font:10px ui-monospace,monospace; }}
 .old {{ color:#7c8aa5; font:10px ui-monospace,monospace; }}
 .key {{ color:#7c8aa5; font-size:13px; margin-top:22px; }}
 .all {{ margin-bottom:18px; }}
</style>
<main>
<h1>Audition the stings</h1>
<p class="lede">Cut straight out of the built sprite, so this is what the bar hears.
<strong>HF</strong> is the fraction of each sound's energy above 2&nbsp;kHz: under 10% is
what "dull, like a tone through a blanket" measures as, flagged orange except where being
dark is the point. <strong>Edge</strong> is the amplitude where the playback window ends;
above about 0.02 it clicks.</p>
<p class="all"><button id="play-all">play all in order</button></p>
<table>
<thead><tr><th>Sting</th><th>Used for</th><th>Length</th><th>HF&gt;2kHz</th><th>Edge</th><th>Source</th></tr></thead>
<tbody>{''.join(rows)}
</tbody></table>
<p class="key">Tell me which ones are wrong and what they should sound like instead.
A spectrum cannot tell whether a horn sounds like a touchdown.</p>
</main>
<script>
// No Web Audio, no fetch, no decodeAudioData: see slices() in tools/audition.py
// for the three separate reasons the first version of this page was silent.
function play(name) {{
  const el = document.getElementById('a-' + name);
  const btn = document.querySelector(`button[data-s="${{name}}"]`);
  el.currentTime = 0;
  return el.play().then(() => {{
    btn.classList.add('on');
    return new Promise(r => {{ el.onended = () => {{ btn.classList.remove('on'); r(); }}; }});
  }}).catch(e => {{
    btn.classList.add('bad');
    btn.textContent = name + ' failed';
    console.error(name, e);
  }});
}}
document.querySelectorAll('button[data-s]').forEach(b =>
  b.addEventListener('click', () => play(b.dataset.s)));
document.getElementById('play-all').addEventListener('click', async () => {{
  for (const b of document.querySelectorAll('button[data-s]')) {{
    await play(b.dataset.s);
    await new Promise(r => setTimeout(r, 180));
  }}
}});
</script>
"""


def main() -> int:
    OUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    webbrowser.open(OUT.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
