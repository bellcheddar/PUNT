"""Every route, against the demo recording, with no network."""

from __future__ import annotations

import pytest

ROUTES = ["/", "/album", "/cheer", "/swing", "/receipts", "/multiverse", "/big-board"]


@pytest.mark.parametrize("path", ROUTES)
def test_every_tab_renders(client, path, no_network):
    response = client.get(path)
    assert response.status_code == 200
    assert b"PUNT" in response.data


@pytest.mark.parametrize("path", ROUTES)
def test_no_tab_is_ever_blank(client, path, no_network):
    """"Never a blank screen" is a design principle, so it is a test.

    A tab with no data must still render either real content or a styled empty
    state with a human-readable message -- never an empty panel."""
    body = client.get(path).get_data(as_text=True)
    main = body.split("<main", 1)[1]
    rendered = ("empty", "matchup", "rows", "album-grid")
    assert any(marker in main for marker in rendered), f"{path} rendered nothing"


def test_tv_mode_drops_the_chrome(client, no_network):
    normal = client.get("/").get_data(as_text=True)
    tv = client.get("/?tv=1").get_data(as_text=True)
    assert 'class="tabbar' in normal
    assert 'class="tabbar' not in tv
    assert 'data-tv="1"' in tv


def test_big_board_is_tv_shaped_without_the_parameter(client, no_network):
    body = client.get("/big-board").get_data(as_text=True)
    assert 'data-tv="1"' in body
    assert 'class="tabbar' not in body


def test_healthz_does_not_depend_on_espn(client, no_network):
    """A health check that fails when a third party is down turns their outage
    into ours, and takes the deploy script with it."""
    payload = client.get("/healthz").get_json()
    assert payload["ok"] is True


def test_api_state_is_shaped_for_the_client(client, no_network):
    payload = client.get("/api/state").get_json()
    assert payload["scoring_period"] == 11
    assert len(payload["album"]) == 10
    assert len(payload["matchups"]) == 5
    assert {c["tier"] for c in payload["album"]} & {"epic", "cursed"}


def test_diagnostics_never_leak_a_cookie(app, no_network):
    """The redaction is the reason it is safe for this route to exist at all."""
    from config import Config
    from espn.cache import TTLCache
    from espn.client import EspnClient
    from espn.replay import ReplayTransport
    from config import DEMO_RECORDING

    secret = "AEBxyzREALCOOKIEVALUE1234567890"
    cfg = Config(league_id="demo", season=2025, espn_s2=secret, espn_swid="{S-W-I-D}",
                 replay=DEMO_RECORDING, replay_speed=0.0)
    from app import create_app

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    other = create_app(cfg=cfg, client=EspnClient(transport, 2025, "demo", TTLCache()), start_live=False)
    body = other.test_client().get("/api/diagnostics").get_data(as_text=True)
    assert secret not in body
    assert f"set:{len(secret)}ch" in body, "the fingerprint should still say which cookie is loaded"


def test_admin_refresh_denies_by_default(client, no_network):
    """An unset ADMIN_TOKEN must deny, not allow. A commissioner route that
    defaults open because nobody set an env var is a public cache-clearing
    endpoint for the whole internet."""
    assert client.post("/admin/refresh-cookies").status_code == 403


def test_monogram_is_served_for_a_logoless_team(client, no_network):
    """Two of the ten managers never upload a logo. That is a designed state."""
    response = client.get("/img/monogram/4")
    assert response.status_code == 200
    assert response.mimetype == "image/svg+xml"
    # The monogram is three characters at most: it is drawn at 120 px square and
    # a fourth letter is unreadable on a 34 px crest.
    assert b"STA" in response.data


def test_a_missing_page_still_shows_the_tab_bar(client, no_network):
    response = client.get("/definitely-not-a-tab")
    assert response.status_code == 404
    assert b"tabbar" in response.data


