"""No real person's name reaches a public surface.

The managers of this league are ten real people, and ESPN's `mTeam` payload
carries their account display names -- their actual usernames. This app serves a
public page at punt.mdeller.com and a public JSON endpoint at `/api/state`, and
for most of its life both published all ten.

The fix is a chokepoint, not a sweep: the name is dropped in
`views/viewmodels.py` and never enters a view model, so there is no second place
to check and no template that can reintroduce it by accident. These tests hold
that line from the outside, against every route, using the fixture's own
invented manager names as the needle.
"""

from __future__ import annotations

import json

import pytest

ROUTES = ["/", "/album", "/cheer", "/swing", "/receipts", "/multiverse",
          "/big-board?tv=1"]
FRAGMENTS = ["/partials/album", "/partials/scorebar", "/partials/cheer",
             "/partials/moments", "/partials/watchnow", "/partials/recap",
             "/partials/team/1"] + [
    f"/partials/panel/{name}" for name in
    ("ticker", "regret", "trouble", "odds", "swings", "allplay", "luck", "shape",
     "grid", "seeds", "gauntlet", "clock", "ledger", "volatility", "swap")] + [
    f"/partials/detail/panel/{name}/1" for name in
    ("shape", "grid", "seeds", "gauntlet", "clock", "ledger", "volatility", "swap")] + [
    "/partials/detail/regret/1", "/partials/detail/trouble/1",
    "/partials/detail/odds/1", "/partials/detail/game/1"]


@pytest.fixture
def managers(app, no_network):
    """The manager names the fixture invents, straight off the model."""
    with app.app_context():
        from views.state import snapshot

        names = {t.manager for t in snapshot().teams if t.manager}
    assert len(names) >= 5, "the fixture has no manager names to look for"
    return names


@pytest.mark.parametrize("path", ROUTES)
def test_no_page_names_a_person(client, managers, path, no_network):
    body = client.get(path).get_data(as_text=True)
    found = sorted(n for n in managers if n in body)
    assert not found, f"{path} publishes {found}"


@pytest.mark.parametrize("path", FRAGMENTS)
def test_no_fragment_names_a_person(client, managers, path, no_network):
    body = client.get(path).get_data(as_text=True)
    found = sorted(n for n in managers if n in body)
    assert not found, f"{path} publishes {found}"


def test_the_public_json_names_nobody(client, managers, no_network):
    """`/api/state` is public by design and fed by the view models, so it
    published every manager in the league for as long as they did."""
    body = client.get("/api/state").get_data(as_text=True)
    found = sorted(n for n in managers if n in body)
    assert not found, f"/api/state publishes {found}"


def test_the_diagnostics_name_nobody(client, managers, no_network):
    """Public by design too, and it has leaked once before: it used to print
    the last four characters of a live session cookie."""
    body = client.get("/api/diagnostics").get_data(as_text=True)
    found = sorted(n for n in managers if n in body)
    assert not found, f"/api/diagnostics publishes {found}"


def test_no_view_model_carries_a_manager(app, managers, no_network):
    """The chokepoint itself. Everything above is downstream of this."""
    with app.app_context():
        from views.state import snapshot, state
        from views import viewmodels as vm

        snap = snapshot()
        live = state().live
        views = {
            "album": vm.album_view(snap, live), "matchup": vm.matchup_view(snap),
            "receipts": vm.receipts_view(snap), "swing": vm.swing_view(snap, live),
            "cheer": vm.cheer_view(snap), "multiverse": vm.multiverse_view(snap, draws=60),
            "watch": vm.watch_now(snap), "shape": vm.shape_view(snap),
            "grid": vm.allplay_view(snap), "gauntlet": vm.gauntlet_view(snap),
            "clock": vm.clock_view(snap), "ledger": vm.ledger_view(snap),
            "volatility": vm.volatility_view(snap), "swap": vm.swap_view(snap),
            "ticker": vm.ticker_view(live, snap),
            "regret_detail": vm.regret_detail(snap, 1),
            "trouble_detail": vm.trouble_detail(snap, 1),
            "odds_detail": vm.odds_detail(snap, 1, draws=60),
            "game_detail": vm.game_detail(snap, next(iter(snap.games), 0)),
        }
    for name, view in views.items():
        blob = json.dumps(view, default=str)
        found = sorted(n for n in managers if f'"{n}"' in blob or f": {n}" in blob)
        assert not found, f"{name}_view carries {found}"


def test_the_recap_names_nobody(app, managers, no_network):
    """The weekly write-up named every manager in the league: it read the fact
    pack's `manager` field, which is the one place the name survived longest."""
    with app.app_context():
        from engine.recap import generate, templated
        from views.state import snapshot, state

        pack = state().live.factpack(snapshot()) if state().live else None
        if pack is None:
            from engine.factpack import build

            pack = build(snapshot(), [])
        for text in (templated(pack), generate(pack, backend=None).text):
            found = sorted(n for n in managers if n in text)
            assert not found, f"the recap says {found}"


def test_the_chooser_leads_with_the_team(no_network):
    """The first-run picker asked which of ten named people is holding the
    phone, and listed them."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "static" / "js" / "identity.js").read_text("utf-8")
    assert "team.manager" not in js, "the chooser lists real names again"
    assert "team.name" in js


def test_nothing_broadcast_over_the_stream_names_a_person(repo, managers, no_network):
    """The gap that let the red-zone banner through.

    Everything above this tests an HTTP response. The countdown overlay is not
    one: it is pushed over server-sent events and painted across the whole
    screen in letters an inch high, which made it the single most visible place
    a real ESPN display name appeared -- and the only one no test was looking
    at. Four usernames, on the bar television, during every red-zone drive.
    """
    import json
    import threading

    from engine.events import EventEngine
    from engine.live import LiveFeed

    snapshots = []
    feed = LiveFeed(fetch=lambda: snapshots[-1], poll_seconds=5,
                    engine=EventEngine(simulate_draws=60))
    seen: list[str] = []
    started = threading.Event()

    def drain():
        listener = feed.listen()
        started.set()
        for message in listener:
            seen.append(str(message))
            if len(seen) > 400:
                break

    worker = threading.Thread(target=drain, daemon=True)
    worker.start()
    started.wait(timeout=5)

    # A whole afternoon, so every kind of broadcast happens at least once.
    duration = int(repo.client.transport.recording.duration)
    for position in range(0, duration + 1, 600):
        repo.client.transport.clock.seek(position)
        repo.client.cache.invalidate()
        snapshots.append(repo.snapshot())
        feed.poll_once()

    blob = " ".join(seen)
    assert blob, "nothing was broadcast at all, so this test proves nothing"
    found = sorted(n for n in managers if n in blob)
    assert not found, f"the stream broadcasts {found}"


def test_the_countdown_overlay_reads_the_team(no_network):
    """It is built in JavaScript from the stream payload, so the field it reads
    has to move with the payload."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "static" / "js"
          / "countdown.js").read_text("utf-8")
    assert "p.manager" not in js, "the overlay lists real names again"
    assert "p.team" in js
