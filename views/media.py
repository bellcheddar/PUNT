"""Binary assets: the team logo proxy, and later the pre-synthesised TTS."""

from __future__ import annotations

import hashlib
import io
import logging

from flask import Blueprint, Response, abort, redirect, send_file

from views.state import snapshot

log = logging.getLogger(__name__)

bp = Blueprint("media", __name__)

#: Logos are user uploads on ESPN's CDN and effectively immutable once set, so a
#: long cache is safe. A manager who changes their logo waits a day for it,
#: which is a better trade than ten phones re-fetching ten images every poll.
LOGO_CACHE_SECONDS = 86_400


@bp.route("/img/team/<int:team_id>")
def team_logo(team_id: int):
    """Proxy a team's uploaded logo.

    Hotlinking ESPN's CDN directly from ten phones fails in at least four ways --
    a blocked referer, a dead link, a 3000x200 image, or no logo at all -- and
    each failure lands as a broken image on a card. Proxying puts every one of
    those on the server, where the answer is a redirect to the monogram fallback
    instead of a broken card.
    """
    snap = snapshot()
    team = snap.team(team_id)
    if team is None:
        abort(404)
    if not team.logo:
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
        return redirect(f"/img/monogram/{team_id}", code=302)

    return Response(
        response.content,
        mimetype=content_type,
        headers={
            "Cache-Control": f"public, max-age={LOGO_CACHE_SECONDS}",
            "ETag": hashlib.sha256(response.content).hexdigest()[:16],
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
