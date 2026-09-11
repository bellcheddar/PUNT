#!/usr/bin/env python3
"""Capture the app at a real phone width.

Chrome's window has a platform minimum width of 500 px on macOS, so
`--window-size=390,844` silently lays the page out at 500 and the resulting
capture is a 390 px crop of a 500 px layout. Everything looks broken -- the tab
bar loses a tab, the header loses its mute button, text runs off the right edge --
and none of it is a CSS bug. That cost a round of chasing a phantom overflow.

The fix is to render the page in an iframe of the intended size inside a window
that is large enough for Chrome to accept, and crop to the iframe. The iframe
gets a genuine 390 px layout viewport, which is the thing being tested.

    python3 tools/screenshot.py --out docs/screenshots
    python3 tools/screenshot.py --url http://127.0.0.1:8019/album --width 390
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ROOT = Path(__file__).resolve().parent.parent

#: Set by main(); the harness must be fetched over HTTP rather than from file://
#: so it is same-origin with the app and can seed the iframe's localStorage.
BASE_FOR_HARNESS = ["http://127.0.0.1:8019"]

#: Most routes are captured with `?punt=steady&team=5`, which skips the first-run
#: overlays and marks a team, so the captures show the app in use rather than
#: whichever one-time overlay a fresh browser profile is due. The pack and the
#: chooser get their own shots without it.
STEADY = "punt=steady&team=5"

#: (filename stem, path, viewport width, viewport height, alt text, steady?)
SHOTS = [
    # Taller than a phone on purpose: the Today tab is three panels and a 844 px
    # capture shows only the first, which makes the README's hero image an
    # advert for a scoreboard rather than for the app.
    ("today", "/", 390, 1560, "The Today tab on a phone: five live matchups with the phone owner's own team marked, who is in trouble, and the live commentary feed", True),
    ("album", "/album", 390, 844, "Ten manager cards in a two-by-five grid, tiered epic, rare, common or cursed by this week's score, each tinted in that team's own colour", True),
    ("pack", "/album", 390, 844, "The weekly pack, sealed: a foil packet with the wordmark, the week and the card count, waiting to be ripped", False),
    ("chooser", "/", 390, 1000, "First run: pick which of the ten managers is holding this phone. Kept locally, with no account to make", False),
    ("cheer", "/cheer", 390, 844, "The Cheer tab: for every live NFL game, whether you should be cheering, booing or conflicted, and exactly why", True),
    ("swing", "/swing", 390, 844, "The Swing tab: live Monte Carlo win probability and the day's biggest swings", True),
    ("receipts", "/receipts", 390, 844, "The Receipts tab: every manager ranked by points left on the bench, with the exact swap that cost them", True),
    ("big-board", "/big-board?tv=1", 1280, 720, "The Big Board in TV mode: the whole slate on the bar screen", True),
]


def capture(url: str, width: int, height: int, out: Path, scale: int = 2) -> Path:
    """Render one route at a real phone width and save a PNG.

    The route decides which overlays it shows, via `?punt=steady`, rather than
    the harness seeding localStorage: a seeded value set by the parent page is
    not reliably visible to the iframe under `--screenshot`, and every capture
    came out showing a first-run overlay.
    """
    harness = f"""<!DOCTYPE html><meta charset="utf-8">
