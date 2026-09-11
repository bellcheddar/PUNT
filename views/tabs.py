"""The six tabs plus the TV board. Server-rendered; htmx swaps the live parts.

`?tv=1` on any route drops the navigation, scales the type and disables
interaction, so the bar screen is a query parameter rather than a second app.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from views.state import snapshot, state
from views.viewmodels import (
    album_view,
    watch_now,
    cheer_view,
    matchup_view,
    moments_view,
    multiverse_view,
    receipts_view,
    swing_view,
)

bp = Blueprint("tabs", __name__)

#: Order matters: it is the bottom tab bar, left to right, and the swipe order.
TABS = [
    {"endpoint": "tabs.today", "label": "Today", "icon": "today", "path": "/"},
    {"endpoint": "tabs.album", "label": "Album", "icon": "album", "path": "/album"},
    {"endpoint": "tabs.cheer", "label": "Cheer", "icon": "cheer", "path": "/cheer"},
    {"endpoint": "tabs.swing", "label": "Swing", "icon": "swing", "path": "/swing"},
    {"endpoint": "tabs.receipts", "label": "Receipts", "icon": "receipts", "path": "/receipts"},
]

#: Multiverse folds under Receipts rather than becoming a sixth tab: the spec
#: caps the bar at five, and five is already the point at which a thumb has to
#: aim.


@bp.app_context_processor
def inject_chrome():
    """Everything `base.html` needs, computed once per request.

    `tv` is read here rather than in each view so that adding a tab cannot
    accidentally omit TV support.
    """
    st = state()
    live = snapshot()
    return {
        "TABS": TABS,
        "tv": request.args.get("tv") == "1",
        "mode": st.mode,
        "replay": st.replay.describe() if st.replay is not None else None,
        "poll_seconds": st.cfg.poll_seconds,
        "roast_level": st.cfg.roast_level,
        "chosen_week": _week_param(),
        # From the live snapshot, not the page's. A page showing week 9 still
        # needs to know that week 11 is the one in progress, or the menu cannot
        # mark it. This is a cache hit: every route has already fetched it.
        "week_menu": st.weeks(live),
        # Which week is actually in progress, so a page rendering an older one
        # can say so. Not `snap.scoring_period`, which on an archive page is the
        # archived week and would make the test always false.
        "live_week": live.scoring_period,
    }


@bp.route("/")
def today():
    """Everything, on one page.

    It used to be five tabs. They were plain links, so every tab change was a
    new document -- and audio needs a user gesture per document, which meant the
    tap that changed tabs was also the tap that unlocked the sound. The bed
    began its 1400 ms fade-in and the browser tore the document down before it
    finished. The music could only be heard in the gap between the tap and the
    page changing, which is exactly what it sounded like.

    One document fixes that by construction, and the panels turn out to fit
    beside each other anyway.
    """
    snap = snapshot(_week_param())
    live = state().live
    return render_template(
        "tabs/home.html",
        snap=snap,
        matchups=matchup_view(snap),
        moments=moments_view(live, snap=snap),
        swing=swing_view(snap, live),
        album=album_view(snap, live),
        cheer=cheer_view(snap, _team_param()),
        receipts=receipts_view(snap),
        multiverse=multiverse_view(snap),
    )


@bp.route("/album")
def album():
    snap = snapshot(_week_param())
    return render_template("tabs/album.html", snap=snap, album=album_view(snap, state().live))


@bp.route("/cheer")
def cheer():
    snap = snapshot(_week_param())
    return render_template("tabs/cheer.html", snap=snap, cheer=cheer_view(snap, _team_param()))


def _week_param() -> int | None:
    """Which week the page is being asked for, or None for whatever is live.

    None rather than the current number on purpose: the difference between
    "week 11" and "this week, which happens to be 11" is the whole point of the
    menu. A page pinned to 11 by a bookmark would still be showing week 11 in
    December; a page with no week in the URL follows the season on its own.
    """
    raw = request.args.get("week", "")
    try:
        week = int(raw)
    except (TypeError, ValueError):
        return None
    # ESPN numbers scoring periods from 1, and an 18 game season plus playoffs
    # does not reach 30. A silly number is a typed URL, not a week.
    return week if 1 <= week <= 30 else None


def _team_param() -> int | None:
    """Which team the asking phone belongs to.

    Passed as a query parameter rather than read from a session, because there
    are no sessions and no accounts: the phone remembers, and tells the server
    when it needs a personalised answer."""
    raw = request.args.get("team", "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


@bp.route("/swing")
def swing():
    snap = snapshot(_week_param())
    return render_template("tabs/swing.html", snap=snap, swing=swing_view(snap, state().live))


@bp.route("/receipts")
def receipts():
    snap = snapshot(_week_param())
    return render_template("tabs/receipts.html", snap=snap, receipts=receipts_view(snap))


@bp.route("/multiverse")
def multiverse():
    snap = snapshot(_week_param())
    return render_template("tabs/multiverse.html", snap=snap, multiverse=multiverse_view(snap))


@bp.route("/big-board")
def big_board():
    """The bar screen. Always TV-shaped regardless of the query parameter."""
    snap = snapshot(_week_param())
    return render_template("tabs/big_board.html", snap=snap,
                           matchups=matchup_view(snap), watch=watch_now(snap), force_tv=True)
