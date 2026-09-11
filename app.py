"""Application factory and blueprint registration.

    FLASK_APP=app flask run          # demo recording, no cookies needed
    REPLAY=demo-2025-11-16 REPLAY_SPEED=60 flask run
    python3 -m app                   # same thing, without the flask CLI
"""

from __future__ import annotations

import logging
import os

from flask import Flask, render_template

from config import Config, assert_no_secrets_in_static
from espn.cache import TTLCache
from espn.client import LeagueRepository, build_client
from views import admin, api, media, tabs
from views.state import PuntState

log = logging.getLogger(__name__)


def create_app(cfg: Config | None = None, client=None, start_live: bool = True) -> Flask:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = cfg or Config.from_env()
    # Runs before anything is served. Cheap, and the accident it catches -- a
    # cookie written into the public static tree by some build step -- is not
    # recoverable once a crawler has been past.
    assert_no_secrets_in_static(cfg)

    app = Flask(__name__)
    app.config["PUNT"] = cfg
    # There is no form, no login and no session in this app, so the secret key
    # exists only to satisfy Flask's flash machinery if it is ever used.
    app.secret_key = os.environ.get("SECRET_KEY", "punt-no-session-state")

    client = client or build_client(cfg, cache=TTLCache())
    app.extensions["punt"] = PuntState(cfg=cfg, client=client, repo=LeagueRepository(client))

    app.register_blueprint(tabs.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(media.bp)
    app.register_blueprint(admin.bp)

    # Off by default in tests: a background thread that polls a replay clock
    # makes every assertion in the suite a race.
    if start_live:
        app.extensions["punt"].start_live()

    _register_asset_version(app)
    _register_filters(app)
    _register_error_handlers(app)
    _register_cache_control(app)

    log.info("PUNT ready: %s", app.extensions["punt"].diagnostics()["mode"])
    return app


def _register_cache_control(app: Flask) -> None:
    """Stop a browser guessing how long to keep a page.

    Flask sends no Cache-Control on a rendered template, so a browser applies
    heuristic caching to the HTML, keeps serving the `?v=` stamp it already has,
    and a CSS or JS deploy is simply invisible to anyone who has visited before.

    Set here rather than in the nginx vhost, which is where it used to live. The
    nginx form was `add_header` on `location /`, and add_header *appends*: the
    team-logo routes set their own `public, max-age=86400`, both headers went out,
    and a browser joins repeated Cache-Control field lines into one comma-joined
    value where `no-cache` wins. Every team logo was therefore revalidated on
    every page load -- ten of them per album, ten phones, one bar wifi -- while
    each response looked correct on its own and the intended header was right
    there in the response.

    `setdefault`, so a route that has decided how long its own body lives keeps
    the answer.
    """

    @app.after_request
    def _cache_control(response):
        response.headers.setdefault("Cache-Control", "no-cache, must-revalidate")
        return response


def _register_asset_version(app: Flask) -> None:
    """Stamp every static URL with the newest mtime in the static tree.

    Flask sends no Cache-Control header on a rendered template, so a browser
    applies heuristic caching to the HTML, keeps serving the old `?v=` it already
    has, and a CSS or JS deploy is simply invisible to anyone who has visited
    before. Computed once at boot, because the files do not change under a
    running gunicorn.
    """
    from config import STATIC_DIR  # noqa: PLC0415

    newest = 0.0
    if STATIC_DIR.is_dir():
        newest = max((p.stat().st_mtime for p in STATIC_DIR.rglob("*") if p.is_file()), default=0.0)
    version = str(int(newest))

    @app.context_processor
    def inject_asset_version():
        return {"asset_version": version}


def _register_filters(app: Flask) -> None:
    @app.template_filter("points")
    def points(value) -> str:
        """Scores are always shown to one decimal. Fantasy points carry two, but
        the second is noise at a glance and costs a character on a 390 px card."""
        try:
            return f"{float(value):.1f}"
        except (TypeError, ValueError):
            return "--"

    @app.template_filter("signed")
    def signed(value) -> str:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "--"
        return f"{number:+.1f}"

    @app.template_filter("pct")
    def pct(value) -> str:
        try:
            return f"{float(value) * 100:.0f}%"
        except (TypeError, ValueError):
            return "--"


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404,
                               message="That page does not exist. The tab bar is below."), 404

    @app.errorhandler(500)
    def server_error(error):
        log.exception("unhandled error: %s", error)
        return render_template("error.html", code=500,
                               message="Something broke on the server. The scores are fine; "
                                       "this page is not."), 500


def main() -> None:
    """`python3 -m app`. The flask CLI finds `create_app` on its own, and
    gunicorn goes through `wsgi.py`, so there is deliberately no module-level
    `app` here: importing this module in a test would otherwise build a whole
    second application and load the demo recording as a side effect."""
    app = create_app()
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "8011")),
            debug=os.environ.get("FLASK_DEBUG") == "1",
            threaded=True)


if __name__ == "__main__":
    main()