<style>
  html,body {{ margin:0; padding:0; background:#0d1017; }}
  iframe {{ width:{width}px; height:{height}px; border:0; display:block; }}
</style>
<iframe src="{url}"></iframe>"""

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as handle:
        handle.write(harness)
        harness_path = handle.name
    harness_url = f"file://{harness_path}"

    # The window has to clear Chrome's 500 px floor and be tall enough for the
    # whole iframe; the crop below discards the rest.
    window_w = max(520, width + 40)
    window_h = max(600, height + 40)
    subprocess.run(
        [
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            f"--force-device-scale-factor={scale}",
            f"--window-size={window_w},{window_h}",
            "--virtual-time-budget=3000",
            f"--screenshot={out}", f"file://{harness_path}",
        ],
        check=True, capture_output=True,
    )
    Path(harness_path).unlink(missing_ok=True)

    from PIL import Image

    image = Image.open(out)
    image = image.crop((0, 0, width * scale, height * scale))
    # Downscale to about 1400 px: a 3x grab is several megabytes and GitHub will
    # not thank you for it.
    if image.width > 1400:
        image = image.resize((1400, round(image.height * 1400 / image.width)), Image.LANCZOS)
    # Flatten: PIL hands back RGBA by default and an alpha channel is rejected
    # wherever these get reused as store assets.
    image.convert("RGB").save(out, optimize=True)

    grey = image.convert("L")
    low, high = grey.getextrema()
    if high == low:
        raise SystemExit(f"{out} is a uniform image: the capture failed, do not commit it")
    return out


def check_overflow(base: str, width: int = 390) -> int:
    """Report any element wider than the viewport, on every route.

    Horizontal overflow at phone width is the failure this project keeps
    producing and cannot see: a table column pushed past the right edge simply
    is not drawn, with no scrollbar and nothing to suggest it exists. It has
    happened twice, both times found by looking at a screenshot.

    Mechanics, both of which were arrived at the hard way. The routes are loaded
    into iframes of the intended width, because Chrome will not give a window
    narrower than 500 px and a 390 px capture of a 500 px layout looks broken in
    exactly the way real overflow does. And the harness is written into the app's
    own `static/` directory and fetched over HTTP, because reading an iframe's
    DOM is same-origin only: a `file://` harness reads nothing, and
    `--disable-web-security` with a throwaway profile hangs Chrome outright.
    """
    routes = sorted({r for _, r, _, _, _, _ in SHOTS if not r.startswith("/big-board")})
    frames = "".join(f'<iframe data-route="{r}" src="{r}"></iframe>' for r in routes)
    probe = f"""<!DOCTYPE html><meta charset="utf-8">
<style>html,body{{margin:0}}iframe{{width:{width}px;height:1200px;border:0;display:block}}</style>
{frames}
<pre id="out">pending</pre>
<script>
window.addEventListener('load', () => {{
  const lines = [...document.querySelectorAll('iframe')].map(f => {{
    const d = f.contentDocument;
    if (!d) return f.dataset.route + '  (not readable)';
    const w = d.documentElement.clientWidth;
    const wide = [...d.querySelectorAll('body *')]
      .filter(e => Math.round(e.getBoundingClientRect().right) > w + 1)
      .map(e => e.tagName + '.' + (e.className || '-').toString().split(' ')[0])
      .filter((v, i, a) => a.indexOf(v) === i).slice(0, 6);
    return f.dataset.route.padEnd(12) + ' client=' + w + ' scroll=' + d.documentElement.scrollWidth +
           (wide.length ? '  OVERFLOW: ' + wide.join(', ') : '  ok');
  }});
  document.getElementById('out').textContent = lines.join('\\n');
}});
</script>"""

    # Written into static/ so it is same-origin with the routes, and removed
    # again whatever happens: a probe left in the static tree would ship.
    harness = Path(__file__).resolve().parent.parent / "static" / "_overflow_probe.html"
    harness.write_text(probe, encoding="utf-8")
    try:
        result = subprocess.run(
            # No --virtual-time-budget. The cards carry infinite CSS animations
            # (the epic and legendary foil sweeps), and virtual time never
            # reaches quiescence with one running, so Chrome simply never exits.
            # `--dump-dom` waits for `load` on its own, which is all this needs.
            [CHROME, "--headless=new", "--disable-gpu",
             f"--window-size={max(520, width + 40)},1000",
             "--dump-dom", f"{base}/static/_overflow_probe.html"],
            capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: the overflow probe timed out", file=sys.stderr)
        return 1
    finally:
        harness.unlink(missing_ok=True)

    import html as html_module
    import re as re_module

    match = re_module.search(r'<pre id="out">(.*?)</pre>', result.stdout, re_module.S)
    report = html_module.unescape(match.group(1)) if match else "(probe produced nothing)"
    print(report)
    # "not readable", "pending" and "nothing" are the probe failing, not the page
    # passing. A check that reports success when it measured nothing is worse
    # than no check at all.
    failed = any(m in report for m in ("OVERFLOW", "pending", "not readable", "nothing"))
    if failed:
        print("\nFAIL: see above", file=sys.stderr)
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8019")
    parser.add_argument("--out", default="docs/screenshots")
    parser.add_argument("--url", help="capture a single URL instead of the standard set")
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    parser.add_argument("--check-overflow", action="store_true",
                        help="report elements wider than the viewport instead of capturing")
    args = parser.parse_args()

    if args.check_overflow:
        return check_overflow(args.base, args.width)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    BASE_FOR_HARNESS[0] = args.base

    if args.url:
        path = capture(args.url, args.width, args.height, out_dir / "capture.png")
        print(f"  {path}")
        return 0

    for stem, path, width, height, alt, steady in SHOTS:
        url = f"{args.base}{path}"
        if steady:
            url += ("&" if "?" in path else "?") + STEADY
        target = capture(url, width, height, out_dir / f"{stem}.png")
        size = target.stat().st_size / 1000
        print(f"  {target}  {width}x{height}  {size:.0f} kB  -- {alt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
