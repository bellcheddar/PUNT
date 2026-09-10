#!/usr/bin/env python3
"""Generate the PWA icons and the iOS splash screens.

Rendered in headless Chrome against the app's own self-hosted webfont rather
than drawn with PIL. PIL cannot read woff2, and fetching a TTF of Anton just for
this returned an Embedded OpenType file that FreeType refuses. Rendering in the
browser also guarantees the icon's wordmark is typographically identical to the
one in the header, which a hand-drawn approximation would not be.

    python3 tools/make_icons.py            # needs the app running on --base

Output is committed: this runs once, or when the wordmark changes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "static" / "icons"
SPLASH_DIR = ROOT / "static" / "splash"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

BG = "#0d1017"
GRADIENT = "linear-gradient(128deg, #1e73be 0%, #9b51e0 52%, #ff2fd0 100%)"

#: (filename, px, maskable). A maskable icon must keep everything meaningful
#: inside the central 80% circle, because Android crops it to whatever shape the
#: launcher uses. An icon that only ships a square version gets letterboxed on a
#: white plate, which looks like a bug.
ICONS = [
    ("icon-192.png", 192, False),
    ("icon-512.png", 512, False),
    ("icon-192-maskable.png", 192, True),
    ("icon-512-maskable.png", 512, True),
    ("apple-touch-icon.png", 180, False),
]

#: iOS ignores the manifest and needs a splash image per device resolution, in
#: device pixels, selected by a media query. Without them an installed app opens
#: on a white flash, which is the single most obvious tell that it is a web page.
#: These cover every iPhone still running a current iOS.
SPLASHES = [
    (1320, 2868), (1206, 2622), (1290, 2796), (1179, 2556),
    (1284, 2778), (1170, 2532), (1125, 2436), (828, 1792), (750, 1334),
]


def render(html: str, width: int, height: int, out: Path, scale: int = 1) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, dir=ROOT / "static") as handle:
        handle.write(html)
        path = Path(handle.name)
    try:
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             f"--force-device-scale-factor={scale}",
             f"--window-size={max(520, width)},{max(400, height)}",
             "--default-background-color=00000000",
             "--virtual-time-budget=4000",
             f"--screenshot={out}", f"file://{path}"],
            check=True, capture_output=True, timeout=60,
        )
    finally:
        path.unlink(missing_ok=True)

    from PIL import Image

    image = Image.open(out).crop((0, 0, width * scale, height * scale))
    # Flattened to RGB deliberately: an alpha channel is rejected wherever these
    # get reused as store assets, and PIL hands back RGBA by default.
    Image.alpha_composite(
        Image.new("RGBA", image.size, (13, 16, 23, 255)), image.convert("RGBA")
    ).convert("RGB").save(out, optimize=True)


def font_css(base: str) -> str:
    return f'<link rel="stylesheet" href="{base}/static/css/fonts.css">'


def icon_html(base: str, size: int, maskable: bool) -> str:
    # 80% safe zone for maskable, full bleed otherwise.
    inset = size * 0.1 if maskable else 0
    radius = 0 if maskable else size * 0.22
    return f"""<!DOCTYPE html><meta charset="utf-8">{font_css(base)}
<style>
  html,body {{ margin:0; width:{size}px; height:{size}px; background:{BG}; }}
  .plate {{ position:absolute; inset:0; background:{GRADIENT};
            border-radius:{radius}px; display:grid; place-items:center; }}
  .mark {{ font-family:Anton, Impact, sans-serif; color:#fff;
           font-size:{(size - inset * 2) * 0.30}px; letter-spacing:{size * 0.012}px;
           line-height:1; text-shadow:0 {size*0.012}px {size*0.03}px rgba(0,0,0,.35); }}
</style>
<div class="plate"><span class="mark">PUNT</span></div>"""


def splash_html(base: str, width: int, height: int) -> str:
    mark = min(width, height) * 0.26
    return f"""<!DOCTYPE html><meta charset="utf-8">{font_css(base)}
<style>
  html,body {{ margin:0; width:{width}px; height:{height}px; background:{BG};
               display:grid; place-items:center; }}
  .mark {{ font-family:Anton, Impact, sans-serif; font-size:{mark}px; line-height:1;
           letter-spacing:{width * 0.008}px;
           background:{GRADIENT}; -webkit-background-clip:text; background-clip:text;
           color:transparent; }}
</style>
<span class="mark">PUNT</span>"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8019")
    args = parser.parse_args()

    ICON_DIR.mkdir(parents=True, exist_ok=True)
    SPLASH_DIR.mkdir(parents=True, exist_ok=True)

    for name, size, maskable in ICONS:
        target = ICON_DIR / name
        render(icon_html(args.base, size, maskable), size, size, target)
        print(f"  {target.relative_to(ROOT)}  {size}x{size}  {target.stat().st_size / 1000:.0f} kB")

    total = 0
    for width, height in SPLASHES:
        target = SPLASH_DIR / f"splash-{width}x{height}.png"
        render(splash_html(args.base, width, height), width, height, target)
        total += target.stat().st_size
    print(f"  {len(SPLASHES)} splash screens, {total / 1000:.0f} kB total")

    # A capture that failed still writes a plausible PNG of the right size, and
    # every pixel in it is the background colour. Check before committing.
    from PIL import Image

    for path in list(ICON_DIR.glob("*.png")) + list(SPLASH_DIR.glob("*.png")):
        low, high = Image.open(path).convert("L").getextrema()
        if low == high:
            print(f"FAIL: {path.name} is a uniform image", file=sys.stderr)
            return 1
    print("  all captures have content")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
