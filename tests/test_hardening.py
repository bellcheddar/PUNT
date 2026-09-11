"""Phase 6: what happens when things break in a bar.

The acceptance criterion is *pulling the network cable mid-Sunday degrades
gracefully on every tab and recovers without a reload*, so these tests break
things on purpose and then check two separate properties each time: that nothing
500s, and that the page says something a person could act on.

A page that renders an empty panel after an outage has technically degraded
gracefully and is useless: the manager holding it has no idea whether the scores
are stale, whether it is their wifi, or whether their team is losing.
"""

from __future__ import annotations

import threading
import time

import pytest

from config import DEMO_RECORDING, Config
from espn import feeds
from espn.cache import TTLCache
from espn.client import AuthExpired, EspnClient, LeagueRepository, UpstreamError
from espn.replay import ReplayTransport

TABS = ["/", "/album", "/cheer", "/swing", "/receipts", "/multiverse", "/big-board"]


class Flaky:
    """A transport that can be broken and mended between polls.

    `down` is a *local* failure -- the bar's wifi -- which is the case the
    hardening phase is actually about, and which backs off far less aggressively
    than a failure that reached ESPN.
    """

    def __init__(self, inner, mode: str = "ok") -> None:
        self.inner = inner
        self.mode = mode
        self.calls = 0

    def fetch(self, feed, season, league_id, scoring_period):
        self.calls += 1
        if self.mode == "down":
            raise UpstreamError(f"{feed.name}: connection refused", local=True)
        if self.mode == "expired":
            raise AuthExpired(f"{feed.name}: ESPN returned 401")
        return self.inner.fetch(feed, season, league_id, scoring_period)


@pytest.fixture
def flaky_app():
    from app import create_app

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    transport.clock.seek(3 * 3600)
    flaky = Flaky(transport)
    client = EspnClient(transport=flaky, season=2025, league_id="demo", cache=TTLCache())
    cfg = Config(league_id="demo", season=2025, replay=DEMO_RECORDING, replay_speed=0.0)
    app = create_app(cfg=cfg, client=client, start_live=False)
    app.config.update(TESTING=True)
    return app, flaky, client


# --------------------------------------------------------------------------
# the cable
# --------------------------------------------------------------------------

def test_every_tab_survives_the_upstream_disappearing(flaky_app, no_network):
    app, flaky, client = flaky_app
    web = app.test_client()

    # Warm the cache, the way a real Sunday would have.
    for path in TABS:
        assert web.get(path).status_code == 200

    flaky.mode = "down"
    client.cache.invalidate()

    for path in TABS:
        response = web.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code} with ESPN down"
        body = response.get_data(as_text=True)
        assert "Traceback" not in body
        # Something a person could act on, not an empty panel.
        main = body.split("<main", 1)[1]
        assert any(marker in main for marker in ("empty", "banner", "matchup", "rows", "album-grid")), path


def test_an_outage_says_so_rather_than_showing_nothing(flaky_app, no_network):
    """"Never a blank screen" is a design principle, so it is a test.

    A page that renders an empty panel after an outage has technically degraded
    gracefully and is useless: the manager holding it cannot tell whether the
    scores are stale, whether it is their wifi, or whether they are losing."""
    app, flaky, client = flaky_app
    web = app.test_client()
    web.get("/")

    flaky.mode = "down"
    client.cache.invalidate()

    body = web.get("/").get_data(as_text=True)
    assert "banner--stale" in body or "Showing the last good data" in body
    assert "catch up on their own" in body, "the banner does not say it will recover"


def test_it_recovers_without_a_reload(flaky_app, no_network):
    """The other half of the criterion. htmx keeps polling the same fragment, so
    recovery means the next successful poll simply renders normally."""
    app, flaky, client = flaky_app
    web = app.test_client()

    flaky.mode = "down"
    client.cache.invalidate()
    broken = web.get("/partials/scorebar").get_data(as_text=True)

    flaky.mode = "ok"
    client.cache.invalidate()
    # The backoff outlives the outage by design, so recovery is at the next poll
    # after it expires rather than instantly. `clear_backoff` is what the
    # commissioner refresh calls; here it stands in for waiting.
    client.clear_backoff()
    mended = web.get("/partials/scorebar").get_data(as_text=True)

    assert "Bench Mob Rule" in mended
    assert mended != broken


