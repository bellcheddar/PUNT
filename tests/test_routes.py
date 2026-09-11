"""Every route, against the demo recording, with no network."""

from __future__ import annotations

from pathlib import Path

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


ROOT_STATIC = Path(__file__).resolve().parent.parent / "static"

def test_tv_mode_drops_the_chrome(client, no_network):
    """The chrome a phone needs and a television across a room does not.

    The tab bar used to be the marker here. There is no tab bar now, so the
    marker is the music credit: CC-BY is discharged where the work is heard, and
    a credit is worth showing to somebody holding the page and pointless on a
    screen nobody can touch.
    """
    normal = client.get("/").get_data(as_text=True)
    tv = client.get("/?tv=1").get_data(as_text=True)
    assert 'class="credit"' in normal
    assert 'class="credit"' not in tv
    assert 'data-tv="1"' in tv
    assert 'class="tabbar' not in normal, "the tab bar is gone; see views/tabs.py"


def test_big_board_is_tv_shaped_without_the_parameter(client, no_network):
    body = client.get("/big-board").get_data(as_text=True)
    assert 'data-tv="1"' in body
    assert 'class="credit"' not in body


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

    # Nor any *fragment* of one. The original test asserted only that the whole
    # value was absent, and passed the entire time the fingerprint ended in
    # `…{secret[-4:]}` -- four literal characters of a live session cookie, on a
    # route that is unauthenticated on purpose. On a 38-character SWID that is a
    # tenth of the value, published to anyone who asked.
    swid = "{S-W-I-D}"
    for value in (secret, swid):
        for size in range(4, min(len(value), 12) + 1):
            for start in range(len(value) - size + 1):
                assert value[start:start + size] not in body, (
                    f"{size} characters of a cookie reached /api/diagnostics: "
                    f"{value[start:start + size]!r}"
                )

    # And it still distinguishes: rotate the cookie, get a different tag.
    from config import _fingerprint

    assert _fingerprint(secret) != _fingerprint(secret + "x")
    assert _fingerprint(secret) == _fingerprint(secret), "the tag must be stable across restarts"


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


def test_a_missing_page_still_shows_the_app_around_it(client, no_network):
    """A 404 is still a page of this app, not a bare browser error.

    This used to assert the tab bar was on it. There is no tab bar any more:
    five tabs meant five documents, and audio needs a user gesture per document,
    so the tap that changed tabs was the tap that unlocked the sound and the
    document was destroyed a moment later, mid fade-in. What matters here was
    never the bar itself but that the chrome is still wrapped round the error.
    """
    response = client.get("/definitely-not-a-tab")
    assert response.status_code == 404
    assert b"PUNT" in response.data
    assert b"<main" in response.data


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


def test_everything_is_on_one_page(client, no_network):
    """The whole point of the restructure.

    Five tabs meant five documents, and a browser requires a user gesture per
    document before it will make a sound. So the tap that changed tabs was also
    the tap that unlocked the audio: the bed began its 1400 ms fade-in and the
    document was torn down before it finished. The music could only ever be
    heard in the gap between the tap and the page changing.
    """
    body = client.get("/?punt=steady&team=5").get_data(as_text=True)
    for marker in ("album-grid", "Who is in trouble", "Commentary",
                   "Cheer", "Bench regret", "Playoff odds"):
        assert marker in body, f"{marker} is not on the single page"
    # The shape changed again: the cards and the live slate are full width with
    # their own internal grids, and bench regret sits beside a stack of the
    # rest in two equal-height columns.
    assert 'class="matchup-grid"' in body, "the live slate is not two matchups to a row"
    assert 'class="columns"' in body, "bench regret is not beside the others"
    assert 'class="column-stack"' in body


def test_the_cards_just_load(client, no_network):
    """No pack rip. It was face-down cards, torn open, revealed worst-last -- a
    flourish in front of the content, on a page somebody opens forty times on a
    Sunday."""
    body = client.get("/?punt=steady&team=5").get_data(as_text=True)
    assert "card-face--back" not in body, "the cards still have a back to flip"
    assert "packrip" not in body
    assert (ROOT_STATIC / "js" / "packrip.js").exists() is False


def test_anything_naming_a_team_can_be_opened(client, no_network):
    """Every card and every score row carries a team id, and one delegated
    listener turns all of them into a way to see that manager's afternoon."""
    body = client.get("/?punt=steady&team=5").get_data(as_text=True)
    assert body.count("data-team=") >= 20, "most rows are not openable"
    assert 'id="sheet"' in body, "there is nothing for them to open"

    detail = client.get("/partials/team/5")
    assert detail.status_code == 200
    text = detail.get_data(as_text=True)
    assert "sheet-head" in text and "Starters" in text


def test_the_landing_gate_is_the_same_on_every_device():
    """One landing, one tap, phone and desktop alike.

    The gate used to be touch-only, on the reasoning that a desktop needs no
    permission for the pointer tilt so a splash in front of the scores would be
    theatre. True of the tilt and wrong about the sound: audio needs a gesture
    everywhere, so on a desktop the music started on whatever the visitor
    happened to click first, which is not a start, it is a surprise.
    """
    source = (ROOT_STATIC / "js" / "cards.js").read_text("utf-8")
    assert "isTouchDevice" not in source, "the gate is gated on input modality again"
    assert "if (!gatePassed()) buildGate();" in source
    assert "Turns on the tilt" not in source, "the tilt note is back"


def test_the_theme_plays_once_and_does_not_loop():
    """A theme that comes round every forty-five seconds for four hours stops
    being a theme and becomes a thing people ask you to turn off."""
    source = (ROOT_STATIC / "js" / "audio.js").read_text("utf-8")
    assert "loop: false" in source
    assert "loop: true" not in source


def test_every_card_catches_the_light():
    """Common cards were matte -- the foil sat at zero opacity and that WAS the
    rarity treatment. Rarity is carried by the border, the tier colour and the
    animation speed now, and every card glints."""
    import re

    css = (ROOT_STATIC / "css" / "theme.css").read_text("utf-8")
    block = re.search(r"\.card-face--front::before \{(.*?)\}", css, re.S).group(1)
    opacity = float(re.search(r"opacity:\s*([\d.]+)", block).group(1))
    assert opacity > 0, "the base foil is invisible again, so common cards are matte"


def test_the_team_name_is_the_headline_everywhere():
    """The league calls itself by its team names. The manager stays as the line
    underneath, and on the cards."""
    home = (Path(__file__).resolve().parent.parent / "templates" / "tabs" / "home.html").read_text("utf-8")
    assert "row.manager" not in home, "a table is still headlined by the username"
    assert home.count("row.team") >= 3

    css = (ROOT_STATIC / "css" / "theme.css").read_text("utf-8")
    assert ".mside-id b.mside-manager { display: none; }" in css, (
        "the manager leads the live rows again; note the specificity trap, "
        "`.mside-id b` is 0,1,1 and beats a bare class"
    )
