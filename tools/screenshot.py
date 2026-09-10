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
from pathlib import Path

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

#: (filename stem, path, viewport width, viewport height, alt text)
SHOTS = [
    ("today", "/", 390, 844, "The Today tab on a phone: five live matchups with running scores"),
    ("album", "/album", 390, 844, "The Album tab: ten manager cards, one per team, tiered by this week's score"),
    ("receipts", "/receipts", 390, 844, "The Receipts tab: every manager ranked by points left on the bench"),
    ("big-board", "/big-board?tv=1", 1280, 720, "The Big Board in TV mode: the whole slate on the bar screen"),
]


def capture(url: str, width: int, height: int, out: Path, scale: int = 2) -> Path:
    harness = f"""<!DOCTYPE html><meta charset="utf-8">
<style>
  html,body {{ margin:0; padding:0; background:#0d1017; }}
  iframe {{ width:{width}px; height:{height}px; border:0; display:block; }}
</style>
<iframe src="{url}"></iframe>"""

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as handle:
        handle.write(harness)
        harness_path = handle.name

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8019")
    parser.add_argument("--out", default="docs/screenshots")
    parser.add_argument("--url", help="capture a single URL instead of the standard set")
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.url:
        path = capture(args.url, args.width, args.height, out_dir / "capture.png")
        print(f"  {path}")
        return 0

    for stem, path, width, height, alt in SHOTS:
        target = capture(f"{args.base}{path}", width, height, out_dir / f"{stem}.png")
        size = target.stat().st_size / 1000
        print(f"  {target}  {width}x{height}  {size:.0f} kB  -- {alt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
