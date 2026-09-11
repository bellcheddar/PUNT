"""The six tabs plus the TV board. Server-rendered; htmx swaps the live parts.

`?tv=1` on any route drops the navigation, scales the type and disables
interaction, so the bar screen is a query parameter rather than a second app.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from views.state import snapshot, state
from views.viewmodels import (
    album_view,
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


@bp.app_context_processor
def inject_chrome():
    """Everything `base.html` needs, computed once per request.

    `tv` is read here rather than in each view so that adding a tab cannot
    accidentally omit TV support.
    """
    st = state()
    return {
        "TABS": TABS,
        "tv": request.args.get("tv") == "1",
        "mode": st.mode,
        "replay": st.replay.describe() if st.replay is not None else None,
        "poll_seconds": st.cfg.poll_seconds,
        "roast_level": st.cfg.roast_level,
    }


@bp.route("/")
def today():
    snap = snapshot()
    live = state().live
    return render_template(
        "tabs/today.html",
        snap=snap,
        matchups=matchup_view(snap),
        moments=moments_view(live),
        swing=swing_view(snap, live),
    )


@bp.route("/album")
def album():
    snap = snapshot()
    return render_template("tabs/album.html", snap=snap, album=album_view(snap))


@bp.route("/cheer")
def cheer():
    snap = snapshot()
    return render_template("tabs/cheer.html", snap=snap, cheer=cheer_view(snap, _team_param()))


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
    snap = snapshot()
    return render_template("tabs/swing.html", snap=snap, swing=swing_view(snap, state().live))


@bp.route("/receipts")
def receipts():
    snap = snapshot()
    return render_template("tabs/receipts.html", snap=snap, receipts=receipts_view(snap))


@bp.route("/multiverse")
def multiverse():
    snap = snapshot()
    return render_template("tabs/multiverse.html", snap=snap, multiverse=multiverse_view(snap))


@bp.route("/big-board")
def big_board():
    """The bar screen. Always TV-shaped regardless of the query parameter."""
    snap = snapshot()
    return render_template("tabs/big_board.html", snap=snap, matchups=matchup_view(snap), force_tv=True)