def test_view_models_produce_text_not_markup(client, no_network):
    """A view model's strings are text, and Jinja escapes them.

    An HTML entity written into one -- "A &mdash; B" -- comes out on the page as
    the literal characters `&mdash;`, because the ampersand is escaped. It looked
    fine in the Python and wrong on the bar screen, which is the only place it
    was visible.
    """
    import re

    from views.viewmodels import cheer_view, receipts_view, swing_view, watch_now

    state = client.application.extensions["punt"]
    # Mid-afternoon, not kickoff. The default replay position is pre-game, where
    # `cheer_view` and `watch_now` both correctly return nothing -- so the first
    # version of this test walked two empty lists and passed no matter what was
    # planted in them. Verified by planting an entity on an always-executed path
    # and watching it still pass.
    state.replay.clock.seek(3 * 3600)
    state.client.cache.invalidate()
    snap = state.repo.snapshot()

    entity = re.compile(r"&(?:[a-zA-Z]+|#\d+);")

    def check(label: str, rows: list[dict]) -> int:
        strings = [v for row in rows for v in row.values() if isinstance(v, str)]
        offenders = [s for s in strings if entity.search(s)]
        assert not offenders, f"HTML entities in {label}: {offenders[:3]}"
        return len(strings)

    with client.application.app_context():
        # Each view model separately, and each must actually have produced text:
        # an empty list is not a pass.
        assert check("watch_now", watch_now(snap)) > 0
        assert check("cheer_view", cheer_view(snap, team_id=1)) > 0
        assert check("receipts_view", receipts_view(snap)["rows"]) > 0
        assert check("swing_view", swing_view(snap)["rows"]) > 0


def test_a_legendary_card_can_actually_be_minted(no_network):
    """The tier was unreachable, and nothing said so.

    It read the *current* win probability, so mid-game it marked whoever was
    losing as legendary, and once the games finished every probability was 1.0 or
    0.0 and nobody qualified at all. `tools/deadcode.py` found it: the
    `return "legendary"` line never executed during a whole simulated Sunday.

    The spec's rule is a season-high score or a win from under 10%, and "from
    under 10%" is a thing that was true at some point in the afternoon.
    """
    from config import DEMO_RECORDING
    from engine.events import EventEngine
    from engine.live import LiveFeed
    from espn.cache import TTLCache
    from espn.client import EspnClient, LeagueRepository
    from espn.replay import ReplayTransport
    from views.viewmodels import album_view

    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    client = EspnClient(transport, 2025, "demo", TTLCache())
    repo = LeagueRepository(client)
    feed = LiveFeed(
        fetch=lambda: (client.cache.invalidate(), repo.snapshot())[1],
        poll_seconds=30, engine=EventEngine(simulate_draws=200),
    )
    feed._broadcast = lambda payload: None
    for position in range(0, int(transport.recording.duration) + 120, 120):
        transport.clock.seek(position)
        feed.poll_once()

    cards = album_view(feed.snapshot, feed)
    legendary = [c for c in cards if c["tier"] == "legendary"]
    assert legendary, "no Legendary card in a Sunday containing a comeback from 7%"

    for card in legendary:
        assert card["season_high"] or (card["winning"] and card["week_low"] < 0.10)

    # Both routes into Legendary, at the final whistle. The season-high one used
    # to be unreachable there and only there: ESPN marks the week complete the
    # moment the last game ends, so the week being played joined settled_weeks
    # and a team's own live score entered its own season best. "Beat your season
    # high" then read "beat your own score", which is false for everybody. The
    # card was visible all afternoon and gone at the whistle, which is exactly
    # backwards for a trophy, and the disjunct above passed the whole time.
    assert any(c["season_high"] for c in legendary), (
        "no season-high Legendary card at settle: the current week is being "
        "counted as part of its own season best again"
    )
    assert any(c["winning"] and c["week_low"] < 0.10 for c in legendary)

    # And the other half: dipping below 10% and losing is not legendary, it is
    # just losing. Several managers bottomed out at 0.0% in this fixture.
    losers_who_dipped = [
        c for c in cards
        if not c["winning"] and c["week_low"] is not None and c["week_low"] < 0.10
    ]
    assert losers_who_dipped, "the fixture should contain doomed managers"
    assert all(c["tier"] != "legendary" for c in losers_who_dipped)


