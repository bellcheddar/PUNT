"""Contrast, as arithmetic.

The palette is declared in one place and which colour sits on which ground is a
fact about the templates, so this needs no browser and belongs in the suite. The
parts that do need one -- focus rings, touch targets, live regions -- live in
`tools/a11y.py --page`.

This found three real failures the first time it ran, all of them `--ink-faint`
at 3.51:1 on the raised panel ground: sub-lines, table headers, captions, and the
Cheer tab's reasons, which are that tab's entire content.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def a11y():
    spec = importlib.util.spec_from_file_location("punt_a11y", ROOT / "tools" / "a11y.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_declared_pair_meets_wcag_aa(a11y):
    css = (ROOT / "static" / "css" / "theme.css").read_text("utf-8")
    palette = a11y.parse_palette(css)
    assert palette, "could not parse the palette"

    failures = []
    for foreground, background, size, where in a11y.PAIRS:
        ratio = a11y.contrast(palette[foreground], palette[background])
        needed = a11y.AA_LARGE if size == "large" else a11y.AA_NORMAL
        if ratio < needed:
            failures.append(f"{foreground} on {background} is {ratio:.2f}:1, needs {needed} ({where})")
    assert not failures, "\n".join(failures)


def test_the_contrast_maths_is_right(a11y):
    """Checked against the two ratios everybody knows, because a contrast checker
    that is quietly wrong passes everything."""
    assert a11y.contrast("#ffffff", "#000000") == pytest.approx(21.0, abs=0.01)
    assert a11y.contrast("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)
    # The canonical AA boundary: #767676 on white is 4.54:1.
    assert a11y.contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.02)


def test_leading_is_not_signalled_by_colour_alone():
    """Roughly one man in twelve cannot separate the green score from the white
    one, and this is an app for a room full of them. The leading side carries a
    shape and an accessible label as well."""
    css = (ROOT / "static" / "css" / "theme.css").read_text("utf-8")
    assert ".mside--leading .mside-score b::before" in css
    assert "25B2" in css.upper(), "no non-colour marker on the leading score"

    markup = (ROOT / "templates" / "partials" / "matchup.html").read_text("utf-8")
    assert "leading" in markup and "aria-label" in markup


def test_there_is_somewhere_for_a_screen_reader_to_hear_the_afternoon():
    """Scores update silently under htmx. Without a live region a reader hears
    nothing happen for four hours."""
    base = (ROOT / "templates" / "base.html").read_text("utf-8")
    assert 'aria-live="polite"' in base
    assert "data-announce" in base

    app_js = (ROOT / "static" / "js" / "app.js").read_text("utf-8")
    assert "ANNOUNCE_MAGNITUDE" in app_js, "every Moment announced is a second problem, not a fix"


def test_focus_is_visible_and_only_for_keyboards():
    """The browser default is a 1px `auto` outline, which on a near-black page is
    close to invisible. `:focus-visible` rather than `:focus`, so a tap does not
    leave a ring behind."""
    css = (ROOT / "static" / "css" / "theme.css").read_text("utf-8")
    assert ":focus-visible" in css
    assert ":focus " not in css.replace(":focus-visible", ""), "a bare :focus rule would ring on tap"
