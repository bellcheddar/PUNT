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
from views.viewmodels import album_view, matchup_view, moments_view

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
            "album": album_view(snap),
            "moments": moments_view(state().live),
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
    return render_template("partials/album_grid.html", album=album_view(snap), snap=snap)


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
            yield f"event: {payload['event']}\ndata: {json.dumps(payload['data'])}\n\n"

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


@bp.route("/partials/moments")
def partial_moments():
    """The commentary feed as a fragment.

    A fallback for a phone whose SSE connection has dropped: the feed keeps
    filling on the 30 s poll rather than going silent until a reload."""
    return render_template("partials/moments.html", moments=moments_view(state().live))


@bp.route("/api/diagnostics")
def diagnostics():
    """Cache hit rate, backoff state, replay position. Never a cookie value."""
    return jsonify(state().diagnostics())
