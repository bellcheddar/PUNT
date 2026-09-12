"""JSON and htmx-fragment routes.

Fragments are deliberately the same Jinja partials the full page renders, so
first paint and every 30 s refresh afterwards go through one code path. A
fragment that diverges from its page is the classic way a live-updating app
develops a state only reachable by waiting.
"""

from __future__ import annotations

import json
import time

from flask import Blueprint, Response, abort, jsonify, render_template, request

from views.state import snapshot, state
from views.tabs import _team_param, _week_param
from views.viewmodels import (
    FORM_WEIGHTS,
    album_view,
    allplay_view,
    cheer_view,
    game_detail,
    matchup_view,
    moment_detail,
    moments_view,
    multiverse_view,
    odds_detail,
    receipts_view,
    regret_detail,
    clock_view,
    gauntlet_view,
    ledger_view,
    seeds_view,
    shape_view,
    swap_view,
    volatility_view,
    change_detail,
    clock_detail,
    gauntlet_detail,
    grid_detail,
    ledger_detail,
    seeds_detail,
    shape_detail,
    swap_detail,
    volatility_detail,
    stored_moments,
    ticker_view,
    swing_view,
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
    snap = snapshot(_week_param())
    return render_template("partials/album_grid.html",
                           album=album_view(snap, state().live), snap=snap)


@bp.route("/partials/scorebar")
def partial_scorebar():
    snap = snapshot(_week_param())
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
    snap = snapshot(_week_param())
    return render_template("partials/cheer.html", cheer=cheer_view(snap, _team_param()), snap=snap)


#: The panels that poll, and the view model each one needs.
#:
#: One route rather than six, because the six differ only in which view model
#: they build and which template they render. Three of these panels used to have
#: no route at all: bench regret, playoff odds and who is in trouble were
#: rendered once when the page loaded and never again, so a phone left on the
#: bar showed two o'clock's numbers at five and nothing on screen said so. They
#: were correct on arrival, which is why nobody caught it.
PANELS = {
    "ticker": lambda snap, live: {"ticker": ticker_view(live, snap)},
    "regret": lambda snap, live: {"receipts": receipts_view(snap)},
    "trouble": lambda snap, live: {"swing": swing_view(snap, live)},
    "odds": lambda snap, live: {"multiverse": multiverse_view(snap)},
    "swings": lambda snap, live: {"swing": swing_view(snap, live)},
    "allplay": lambda snap, live: {"receipts": receipts_view(snap)},
    "luck": lambda snap, live: {"multiverse": multiverse_view(snap)},
    # The season panels. Each answers something the week's own numbers cannot,
    # and each is one view model, so adding a ninth is one line here and one
    # template rather than a route.
    "shape": lambda snap, live: {"shape": shape_view(snap, state().store())},
    "grid": lambda snap, live: {"grid": allplay_view(snap)},
    "seeds": lambda snap, live: {"seeds": seeds_view(snap)},
    "gauntlet": lambda snap, live: {"gauntlet": gauntlet_view(snap)},
    "clock": lambda snap, live: {"clock": clock_view(snap)},
    "ledger": lambda snap, live: {"ledger": ledger_view(snap)},
    "volatility": lambda snap, live: {"volatility": volatility_view(snap)},
    "swap": lambda snap, live: {"swap": swap_view(snap)},
}


#: The detail sheet behind each season panel. Same shape as `PANELS`, and for
#: the same reason: nine routes that differ only in which view model they build
#: and which template they render is nine copies of one route.
DETAILS = {
    "shape": lambda snap, team_id: shape_detail(snap, team_id, state().store()),
    "grid": grid_detail,
    "seeds": seeds_detail,
    "gauntlet": gauntlet_detail,
    "clock": clock_detail,
    "ledger": ledger_detail,
    "volatility": volatility_detail,
    "swap": swap_detail,
}


@bp.route("/partials/detail/panel/<name>/<int:team_id>")
def detail_panel(name: str, team_id: int):
    """One team, as the panel that was tapped sees them.

    Built on the panel's own view model rather than beside it, so a sheet cannot
    quietly disagree with the figure that was tapped to open it.
    """
    if name not in DETAILS:
        abort(404)
    snap = snapshot(_week_param())
    return render_template(f"partials/detail_{name}.html",
                           d=DETAILS[name](snap, team_id), snap=snap)


@bp.route("/partials/detail/change/<change_id>")
def detail_change(change_id: str):
    """One line of the ticker, in full."""
    snap = snapshot(_week_param())
    return render_template("partials/detail_change.html",
                           d=change_detail(state().live, change_id, snap), snap=snap)


@bp.route("/partials/panel/<name>")
def partial_panel(name: str):
    """One panel, re-rendered. The fragment htmx swaps in on every poll.

    `?full=1` picks the fuller variant a dedicated tab has room for. The flag
    rides on the request rather than living in two templates, because two
    templates is how the Receipts tab ended up still leading every row with a
    username months after the rest of the app moved to team names.
    """
    if name not in PANELS:
        abort(404)
    snap = snapshot(_week_param())
    context = PANELS[name](snap, state().live)
    return render_template(f"partials/{name}.html", snap=snap,
                           full=request.args.get("full") == "1", **context)


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
    if card is not None:
        # The rank is out of however many teams the league actually has, which
        # is not always ten: a nine-team league would otherwise read "#9 of 10".
        card["field"] = len(cards)
    return render_template("partials/team.html", card=card, snap=snap,
                           form_weights=FORM_WEIGHTS)


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
    snap = snapshot(_week_param())
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
    # The live feed's buffer is this week's by construction, so a page pinned to
    # an older week must not have it swapped in underneath: it would put this
    # Sunday's commentary beside that Sunday's scores. The stored lines are what
    # that week actually said, and they are what the fragment serves instead.
    week = _week_param()
    snap = snapshot(week)
    if week is not None and week != snapshot().scoring_period:
        return render_template("partials/moments.html",
                               moments=stored_moments(state().store(), snap), snap=snap)
    return render_template("partials/moments.html",
                           moments=moments_view(state().live, snap=snap), snap=snap)


@bp.route("/api/diagnostics")
def diagnostics():
    """Cache hit rate, backoff state, replay position. Never a cookie value."""
    return jsonify(state().diagnostics())
