"""The icons, the favicons, and the check that says they are not blank.

`icon-192.png` shipped for a week as the brand gradient with nothing on it. The
wordmark was set in a webfont fetched over HTTP from the running app, and at
that size the capture finished before the font arrived. Every guard passed: the
file was the right size, it was a valid PNG, and it was not a uniform image,
because a gradient is not uniform. The only way to see it was to look at it.

The icons are drawn from a path now, so there is nothing left to arrive late,
and these assert the properties that were false while nobody noticed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "static" / "icons"
SPLASH = ROOT / "static" / "splash"

sys.path.insert(0, str(ROOT / "tools"))

Image = pytest.importorskip("PIL.Image", reason="Pillow is a dev dependency")


def _lit(path: Path, floor: int) -> tuple[float, bool]:
    """The share of pixels at or above `floor` luminance, and whether flat."""
    grey = Image.open(path).convert("L")
    low, high = grey.getextrema()
    lit = sum(count for value, count in enumerate(grey.histogram()) if value >= floor)
    return lit / (grey.width * grey.height), low == high


#: The icons' mark is white. The splash screens' is the gradient clipped to the
#: text, whose brightest pixel measures 127, so one threshold cannot serve both.
MARK = [(ICONS, 240, 0.02), (SPLASH, 100, 0.005)]


@pytest.mark.parametrize("directory,floor,share", MARK, ids=["icons", "splashes"])
def test_every_generated_image_carries_a_mark(directory, floor, share):
    files = sorted(directory.glob("*.png"))
    assert files, f"nothing in {directory}"
    for path in files:
        fraction, flat = _lit(path, floor)
        assert not flat, f"{path.name} is a uniform image"
        assert fraction >= share, (
            f"{path.name} has no mark on it: {fraction:.4%} of pixels at or above "
            f"{floor}, wanted {share:.2%}. This is exactly how the wordmark icon "
            f"shipped blank."
        )


def test_the_guard_catches_a_blank_gradient():
    """A check that has only ever passed is not a check.

    The case that got through was not a flat image, it was a perfectly good
    gradient with the mark missing, so that is the one to reproduce.
    """
    gradient = Image.new("RGB", (192, 192))
    for x in range(192):
        for y in range(192):
            gradient.putpixel((x, y), (30 + x // 3, 40 + y // 8, 190 - y // 4))
    grey = gradient.convert("L")
    low, high = grey.getextrema()
    white = sum(c for v, c in enumerate(grey.histogram()) if v >= 240)
    assert low != high, "the old uniform-image guard would have passed this"
    assert white == 0, "and the white-pixel guard catches it"


def test_the_favicons_are_the_silhouette_and_the_big_ones_are_not():
    """Two drawings on purpose, not an oversight.

    At 16 px the laces collapse into one dark mass in the middle of a white lens
    and the icon reads as an eye. The small sizes therefore drop them. The test
    is that the difference survives: an edit that gives the favicon its laces
    back would look like an improvement in the source and be a regression on
    screen.
    """
    from make_icons import football

    assert "<line" not in football(16, "#fff", "#000", detail=False)
    assert "<line" in football(512, "#fff", "#000")


def test_a_football_is_not_an_ellipse():
    """The body is two circular arcs on a chord, so the ends come to points.

    An ellipse of the same proportions is a much easier thing to write and reads
    as an egg. The radius is the tell: for a chord of 2a and a bulge of b it is
    (a^2 + b^2) / 2b, which is far larger than either semi-axis.
    """
    from make_icons import football

    svg = football(512, "#fff", "#000")
    assert " A " in svg, "the body is not arcs"
    radius = float(svg.split(" A ", 1)[1].split()[0])
    assert radius > 512 * 0.355, "the arc is too tight to give a pointed end"


def test_the_favicon_is_served_from_the_root(client, no_network):
    """Browsers ask for /favicon.ico whether or not the page links to one."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.mimetype == "image/png"


def test_the_page_links_an_svg_icon_first(client, no_network):
    """Every current browser prefers it, and it is the one that stays sharp."""
    head = client.get("/").get_data(as_text=True).split("</head>", 1)[0]
    icons = [line for line in head.splitlines() if 'rel="icon"' in line]
    assert icons, "no tab icon linked at all"
    assert 'type="image/svg+xml"' in icons[0], "the SVG must come first"
    assert any("favicon-32.png" in line for line in icons), "no PNG fallback"