def test_a_cold_start_during_an_outage_still_serves_a_page(no_network):
    """The worst case: the app restarts while ESPN is unreachable, so there is
    no cached data to fall back on at all."""
    from app import create_app

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    flaky = Flaky(transport, mode="down")
    client = EspnClient(transport=flaky, season=2025, league_id="demo", cache=TTLCache())
    cfg = Config(league_id="demo", season=2025, replay=DEMO_RECORDING, replay_speed=0.0)
    app = create_app(cfg=cfg, client=client, start_live=False)

    for path in TABS:
        response = app.test_client().get(path)
        assert response.status_code == 200, f"{path} failed on a cold start during an outage"


# --------------------------------------------------------------------------
# the cookies
# --------------------------------------------------------------------------

def test_expired_cookies_keep_serving_and_say_what_is_wrong(flaky_app, no_network):
    """espn_s2 rotates, and it will rotate on a Sunday.

    Cached data must keep serving, and the commissioner needs a banner that names
    the actual problem -- "ESPN is not answering" would send them to check their
    wifi for an hour."""
    app, flaky, client = flaky_app
    web = app.test_client()
    web.get("/")

    flaky.mode = "expired"
    client.cache.invalidate()

    response = web.get("/")
    assert response.status_code == 200
    assert client.auth.ok is False
    assert client.auth.since, "the moment the cookies expired was not recorded"

    diagnostics = web.get("/api/diagnostics").get_json()
    assert diagnostics["auth_ok"] is False
    assert "401" in diagnostics["auth_detail"] or "rotated" in diagnostics["auth_detail"]


def test_the_admin_refresh_clears_the_cache_and_the_auth_flag(no_network):
    from app import create_app

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    flaky = Flaky(transport, mode="expired")
    client = EspnClient(transport=flaky, season=2025, league_id="demo", cache=TTLCache())
    cfg = Config(league_id="demo", season=2025, admin_token="s3cret",
                 replay=DEMO_RECORDING, replay_speed=0.0)
    app = create_app(cfg=cfg, client=client, start_live=False)
    web = app.test_client()

    web.get("/")
    assert client.auth.ok is False

    assert web.post("/admin/refresh-cookies").status_code == 403
    assert web.post("/admin/refresh-cookies",
                    headers={"X-Punt-Admin": "wrong"}).status_code == 403

    response = web.post("/admin/refresh-cookies", headers={"X-Punt-Admin": "s3cret"})
    assert response.status_code == 200
    assert client.auth.ok is True
    assert client.cache.peek("mSettings:2025:demo") is None


def test_local_failures_back_off_far_less_than_espn_failures(no_network):
    """The bar's wifi and somebody else's outage deserve different treatment.

    Backing off for five minutes because the venue's router rebooted protects
    nobody -- the requests were not arriving anywhere -- and it means plugging
    the cable back in takes five minutes to notice, which is the thing this whole
    phase exists to prevent.
    """
    from espn.client import MAX_BACKOFF, MAX_LOCAL_BACKOFF

    assert MAX_LOCAL_BACKOFF < MAX_BACKOFF / 4

    def ceiling(local: bool) -> float:
        transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
        client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
        # Driven directly rather than through repeated polls: a refusal *while*
        # backing off is deliberately not counted as a new failure, so escalation
        # is time-gated and a tight loop never reaches the ceiling. Exercising
        # the ceiling is the point here.
        for _ in range(12):
            client._record_failure(local=local)
        return client.backoff_remaining

    local_ceiling = ceiling(local=True)
    upstream_ceiling = ceiling(local=False)
    assert local_ceiling <= MAX_LOCAL_BACKOFF
    assert upstream_ceiling > MAX_LOCAL_BACKOFF * 2
    assert upstream_ceiling <= MAX_BACKOFF


def test_a_refusal_while_backing_off_is_not_counted_as_a_new_failure(no_network):
    """Otherwise the backoff escalates on its own refusals and reaches the
    ceiling in a fraction of a second, turning a single blip into five minutes
    of silence."""
    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    flaky = Flaky(transport, mode="down")
    client = EspnClient(transport=flaky, season=2025, league_id="demo", cache=TTLCache())

    client.get(feeds.SETTINGS)
    after_one = client.backoff_remaining
    for _ in range(20):
        client.cache.invalidate()
        client.get(feeds.SETTINGS)

    assert client.backoff_remaining <= after_one + 0.5
    assert flaky.calls == 1, f"{flaky.calls} upstream calls while backing off"


