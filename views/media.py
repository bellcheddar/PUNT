"""Binary assets: the team logo proxy, and later the pre-synthesised TTS."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from collections import OrderedDict
from pathlib import Path

from flask import Blueprint, Response, abort, current_app, redirect, send_file, send_from_directory

from views.state import snapshot

log = logging.getLogger(__name__)

bp = Blueprint("media", __name__)

#: Logos are user uploads on ESPN's CDN and effectively immutable once set, so a
#: long cache is safe. A manager who changes their logo waits a day for it,
#: which is a better trade than ten phones re-fetching ten images every poll.
LOGO_CACHE_SECONDS = 86_400

#: Proxied logos, in this process, so the browser cache is not the only one.
#:
#: Measured before this existed: each `/img/team/N` took 600 to 900 ms, because
#: every request went to ESPN's CDN again. Ten phones opening the album is a
#: hundred upstream image fetches for ten images, on a first load, over the
#: bar's wifi. The browser's own cache does not help the first visit and does not
#: help the tenth phone at all.
#:
#: Failures are cached too, and that is the more important half: a logo URL that
#: 404s costs the same 900 ms as one that works, every time, for the whole season.
_LOGO_CACHE: "OrderedDict[int, tuple[bytes, str] | None]" = OrderedDict()
LOGO_CACHE_ENTRIES = 64


@bp.route("/audio/phrase/<digest>.mp3")
def phrase_audio(digest: str):
    """Pre-synthesised commentary, immutable and cached hard.

    The name is the hash of exactly what is spoken, so the content behind a URL
    can never change and a year is a safe max-age. A miss is a 404 rather than a
    wait: the line is still on screen, it simply is not spoken, and holding a
    worker open through the loudest minute of the afternoon is a worse failure
    than a silent line.
    """
    from views.state import state  # noqa: PLC0415

    if not re.fullmatch(r"[0-9a-f]{8,64}", digest):
        abort(404)

    cache = state().speech
    if cache is None:
        abort(404)

    path = cache.wait_for(digest)
    if path is None:
        abort(404)

    response = send_file(path, mimetype="audio/mpeg", conditional=True)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@bp.route("/favicon.ico")
def favicon():
    """The icon a browser asks for whether or not the page links to one.

    Every `<link rel="icon">` in the head is honoured, and then a browser still
    requests `/favicon.ico` at the root in some contexts: a bookmark, a
    developer-tools panel, an OS-level shortcut. Unanswered that is a 404 in the
    log on every visit. Served as the 32 px PNG rather than a real ICO, which
    every browser in the last decade accepts.
    """
    return send_from_directory(
        Path(current_app.root_path, "static", "icons"), "favicon-32.png",
        mimetype="image/png", max_age=86400,
    )


@bp.route("/sw.js")
def service_worker():
    """Serve the worker from the site root.

    A service worker's default scope is the directory it is served from, so one
    registered at `/static/js/sw.js` controls `/static/js/` and nothing the app
    ever navigates to. Serving the same file from `/` is the whole fix. It is
    also deliberately not cached: a worker that cannot be replaced is a worker
    you live with for ever.
    """
    from flask import Response, current_app  # noqa: PLC0415

    # Read and substituted rather than sent from disk, because the shell it
    # precaches has to carry the same `?v=` stamp the templates put on their own
    # URLs. `caches.match` compares the whole URL including the query, so an
    # unstamped shell is cached under URLs the page never asks for: online the
    # miss falls through to the network and nobody notices, and offline -- the
    # entire reason this file exists -- the page comes up with no CSS at all.
    source = Path(current_app.root_path, "static", "js", "sw.js").read_text("utf-8")
    stamp = current_app.config.get("ASSET_VERSION", "dev")
    body = source.replace("__ASSET_VERSION__", str(stamp))

    response = Response(body, mimetype="text/javascript")
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@bp.route("/img/team/<int:team_id>")
def team_logo(team_id: int):
    """Proxy a team's uploaded logo.

    Hotlinking ESPN's CDN directly from ten phones fails in at least four ways --
    a blocked referer, a dead link, a 3000x200 image, or no logo at all -- and
    each failure lands as a broken image on a card. Proxying puts every one of
    those on the server, where the answer is a redirect to the monogram fallback
    instead of a broken card.
    """
    cached = _cache_get(team_id)
    if cached is not None:
        if cached is _MISS:
            return redirect(f"/img/monogram/{team_id}", code=302)
        return _logo_response(*cached)

    snap = snapshot()
    team = snap.team(team_id)
    if team is None:
        abort(404)
    if not team.logo:
        _cache_put(team_id, _MISS)
        return redirect(f"/img/monogram/{team_id}", code=302)

    try:
        import requests  # noqa: PLC0415

        response = requests.get(team.logo, timeout=6)
        if response.status_code != 200 or not response.content:
            raise ValueError(f"HTTP {response.status_code}")
        content_type = response.headers.get("Content-Type", "")
        if not content_type.startswith("image/"):
            raise ValueError(f"content type {content_type!r}")
    except Exception as exc:  # noqa: BLE001
        log.info("logo proxy fell back to monogram for team %s: %s", team_id, exc)
        _cache_put(team_id, _MISS)
        return redirect(f"/img/monogram/{team_id}", code=302)

    _cache_put(team_id, (response.content, content_type))
    return _logo_response(response.content, content_type)


#: Sentinel for "this team has no usable logo", so the failure is cached as
#: firmly as a success.
_MISS = object()
_LOGO_LOCK = threading.Lock()


def _cache_get(team_id: int):
    with _LOGO_LOCK:
        entry = _LOGO_CACHE.get(team_id)
        if entry is not None:
            _LOGO_CACHE.move_to_end(team_id)
        return entry


def _cache_put(team_id: int, value) -> None:
    with _LOGO_LOCK:
        _LOGO_CACHE[team_id] = value
        _LOGO_CACHE.move_to_end(team_id)
        while len(_LOGO_CACHE) > LOGO_CACHE_ENTRIES:
            _LOGO_CACHE.popitem(last=False)


def _logo_response(content: bytes, content_type: str) -> Response:
    return Response(
        content,
        mimetype=content_type,
        headers={
            "Cache-Control": f"public, max-age={LOGO_CACHE_SECONDS}",
            "ETag": hashlib.sha256(content).hexdigest()[:16],
        },
    )


@bp.route("/img/monogram/<int:team_id>")
def monogram(team_id: int):
    """The fallback tile, as SVG.

    At least two managers never upload a logo, all season. That makes this a
    designed state rather than an error state, and it is drawn in the team's own
    deterministic hue so it reads as part of the set rather than as a gap in it.
    """
    snap = snapshot()
    team = snap.team(team_id)
    letters = team.monogram if team else "?"
    hue = team.hue if team else 210

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" width="120" height="120" role="img" aria-label="{letters}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="hsl({hue} 62% 46%)"/>
      <stop offset="100%" stop-color="hsl({(hue + 38) % 360} 58% 28%)"/>
    </linearGradient>
  </defs>
  <rect width="120" height="120" rx="18" fill="url(#g)"/>
  <text x="60" y="60" text-anchor="middle" dominant-baseline="central"
        font-family="Anton, Impact, 'Arial Black', sans-serif"
        font-size="{46 if len(letters) < 3 else 36}" fill="rgba(255,255,255,.94)"
        letter-spacing="1">{letters}</text>
</svg>"""
    return Response(
        svg,
        mimetype="image/svg+xml",
        headers={"Cache-Control": f"public, max-age={LOGO_CACHE_SECONDS}"},
    )
