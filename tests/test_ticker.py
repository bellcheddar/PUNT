"""The LATEST strip: the differ, and the two traps in animating it.

The Moment engine answers "did something happen". This answers "what has moved",
which is a different question: nobody's win probability falls twelve points in
one play, it falls twelve points over twenty minutes of the other bloke's
running back grinding out first downs, and there is no Moment anywhere in that.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

from engine.ticker import (
    BENCH_DELTA,
    PER_POLL,
    SCORE_DELTA,
    WIN_DELTA,
    Ticker,
)

ROOT = Path(__file__).resolve().parent.parent


def _snap(repo):
    return repo.snapshot()


def _code(text: str) -> str:
    """Source with the comments taken out.

    Both of the checks below first failed on the prose explaining them: the CSS
    comment saying "not an infinite keyframes loop" contains the word infinite,
    and the JS comment saying "a poll swaps the strip's innerHTML" contains
    innerHTML. A grep over source text is a grep over the documentation too.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def test_the_first_poll_says_nothing(repo, no_network):
    """A fresh process sees every number change from nothing.

    Announcing that would open the afternoon with forty lines about things that
    happened before anybody was watching, and it would do it again after every
    deploy.
    """
    ticker = Ticker()
    assert ticker.observe(_snap(repo)) == []


def test_a_score_moving_makes_a_line(repo, no_network):
    ticker = Ticker()
    snap = _snap(repo)
    ticker.observe(snap)                       # learn the state
    later = copy.deepcopy(snap)
    side = (later.live_matchups or later.matchups)[0].home
    side.total += SCORE_DELTA + 5
    changes = ticker.observe(later)
    assert any(c.kind == "SCORE" and c.good for c in changes), [c.kind for c in changes]


def test_a_move_under_the_threshold_says_nothing(repo, no_network):
    """The strip shows one line at a time. Anything firing more often than it
    can rotate is not informing the room, it is evicting the line the room was
    reading."""
    ticker = Ticker()
    snap = _snap(repo)
    ticker.observe(snap)
    later = copy.deepcopy(snap)
    (later.live_matchups or later.matchups)[0].home.total += SCORE_DELTA - 0.5
    assert [c for c in ticker.observe(later) if c.kind == "SCORE"] == []


def test_bench_regret_going_up_is_red(repo, no_network):
    """The one number here that is bad when it rises.

    A template that read the sign of the delta would paint this green, which is
    why every Change carries `good` explicitly instead.
    """
    ticker = Ticker()
    snap = _snap(repo)
    ticker.observe(snap)
    later = copy.deepcopy(snap)
    side = (later.live_matchups or later.matchups)[0].home
    for player in side.bench[:1]:
        player.points += BENCH_DELTA + 5
    changes = ticker.observe(later)
    bench = [c for c in changes if c.kind == "BENCH"]
    assert bench, "a benched player scoring is not being reported"
    assert bench[0].good is False, "bench regret rising was painted as good news"


def test_a_rank_improving_is_green_even_though_the_number_falls(repo, no_network):
    """#6 to #2 is up, and is a smaller number. The other direction a naive
    sign test gets backwards."""
    ticker = Ticker()
    snap = _snap(repo)
    ids = [m.home.team_id for m in (snap.live_matchups or snap.matchups)]
    ticker.observe(snap, ranks={tid: 6 for tid in ids})
    changes = ticker.observe(copy.deepcopy(snap), ranks={tid: 2 for tid in ids})
    form = [c for c in changes if c.kind == "FORM"]
    assert form, "an album move is not being reported"
    assert all(c.good for c in form), "moving up the album was painted red"


def test_win_probability_moves_both_ways(repo, no_network):
    ticker = Ticker()
    snap = _snap(repo)
    ids = [m.home.team_id for m in (snap.live_matchups or snap.matchups)]
    ticker.observe(snap, probabilities={tid: 0.50 for tid in ids})
    up = ticker.observe(copy.deepcopy(snap), probabilities={tid: 0.50 + WIN_DELTA * 2 for tid in ids})
    assert any(c.kind == "WIN" and c.good for c in up)
    down = ticker.observe(copy.deepcopy(snap), probabilities={tid: 0.20 for tid in ids})
    assert any(c.kind == "WIN" and c.good is False for c in down)


def test_nothing_repeats(repo, no_network):
    """Ids are hashed from the facts, like Moment ids, so a re-poll that sees
    the same state does not re-announce it."""
    ticker = Ticker()
    snap = _snap(repo)
    ticker.observe(snap)
    later = copy.deepcopy(snap)
    (later.live_matchups or later.matchups)[0].home.total += 20
    first = ticker.observe(later)
    assert first
    assert ticker.observe(copy.deepcopy(later)) == []


