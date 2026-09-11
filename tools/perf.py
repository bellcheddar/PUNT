#!/usr/bin/env python3
"""Performance budgets, measured rather than asserted.

The build spec sets three: total JS under 150 kB gzipped excluding audio, first
contentful paint under 1.5 s on 4G, and 60 fps on an iPhone 12 with ten cards on
screen.

The first is exact arithmetic and is checked here and in the suite. The second is
estimated from the real transfer size at a 4G rate, which is honest as far as it
goes and no further -- a real measurement needs a real network. The third needs a
real iPhone, but the *work per frame* can be measured here, and if that is over
budget on a desktop it will certainly be over budget on a phone.

    python3 tools/perf.py              # payload only, no browser
    python3 tools/perf.py --page       # also measure the rendered page
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

#: The spec's budget, in bytes, gzipped, excluding audio.
JS_BUDGET = 150_000

#: A conservative 4G figure. Real 4G in a bar is worse, which is the point of
#: having any budget at all.
FOURG_BYTES_PER_SECOND = 1_200_000 / 8
FCP_BUDGET_SECONDS = 1.5

#: One frame at 60 fps. The tilt handler writes two custom properties per card
#: and the rest is composited, so the scripting share has to be a small part of
#: this or ten cards cannot hold the rate.
FRAME_BUDGET_MS = 16.7


def gzipped(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), 9))


def audit_payload() -> int:
    rows: list[tuple[str, int, int]] = []
    for path in sorted(STATIC.rglob("*.js")):
        rows.append((str(path.relative_to(STATIC)), path.stat().st_size, gzipped(path)))

    total = sum(row[2] for row in rows)
    print(f"{'file':<26}{'raw':>10}{'gzipped':>10}")
    for name, raw, gz in rows:
        print(f"  {name:<24}{raw / 1000:9.1f}k{gz / 1000:9.1f}k")
    print(f"  {'TOTAL JS':<24}{sum(r[1] for r in rows) / 1000:9.1f}k{total / 1000:9.1f}k"
          f"   budget {JS_BUDGET / 1000:.0f}k")

    css = sorted(STATIC.rglob("*.css"))
    css_total = sum(gzipped(p) for p in css)
    # Only the faces a page actually fetches. Latin-ext is gated by
    # `unicode-range` and never requested for English content, and a weight
    # nothing selects is never requested either -- so counting the directory
    # overstates the transfer by about a third.
    fetched = ["anton-400-latin.woff2", "inter-400-latin.woff2",
               "inter-600-latin.woff2", "roboto-mono-400-latin.woff2"]
    fonts = [STATIC / "fonts" / name for name in fetched]
    font_total = sum(p.stat().st_size for p in fonts if p.is_file())
    print(f"\n  {'CSS (gzipped)':<24}{css_total / 1000:19.1f}k")
    print(f"  {'fonts actually fetched':<24}{font_total / 1000:19.1f}k  (woff2, already compressed)")

    # First contentful paint is blocked by the stylesheets and nothing else.
    # The scripts are `defer` and the fonts are `display: swap`, so text is drawn
    # in the fallback stack before either arrives. Reporting the total as if it
    # were the FCP path was this tool's own first mistake -- it said 1.87s
    # against a 1.5s budget while the actual blocking path was a twentieth of it.
    blocking = css_total
    total_first_visit = total + css_total + font_total
    print(f"\n  blocking first paint (CSS only)   {blocking / 1000:7.1f} kB"
          f"   {blocking / FOURG_BYTES_PER_SECOND:.2f}s at 1.2 Mbit"
          f"   budget {FCP_BUDGET_SECONDS}s")
    print(f"  deferred (JS, gzipped)            {total / 1000:7.1f} kB")
    print(f"  swapped in (fonts actually used)  {font_total / 1000:7.1f} kB")
    print(f"  whole first visit                 {total_first_visit / 1000:7.1f} kB"
          f"   {total_first_visit / FOURG_BYTES_PER_SECOND:.2f}s")

    over = total > JS_BUDGET
    print()
    if over:
        print(f"OVER BUDGET by {(total - JS_BUDGET) / 1000:.1f} kB", file=sys.stderr)
    else:
        print(f"Within budget, {(JS_BUDGET - total) / 1000:.1f} kB to spare.")
    return 1 if over else 0


PAGE_PROBE = """<!DOCTYPE html><meta charset="utf-8">
<style>html,body{margin:0}iframe{width:390px;height:900px;border:0}</style>
<iframe src="__BASE__/album?punt=steady&team=5"></iframe><pre id="out">pending</pre>
<script>
window.addEventListener('load', () => {
  const f = document.querySelector('iframe');
  const d = f.contentDocument, w = f.contentWindow;
  const lines = [];

  const cards = [...d.querySelectorAll('.card')];
  lines.push('cards on screen: ' + cards.length);

  // The tilt handler's actual work: write two custom properties per card, then
  // force the style recalc and layout that a real frame would do. Measured over
  // many iterations because a single one is noise.
  const ROUNDS = 300;
  const start = w.performance.now();
  for (let i = 0; i < ROUNDS; i++) {
    const x = Math.sin(i / 10), y = Math.cos(i / 10);
    for (const card of cards) {
      card.style.setProperty('--tx', x.toFixed(3));
      card.style.setProperty('--ty', y.toFixed(3));
    }
    void d.body.offsetHeight;      // force the recalc, as a frame would
  }
  const per = (w.performance.now() - start) / ROUNDS;
  lines.push('tilt work per frame: ' + per.toFixed(3) + ' ms for ' + cards.length + ' cards');
  lines.push('frame budget: 16.7 ms, so scripting is ' + (per / 16.7 * 100).toFixed(1) + '% of it');

  // Resource count and transfer for the page itself.
  const entries = w.performance.getEntriesByType('resource');
  const bytes = entries.reduce((n, e) => n + (e.transferSize || 0), 0);
  lines.push('subresources: ' + entries.length + ', ' + (bytes / 1000).toFixed(0) + ' kB transferred');
  const slow = entries.filter(e => e.duration > 120)
                      .map(e => new URL(e.name).pathname + ' ' + e.duration.toFixed(0) + 'ms');
  if (slow.length) lines.push('slow: ' + slow.slice(0, 5).join(', '));

  const paint = w.performance.getEntriesByType('paint')
                 .map(p => p.name + ' ' + p.startTime.toFixed(0) + 'ms');
  if (paint.length) lines.push('paint: ' + paint.join(', '));

  document.getElementById('out').textContent = lines.join('\\n');
});
</script>"""


def audit_page(base: str) -> int:
    harness = STATIC / "_perf_probe.html"
    # `.replace`, not `%`: the probe is full of literal percent signs (it prints
    # a share of the frame budget), and `%` formatting chokes on every one.
    harness.write_text(PAGE_PROBE.replace("__BASE__", base), encoding="utf-8")
    try:
        result = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--window-size=600,1000",
             "--dump-dom", f"{base}/static/_perf_probe.html"],
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        print("the page probe timed out", file=sys.stderr)
        return 1
    finally:
        harness.unlink(missing_ok=True)

    import html as html_module

    match = re.search(r'<pre id="out">(.*?)</pre>', result.stdout, re.S)
    report = html_module.unescape(match.group(1)) if match else "(probe produced nothing)"
    print("\n" + report)

    per_frame = re.search(r"tilt work per frame: ([\d.]+) ms", report)
    if per_frame and float(per_frame.group(1)) > FRAME_BUDGET_MS / 2:
        print(f"\nScripting is over half the frame budget on a desktop; a phone will "
              f"not hold 60 fps.", file=sys.stderr)
        return 1
    return 0 if "pending" not in report else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", action="store_true")
    parser.add_argument("--base", default="http://127.0.0.1:8019")
    args = parser.parse_args()

    status = audit_payload()
    if args.page:
        status |= audit_page(args.base)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