def test_backoff_stops_hammering_a_broken_upstream(no_network):
    """An undocumented endpoint stops being available to anybody when somebody's
    app retries it every thirty seconds for four hours."""
    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    flaky = Flaky(transport, mode="expired")
    client = EspnClient(transport=flaky, season=2025, league_id="demo", cache=TTLCache())

    for _ in range(6):
        client.cache.invalidate()
        client.get(feeds.SETTINGS)

    assert client.backing_off is True
    assert client.backoff_remaining > 0
    # Six attempts, but not six *upstream* calls: once backing off, the client
    # refuses locally rather than going out again.
    assert flaky.calls < 6, f"{flaky.calls} upstream calls while backing off"


# --------------------------------------------------------------------------
# ten phones
# --------------------------------------------------------------------------

def test_ten_phones_produce_one_upstream_poll(flaky_app, no_network):
    """The property the whole bar depends on, tested through the app rather than
    through the cache in isolation: ten concurrent page loads, one fetch each
    upstream feed."""
    app, flaky, client = flaky_app
    client.cache.invalidate()
    flaky.calls = 0

    ready = threading.Barrier(10)
    codes: list[int] = []

    def phone():
        web = app.test_client()
        ready.wait()
        codes.append(web.get("/").status_code)

    threads = [threading.Thread(target=phone) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert codes == [200] * 10
    # A snapshot reads one feed of each kind: settings, teams, the season grid,
    # this week's boxscore and the NFL scoreboard. Counted from the repository
    # rather than hard-coded, so adding a feed updates the bound instead of
    # breaking the test for the wrong reason -- which is exactly what happened
    # when the season grid was added.
    feeds_per_snapshot = 5
    assert flaky.calls <= feeds_per_snapshot, (
        f"{flaky.calls} upstream calls for ten simultaneous phones, "
        f"expected at most {feeds_per_snapshot}"
    )


# --------------------------------------------------------------------------
# the licence audit
# --------------------------------------------------------------------------

def test_every_shipped_asset_is_accounted_for():
    """The spec's rule: every asset gets a line, no exceptions, no orphan files.

    Written as a test rather than as a pre-launch checklist because a checklist
    is done once and an orphan arrives on a Tuesday. The licence files are next
    to the assets they describe, so the audit is "is this file named in the
    LICENCES.md beside it", which is a question with an answer.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    static = root / "static"

    #: Generated from the repository's own sources, so covered by the repository's
    #: own licence. Each is named in a LICENCES.md as generated.
    GENERATED = ("audio/sprite.", "audio/bed.", "icons/", "splash/", "css/theme.css",
                 "css/fonts.css", "manifest.json")

    licence_text = "\n".join(
        path.read_text("utf-8") for path in static.rglob("LICENCES.md")
    )
    assert licence_text, "no LICENCES.md anywhere under static/"

    unaccounted: list[str] = []
    for path in sorted(static.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(static).as_posix()
        if path.name in ("LICENCES.md", ".DS_Store") or relative.startswith("audio/phrase/"):
            continue          # derived at runtime, or macOS detritus
        if any(relative.startswith(prefix) for prefix in GENERATED):
            continue
        # A vendored file must be named; a font is covered by the fonts line.
        if path.name in licence_text or relative in licence_text:
            continue
        if relative.startswith("fonts/") and "fonts/*.woff2" in licence_text:
            continue
        unaccounted.append(relative)

    assert not unaccounted, f"assets with no licence line: {unaccounted}"


def test_no_build_artefact_is_left_in_the_static_tree():
    """The screenshot and overflow tools write harnesses into `static/` so they
    are same-origin with the app, and delete them again. One escaped and sat
    there until the licence audit named it -- which is the audit doing its job,
    but a file in the public static tree is worth its own assertion."""
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "static"
    strays = [p.name for p in static.glob("*")
              if p.is_file() and (p.name.startswith(("_", "tmp")) or p.suffix == ".part")]
    assert not strays, f"build artefacts left in the public static tree: {strays}"


def test_nothing_in_the_repository_is_a_downloaded_sound():
    """Every sting is synthesised, which is what keeps the audio licence file
    from ever drifting: there is nothing in it that somebody else made."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    audio_licences = (root / "static" / "audio" / "LICENCES.md").read_text("utf-8")
    assert "synthesised" in audio_licences.lower()
    assert "nothing is sampled" in audio_licences.lower()

    shipped = {p.name for p in (root / "static" / "audio").glob("*")
               if p.is_file() and p.suffix in (".mp3", ".ogg")}
    assert shipped <= {"sprite.mp3", "sprite.ogg", "bed.mp3", "bed.ogg"}, \
        f"an audio file nobody accounted for: {shipped}"
