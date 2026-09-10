"""The commissioner surface. Env-gated, and the only route that mutates anything."""

from __future__ import annotations

import hmac

from flask import Blueprint, jsonify, request

from views.state import state

bp = Blueprint("admin", __name__)


def _authorised() -> bool:
    """Constant-time compare against `ADMIN_TOKEN`.

    An unset token denies rather than allows. A commissioner route that defaults
    open because nobody set an environment variable is how a public URL becomes
    a cache-clearing endpoint for the whole internet.
    """
    expected = state().cfg.admin_token
    if not expected:
        return False
    supplied = request.headers.get("X-Punt-Admin", "") or request.form.get("token", "")
    return hmac.compare_digest(supplied, expected)


@bp.route("/admin/refresh-cookies", methods=["POST"])
def refresh_cookies():
    """Drop the cache after `espn_s2` has been replaced in the environment.

    Deliberately does not accept a cookie value in the request body. The cookie
    is set in `.env` and the service restarted; this route only tells a running
    process to stop serving what it fetched with the old one. Accepting a secret
    over HTTP would put it in an access log the first time somebody used a GET.
    """
    if not _authorised():
        return jsonify({"ok": False, "error": "unauthorised"}), 403

    st = state()
    st.client.cache.invalidate()
    st.client.auth.mark_ok()
    return jsonify({"ok": True, "cleared": True, "mode": st.mode})
