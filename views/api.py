"""JSON and htmx-fragment routes.

Fragments are deliberately the same Jinja partials the full page renders, so
first paint and every 30 s refresh afterwards go through one code path. A
fragment that diverges from its page is the classic way a live-updating app
develops a state only reachable by waiting.
"""

from __future__ import annotations

import json
import time

from flask import Blueprint, Response, jsonify, render_template, request

from views.state import snapshot, state
from views.viewmodels import (
    album_view,
    cheer_view,
    game_detail,
    matchup_view,
    moment_detail,
    moments_view,
    odds_detail,
    regret_detail,
    trouble_detail,
    watch_now,
)

bp = Blueprint("api", __name__)


@bp.route("/healthz")
def healthz():
    """Deploy checks read this. Deliberately does not touch ESPN: a health check
    that fails when a third party is down turns their outage into ours."""
    st = state()
    return jsonify({"ok": True, "mode": st.mode, "season": st.cfg.season})


@bp.route("/api/state")
def api_state():
    snap = snapshot()
    return jsonify(
        {
            "season": snap.season,
            "scoring_period": snap.scoring_period,
            "league": snap.settings.name,
            "captured_at": snap.captured_at,
            "stale": snap.stale,
            "problems": snap.all_problems()[:20],
            "matchups": matchup_view(snap),
            "album": album_view(snap, state().live),
            "moments": moments_view(state().live, snap=snap),
            "diagnostics": state().diagnostics(),
        }
    )


@bp.route("/partials/matchup/<int:matchup_id>")
def partial_matchup(matchup_id: int):
    snap = snapshot()
    for view in matchup_view(snap):
        if view["id"] == matchup_id:
            return render_template("partials/matchup.html", m=view, snap=snap)
    return render_template("partials/missing.html", what=f"matchup {matchup_id}"), 404


@bp.route("/partials/album")
def partial_album():
    snap = snapshot()
    return render_template("partials/album_grid.html",
                           album=album_view(snap, state().live), snap=snap)


@bp.route("/partials/scorebar")
def partial_scorebar():
    snap = snapshot()
    return render_template("partials/scorebar.html", matchups=matchup_view(snap), snap=snap)


@bp.route("/stream")
def stream():
    """Server-Sent Events: Moments, pushed the instant the poller detects them.

    Polling handles scores; this exists so the horn fires when the room sees the
    play rather than up to thirty seconds later. One-directional, so no websocket
    stack, and every listener is fed from the single background poller rather
    than starting one of its own.
    """
    st = state()
    live = st.start_live()
    interval = max(5.0, st.cfg.poll_seconds / 2)

    def events():
        # Tells EventSource to wait this long before reconnecting, which stops a
        # dropped bar wifi connection from becoming a reconnect storm.
        yield f"retry: {int(interval * 1000)}\n\n"
        yield f"event: hello\ndata: {json.dumps({'mode': st.mode, 'ts': time.time()})}\n\n"
        for payload in live.listen():
            body = payload["data"]
            if payload.get("replayed"):
                # Carried into the payload rather than left as a sibling key: the
                # client only ever sees `data`, and a backlog moment that looks
                # live fires a horn for a touchdown from forty minutes ago.
                body = {**body, "replayed": True}
            yield f"event: {payload['event']}\ndata: {json.dumps(body)}\n\n"

    return Response(
        events(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx buffers proxied responses by default, which holds an SSE
            # frame until the buffer fills -- i.e. forever, on a low-rate stream.
            "X-Accel-Buffering": "no",
        },
    )


@bp.route("/partials/cheer")
def partial_cheer():
    from views.tabs import _team_param  # noqa: PLC0415

    snap = snapshot()
    return render_template("partials/cheer.html", cheer=cheer_view(snap, _team_param()), snap=snap)


@bp.route("/partials/team/<int:team_id>")
def partial_team(team_id: int):
    """One manager's afternoon, for the detail sheet.

    Server-rendered, so the sheet shows the same numbers as the page it was
    opened from. Assembling it in JavaScript from what happened to be in the DOM
    would give a second opinion, and two numbers for the same thing on one
    screen is worse than one number nobody can see.
    """
    snap = snapshot()
    cards = album_view(snap, state().live)
    card = next((c for c in cards if c["id"] == team_id), None)
    return render_template("partials/team.html", card=card, snap=snap)


# --------------------------------------------------------------------------
# the detail sheets, one kind per panel
#
# Separate routes rather than one parameterised one: each answers a different
# question and needs different data, and a single endpoint switching on a `kind`
# argument would be five functions sharing a signature for no benefit.
# --------------------------------------------------------------------------

@bp.route("/partials/detail/regret/<int:team_id>")
def detail_regret(team_id: int):
    snap = snapshot()
    return render_template("partials/detail_regret.html",
                           d=regret_detail(snap, team_id), snap=snap)


@bp.route("/partials/detail/trouble/<int:team_id>")
def detail_trouble(team_id: int):
    snap = snapshot()
    return render_template("partials/detail_trouble.html",
                           d=trouble_detail(snap, team_id), snap=snap)


@bp.route("/partials/detail/moment/<moment_id>")
def detail_moment(moment_id: str):
    snap = snapshot()
    return render_template("partials/detail_moment.html",
                           d=moment_detail(state().live, moment_id, snap=snap), snap=snap)


@bp.route("/partials/detail/game/<int:pro_team_id>")
def detail_game(pro_team_id: int):
    snap = snapshot()
    return render_template("partials/detail_game.html",
                           d=game_detail(snap, pro_team_id), snap=snap)


@bp.route("/partials/detail/odds/<int:team_id>")
def detail_odds(team_id: int):
    snap = snapshot()
    return render_template("partials/detail_odds.html",
                           d=odds_detail(snap, team_id), snap=snap)


@bp.route("/partials/watchnow")
def partial_watchnow():
    snap = snapshot()
    return render_template("partials/watchnow.html", watch=watch_now(snap), snap=snap)


@bp.route("/api/recap")
def api_recap():
    from engine.recap import generate, pick_backend, validate  # noqa: PLC0415

    st = state()
    pack = st.live.factpack() if st.live else None
    if pack is None:
        return jsonify({"ok": False, "error": "no week yet"}), 404
    recap = generate(pack, backend=pick_backend(st.cfg))
    return jsonify({
        "ok": True,
        "week": pack.week,
        "recap": recap.to_json(),
        "validates": not validate(recap.text, pack),
        "facts": pack.to_json(),
    })


@bp.route("/partials/recap")
def partial_recap():
    from engine.recap import generate, pick_backend  # noqa: PLC0415

    st = state()
    pack = st.live.factpack() if st.live else None
    recap = generate(pack, backend=pick_backend(st.cfg)) if pack else None
    return render_template("partials/recap.html", recap=recap, pack=pack)


@bp.route("/partials/moments")
def partial_moments():
    """The commentary feed as a fragment.

    A fallback for a phone whose SSE connection has dropped: the feed keeps
    filling on the 30 s poll rather than going silent until a reload."""
    return render_template("partials/moments.html",
                           moments=moments_view(state().live, snap=snapshot()))


@bp.route("/api/diagnostics")
def diagnostics():
    """Cache hit rate, backoff state, replay position. Never a cookie value."""
    return jsonify(state().diagnostics())
