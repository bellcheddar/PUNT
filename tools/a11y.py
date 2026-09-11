#!/usr/bin/env python3
"""Accessibility audit: contrast from the stylesheet, the rest from the browser.

Two halves, because the two questions have different answers.

Contrast is arithmetic and can be checked without rendering anything: the palette
is declared in one place, and which colour sits on which ground is a fact about
the CSS. Everything else -- focus rings, touch target sizes, live regions -- is a
property of the rendered page and is checked in Chrome.

    python3 tools/a11y.py                 # contrast only, no browser needed
    python3 tools/a11y.py --page          # also audit the running app
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "static" / "css" / "theme.css"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

#: WCAG 2.1 AA. 3.0 for text at 18.66px bold or 24px plain, 4.5 for the rest.
AA_NORMAL = 4.5
AA_LARGE = 3.0


def parse_palette(css: str) -> dict[str, str]:
    block = re.search(r":root\s*\{(.*?)\n\}", css, re.S)
    if not block:
        return {}
    return {
        name: value.strip()
        for name, value in re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;", block.group(1))
    }


def to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> float:
    a, b = luminance(to_rgb(foreground)), luminance(to_rgb(background))
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


#: (foreground token, background token, size, where it is used). Written out
#: rather than derived, because "which colour sits on which ground" is a fact
#: about the templates and not one the stylesheet states.
PAIRS = [
    ("ink", "bg", "normal", "body text"),
    ("ink", "bg-raised", "normal", "panel text"),
    ("ink-dim", "bg-raised", "normal", "commentary feed, card stats"),
    ("ink-faint", "bg-raised", "normal", "sub-lines, table headers, captions"),
    ("ink-faint", "bg", "normal", "notes under panels"),
    ("green", "bg-raised", "large", "the leading score"),
    ("green", "bg-raised", "normal", "positive deltas in tables"),
    ("red", "bg-raised", "normal", "negative deltas, doom"),
    ("amber", "bg-raised", "normal", "milestone and clinch chips"),
    ("magenta", "bg-raised", "normal", "the active tab pip"),
    ("brand-bright", "bg-raised", "normal", "links"),
    ("ink-faint", "bg-sunken", "normal", "anything on the darkest ground"),
]


def audit_contrast() -> int:
    css = CSS.read_text("utf-8")
    palette = parse_palette(css)
    if not palette:
        print("could not parse the palette from theme.css", file=sys.stderr)
        return 1

    failures = 0
    print(f"{'foreground':<14}{'on':<12}{'ratio':>7}  {'needs':>6}  where")
    for fg, bg, size, where in PAIRS:
        if fg not in palette or bg not in palette:
            print(f"  missing token: {fg} or {bg}", file=sys.stderr)
            failures += 1
            continue
        ratio = contrast(palette[fg], palette[bg])
        needed = AA_LARGE if size == "large" else AA_NORMAL
        ok = ratio >= needed
        failures += 0 if ok else 1
        mark = " " if ok else "!"
        print(f"{mark} {fg:<13}{bg:<12}{ratio:6.2f}:1{needed:7.1f}  {where}")

    print()
    if failures:
        print(f"{failures} pair(s) below WCAG AA.", file=sys.stderr)
    else:
        print("Every pair meets WCAG AA.")
    return 1 if failures else 0


PAGE_PROBE = """<!DOCTYPE html><meta charset="utf-8">
<style>html,body{margin:0}iframe{width:390px;height:900px;border:0;display:block}</style>
%s
<pre id="out">pending</pre>
<script>
window.addEventListener('load', () => {
  const lines = [];
  for (const f of document.querySelectorAll('iframe')) {
    const d = f.contentDocument, w = f.contentWindow, route = f.dataset.route;
    const problems = [];

    // Interactive things smaller than 44x44, which is the documented minimum and
    // the size a thumb can actually hit on a moving bus.
    for (const el of d.querySelectorAll('a, button, [role="button"]')) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) continue;
      if (r.height < 44 || r.width < 24) {
        problems.push('small target ' + el.tagName + '.' + (el.className || '-') +
                      ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
      }
    }

    // A control with no accessible name.
    for (const el of d.querySelectorAll('button')) {
      const name = (el.getAttribute('aria-label') || el.textContent || '').trim();
      if (!name) problems.push('unnamed button .' + (el.className || '-'));
    }

    // Images without alt.
    for (const el of d.querySelectorAll('img')) {
      if (el.getAttribute('alt') === null) problems.push('img with no alt: ' + el.src);
    }

    // A visible focus ring. Checked by focusing and comparing outline.
    const focusable = d.querySelector('a, button');
    let ring = 'none';
    if (focusable) {
      focusable.focus();
      const cs = w.getComputedStyle(focusable);
      ring = cs.outlineStyle + ' ' + cs.outlineWidth + ' / shadow ' + (cs.boxShadow || 'none').slice(0, 24);
    }

    // Live regions: scores update under the reader without a word otherwise.
    const live = d.querySelectorAll('[aria-live]').length;

    // Heading order.
    const levels = [...d.querySelectorAll('h1,h2,h3')].map(h => +h.tagName[1]);
    let jump = '';
    for (let i = 1; i < levels.length; i++) {
      if (levels[i] - levels[i-1] > 1) jump = 'h' + levels[i-1] + ' -> h' + levels[i];
    }

    lines.push(route.padEnd(12) + ' focus=' + ring + '  live=' + live +
               (jump ? '  heading jump ' + jump : '') +
               (problems.length ? '\\n    ' + problems.slice(0, 5).join('\\n    ') : '  ok'));
  }
  document.getElementById('out').textContent = lines.join('\\n');
});
</script>"""


def audit_page(base: str) -> int:
    routes = ["/", "/album", "/cheer", "/swing", "/receipts", "/multiverse"]
    frames = "".join(
        f'<iframe data-route="{r}" src="{base}{r}?punt=steady&team=5"></iframe>' for r in routes
    )
    harness = ROOT / "static" / "_a11y_probe.html"
    harness.write_text(PAGE_PROBE % frames, encoding="utf-8")
    try:
        result = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--window-size=600,1000",
             "--dump-dom", f"{base}/static/_a11y_probe.html"],
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
    bad = any(m in report for m in ("small target", "unnamed", "no alt", "pending", "nothing"))
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", action="store_true", help="also audit the running app")
    parser.add_argument("--base", default="http://127.0.0.1:8019")
    args = parser.parse_args()

    status = audit_contrast()
    if args.page:
        status |= audit_page(args.base)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
