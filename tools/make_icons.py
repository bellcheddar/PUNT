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
#: The same three stops as a CSS gradient, for the SVG mark. 128deg in CSS runs
#: top-left to bottom-right, which is x1/y1 -> x2/y2 below.
STOPS = [("0%", "#1e73be"), ("52%", "#9b51e0"), ("100%", "#ff2fd0")]


def football(size: float, fill: str, lace: str, tilt: float = -22,
             detail: bool = True) -> str:
    """An American football, as SVG, in a `size` by `size` box.

    Drawn rather than set in a typeface. The icon used to be the wordmark, and
    at 192 px it came out as a bare gradient with no lettering at all: the
    webfont did not arrive before the capture, the fallback did not either, and
    a blank plate is a plausible-looking PNG that nothing checked. The uniform
    image guard at the bottom of this file passed it, because a gradient is not
    uniform. A path has no such dependency, and a ball is legible at 16 px where
    four letters in a rounded square are a smudge.

    The body is two circular arcs on a chord, which is the honest way to get the
    shape: for a chord of 2a and a bulge of b the radius is (a^2 + b^2) / 2b, so
    the two arcs meet at real points rather than the rounded ends an ellipse
    gives. A football with rounded ends reads as an egg.

    `detail=False` drops the laces entirely and is what the 16 and 32 px
    favicons use. This was measured rather than assumed: four treatments were
    rendered at 128 px and downsampled to 16, and every one that kept a lace
    mark came out as a white lens with a dark blob in the middle, which is an
    eye. Thinning the strokes does not help, because the marks are asking for
    about nine pixels between them. A bare silhouette is not ambiguous at that
    size either, but the thing it is ambiguous with is a lozenge rather than an
    eye, and the pointed ends survive the downsample where the laces do not.
    """
    a, b = size * (0.375 if detail else 0.40), size * (0.222 if detail else 0.245)
    r = (a * a + b * b) / (2 * b)
    c = size / 2

    # The laces: a short spine with cross ties, occupying the middle third of
    # the ball and no more. The first attempt ran ties across half the length at
    # the stripes' own weight and came out as a barcode even at 512.
    spine = a * 0.26
    tie = b * 0.30
    ties = "".join(
        f'<line x1="{c + spine * k:.2f}" y1="{c - tie:.2f}" '
        f'x2="{c + spine * k:.2f}" y2="{c + tie:.2f}"/>'
        for k in (-0.78, -0.26, 0.26, 0.78)
    )
    # The two end stripes, thinner than the laces and set well inside the points
    # so they do not collide with the taper.
    stripes = "" if not detail else "".join(
        f'<line x1="{c + x:.2f}" y1="{c - b * 0.50:.2f}" '
        f'x2="{c + x:.2f}" y2="{c + b * 0.50:.2f}"/>'
        for x in (-a * 0.60, a * 0.60)
    )
    stripe_group = "" if not stripes else (
        f'<g stroke="{lace}" stroke-width="{size * 0.020:.2f}" '
        f'stroke-linecap="round" opacity="0.8">{stripes}</g>'
    )
    laces = "" if not detail else (
        f'<g stroke="{lace}" stroke-width="{size * 0.026:.2f}" stroke-linecap="round">'
        f'<line x1="{c - spine:.2f}" y1="{c:.2f}" x2="{c + spine:.2f}" y2="{c:.2f}"/>'
        f'{ties}</g>'
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">
  <g transform="rotate({tilt} {c} {c})">
    <path d="M {c - a:.2f} {c:.2f} A {r:.2f} {r:.2f} 0 0 1 {c + a:.2f} {c:.2f} A {r:.2f} {r:.2f} 0 0 1 {c - a:.2f} {c:.2f} Z"
          fill="{fill}"/>
    {laces}
    {stripe_group}
  </g>
</svg>'''


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

#: The browser-tab icons, which are a different problem from the installed-app
#: ones: they are looked at from across a tab strip at 16 or 32 px, not from a
#: home screen at 180. They use the plain silhouette (see `football`) and a
#: tighter corner radius, because a 16 px tile with a 22% radius is a circle.
FAVICONS = [("favicon-32.png", 32), ("favicon-16.png", 16)]

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


def icon_html(size: int, maskable: bool, detail: bool = True,
              radius_fraction: float = 0.22) -> str:
    """One icon: the ball, in white, on the brand gradient.

    No stylesheet link and no webfont. The previous version set the wordmark in
    Anton loaded over HTTP from the running app, which is why this tool needed a
    server at all, and why `icon-192.png` shipped as a plain gradient with no
    lettering: at that size the capture finished before the font did. Nothing
    here is fetched, so nothing here can arrive late.

    A maskable icon keeps everything meaningful inside the central 80% circle,
    because Android crops it to whatever shape the launcher uses. The ball is
    scaled rather than inset so it stays centred in the crop.
    """
    scale = 0.80 if maskable else 1.0
    box = size * scale
    offset = (size - box) / 2
    radius = 0 if maskable else size * radius_fraction
    return f"""<!DOCTYPE html><meta charset="utf-8">
<style>
  /* The plate is sized explicitly rather than with `inset:0`. An absolutely
     positioned box with no positioned ancestor resolves against the initial
     containing block, which is the CHROME WINDOW, and the window is floored at
     520 px wide by the renderer: so a 32 px icon painted a 520 px gradient and
     the capture cropped its top-left corner. The favicons came out flat blue,
     which looks like a deliberate choice rather than a bug, and the large icons
     were fine because 512 is about the window size anyway. */
  html,body {{ margin:0; width:{size}px; height:{size}px; background:{BG};
               position:relative; overflow:hidden; }}
  .plate {{ position:absolute; left:0; top:0; width:{size}px; height:{size}px;
            background:{GRADIENT}; border-radius:{radius}px; }}
  .mark {{ position:absolute; left:{offset}px; top:{offset}px;
           width:{box}px; height:{box}px; }}
  svg {{ display:block; width:100%; height:100%; }}
</style>
<div class="plate"></div>
<div class="mark">{football(box, "#ffffff", BG, detail=detail)}</div>"""


def favicon_svg() -> str:
    """The tab icon as vector: the same plate and the same ball, self-contained.

    A browser given both an SVG and PNGs picks the SVG, so this is the one most
    people actually see. The gradient is repeated here rather than referenced,
    because a favicon that depends on anything else is a favicon that sometimes
    does not draw.
    """
    size = 64
    stops = "".join(f'<stop offset="{at}" stop-color="{colour}"/>' for at, colour in STOPS)
    ball = football(size, "#ffffff", BG, detail=False)
    inner = ball.split(">", 1)[1].rsplit("</svg>", 1)[0]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">{stops}</linearGradient></defs>'
        f'<rect width="{size}" height="{size}" rx="{size * 0.14:.1f}" fill="url(#g)"/>'
        f'{inner}</svg>\n'
    )


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
        render(icon_html(size, maskable), size, size, target)
        print(f"  {target.relative_to(ROOT)}  {size}x{size}  {target.stat().st_size / 1000:.0f} kB")

    for name, size in FAVICONS:
        target = ICON_DIR / name
        render(icon_html(size, False, detail=False, radius_fraction=0.14),
               size, size, target)
        print(f"  {target.relative_to(ROOT)}  {size}x{size}  {target.stat().st_size / 1000:.1f} kB")

    # An SVG favicon, which every current browser prefers over the PNGs and
    # which is the one that stays sharp on a 4K display. Written rather than
    # captured: it is already vector, and rendering it to pixels and back would
    # be the only lossy step in the chain.
    svg = ICON_DIR / "favicon.svg"
    svg.write_text(favicon_svg(), encoding="utf-8")
    print(f"  {svg.relative_to(ROOT)}  vector  {svg.stat().st_size / 1000:.1f} kB")

    total = 0
    for width, height in SPLASHES:
        target = SPLASH_DIR / f"splash-{width}x{height}.png"
        render(splash_html(args.base, width, height), width, height, target)
        total += target.stat().st_size
    print(f"  {len(SPLASHES)} splash screens, {total / 1000:.0f} kB total")

    # A capture that failed still writes a plausible PNG of the right size.
    #
    # "Not uniform" is not enough, and this is the check that let the real bug
    # through: `icon-192.png` shipped for a week as the brand gradient with no
    # mark on it at all, because the wordmark's webfont did not arrive before
    # the capture. A gradient has plenty of extrema, so the guard passed. The
    # mark is white and nothing else in the frame is, so the test is whether any
    # white survived.
    # The two thresholds are different because the two marks are different, and
    # getting that wrong is how this check first failed: the icons' ball is
    # white, but the splash screens' wordmark is the gradient CLIPPED TO THE
    # TEXT on the dark ground, so its brightest pixel measures 127 and a
    # white-pixel test condemns every one of them. A single threshold is only
    # possible if both marks are drawn the same way, and they are not.
    from PIL import Image

    checks = [(ICON_DIR, 240, 0.02), (SPLASH_DIR, 100, 0.005)]
    failed = False
    for directory, floor, share in checks:
        for path in sorted(directory.glob("*.png")):
            grey = Image.open(path).convert("L")
            low, high = grey.getextrema()
            if low == high:
                print(f"FAIL: {path.name} is a uniform image", file=sys.stderr)
                failed = True
                continue
            lit = sum(count for value, count in enumerate(grey.histogram()) if value >= floor)
            if lit < grey.width * grey.height * share:
                print(f"FAIL: {path.name} has no mark on it "
                      f"({lit} pixels at or above {floor}, wanted "
                      f"{int(grey.width * grey.height * share)})", file=sys.stderr)
                failed = True
    if failed:
        return 1
    print("  every capture carries a mark")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
