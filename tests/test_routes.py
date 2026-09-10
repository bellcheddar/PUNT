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
    other = create_app(cfg=cfg, client=EspnClient(transport, 2025, "demo", TTLCache()))
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