def test_the_low_water_mark_resets_between_weeks():
    """It is the week's minimum, not the season's. Carrying it over would mint a
    Legendary card in week 12 for something that happened in week 11."""
    from engine.events import EventEngine
    from engine.live import LiveFeed
    from espn.models import LeagueSettings, LeagueSnapshot

    feed = LiveFeed(fetch=lambda: None, poll_seconds=30, engine=EventEngine(simulate_draws=10))
    feed.engine._win_prob = {1: 0.04}
    feed._accumulate(LeagueSnapshot(season=2025, scoring_period=11, settings=LeagueSettings()), [])
    assert feed.week_low[1] == 0.04

    feed.engine._win_prob = {1: 0.80}
    feed._accumulate(LeagueSnapshot(season=2025, scoring_period=12, settings=LeagueSettings()), [])
    assert feed.week_low[1] == 0.80, "last week's low survived into this week"


def test_a_response_that_chose_its_own_cache_lifetime_keeps_it(client):
    """One Cache-Control per response, and the route's own answer wins.

    This used to be an `add_header` in the nginx vhost, and add_header appends.
    The team-logo routes set `public, max-age=86400`, both headers went out, and
    a browser joins repeated Cache-Control field lines into one comma-joined
    value where `no-cache` wins -- so every logo was revalidated on every page
    load, ten per album, while each response looked correct on its own and the
    intended header was right there in it.
    """
    page = client.get("/")
    assert page.headers.get_all("Cache-Control") == ["no-cache, must-revalidate"]

    logo = client.get("/img/monogram/1")
    assert logo.status_code == 200
    values = logo.headers.get_all("Cache-Control")
    assert len(values) == 1, f"two Cache-Control headers: {values}"
    assert "max-age=" in values[0] and "no-cache" not in values[0]

    worker = client.get("/sw.js")
    assert worker.headers.get_all("Cache-Control") == ["no-cache, must-revalidate"]


def test_the_offline_shell_precaches_the_urls_the_page_actually_asks_for(client):
    """The service worker's shell has to carry the same stamp the page emits.

    `caches.match` compares the whole URL including the query string. Precached
    as '/static/css/theme.css' while the page asks for
    '/static/css/theme.css?v=<the stamp>', every shell entry was cached under a
    URL nothing ever requests. Online the miss falls straight through to the
    network and nobody notices; offline -- the entire reason a service worker is
    here -- the cached page came up with no CSS, no fonts and no JavaScript.
    """
    import re

    worker = client.get("/sw.js").get_data(as_text=True)
    assert "__ASSET_VERSION__" not in worker, "the stamp was never substituted"

    declared = re.search(r"const STAMP = '(\d+)'", worker)
    assert declared, "the worker has no asset stamp in it"
    stamp = declared.group(1)

    page = client.get("/").get_data(as_text=True)
    assert set(re.findall(r"\?v=(\d+)", page)) == {stamp}, (
        "the worker precaches a different version from the one the page requests"
    )

    # The cache name moves with it, or `activate` evicts nothing and a deploy
    # leaves last month's assets in there for ever.
    assert "const VERSION = `punt-${STAMP}`" in worker

    # Resolve the shell the way the browser will, and check every entry is a URL
    # this app really serves. A precached 404 is a silent hole in offline mode:
    # `cache.add` is called individually precisely so one bad entry does not take
    # the rest down with it, which also means nothing reports it.
    shell = re.search(r"const SHELL = \[(.*?)\];", worker, re.S)
    assert shell, "no shell to check"
    urls = re.findall(r"[`\'](/[^`\']*)[`\']", shell.group(1))
    assert len(urls) >= 10, f"only {len(urls)} shell entries found"
    for url in urls:
        resolved = url.replace("${STAMP}", stamp)
        assert client.get(resolved).status_code == 200, f"{resolved} is precached and does not 200"