def test_a_burst_is_capped(repo, no_network):
    """A Sunday afternoon can produce forty changes in thirty seconds and the
    strip can show about eight in that time. The rest are not a backlog, they
    are noise that pushes the good ones off."""
    ticker = Ticker()
    snap = _snap(repo)
    ticker.observe(snap)
    later = copy.deepcopy(snap)
    for matchup in (later.live_matchups or later.matchups):
        for side in (matchup.home, matchup.away):
            side.total += 40
    assert len(ticker.observe(later)) <= PER_POLL


def test_the_week_rolling_over_empties_it(repo, no_network):
    """A line about last Sunday under this Sunday's scores would be worse than
    no ticker at all."""
    ticker = Ticker()
    snap = _snap(repo)
    ticker.observe(snap)
    later = copy.deepcopy(snap)
    (later.live_matchups or later.matchups)[0].home.total += 20
    ticker.observe(later)
    assert ticker.changes

    next_week = copy.deepcopy(later)
    next_week.scoring_period += 1
    ticker.observe(next_week)
    assert ticker.changes == [], "last week's lines survived the rollover"


def test_the_poller_clears_it_too(repo, no_network):
    """The feed owns the reset, and it has been missed before: three of its six
    per-week stores used to survive a rollover."""
    from engine.live import LiveFeed

    snap = _snap(repo)
    feed = LiveFeed(fetch=lambda: snap, poll_seconds=5)
    feed.poll_once()
    feed.ticker.changes.append(object())
    later = copy.deepcopy(snap)
    later.scoring_period += 1
    feed.fetch = lambda: later
    feed.poll_once()
    assert feed.ticker.changes == []


def test_the_summary_fills_an_empty_strip(repo, no_network):
    """The differ needs two polls before it can say anything, so a fresh
    process, a deploy or anybody opening the page on a Tuesday would otherwise
    get an empty strip where the liveliest thing on the page is meant to be."""
    from views.viewmodels import ticker_view

    rows = ticker_view(None, _snap(repo))
    assert rows, "nothing to show when nothing has moved"
    assert all(r["text"] and r["value"] for r in rows)
    assert all(r["good"] in (True, False) for r in rows), "a summary line has no colour"


# -- the two traps in animating it ------------------------------------------

def test_the_wheel_is_not_an_infinite_css_animation():
    """An infinite `@keyframes` loop stops Chrome's `--virtual-time-budget` from
    ever settling, and `tools/screenshot.py` uses it. The reel is stepped by a
    finite transition from JavaScript for exactly that reason, and a later edit
    that "simplifies" it into a CSS loop would hang every capture in the repo.
    """
    css = (ROOT / "static" / "css" / "theme.css").read_text("utf-8")
    block = css[css.index("/* --- the LATEST wheel"):css.index("/* The panel it lives in")]
    assert "infinite" not in _code(block), "the wheel animates forever; captures will hang"
    assert "transition:" in block, "the reel has no transition to step with"


def test_every_virtual_time_probe_asks_for_a_still_page():
    """`?punt=steady` is what stops the reel, so a probe that forgets it hangs.

    Found the hard way: a plain `--dump-dom --virtual-time-budget` against `/`
    never returned. The capture path was fine only because it happens to pass
    steady already, which is luck rather than design until something checks it.
    """
    for name in ("screenshot.py", "a11y.py", "perf.py"):
        source = (ROOT / "tools" / name).read_text("utf-8")
        if "virtual-time-budget" not in source:
            continue
        assert "steady" in source, (
            f"tools/{name} drives Chrome with virtual time and never asks for a "
            f"still page: the ticker will keep it spinning and it will hang"
        )


def test_the_strip_is_thin():
    """It sits above everything. A tall one pushes the album off the screen,
    which is the thing the page opens with."""
    css = (ROOT / "static" / "css" / "theme.css").read_text("utf-8")
    row = re.search(r"--wheel-row:\s*(\d+)px", css)
    assert row and int(row.group(1)) <= 40, "the LATEST strip has grown"


def test_the_reel_never_writes_markup_from_a_team_name():
    """Every line is assembled from ESPN team and player names, which are
    whatever ten people typed into a web form."""
    js = _code((ROOT / "static" / "js" / "ticker.js").read_text("utf-8"))
    assert "innerHTML" not in js, "the ticker builds DOM from names with innerHTML"
    assert "textContent" in js
