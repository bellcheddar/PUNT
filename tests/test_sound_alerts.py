"""Every sound says what it was.

After the first real Sunday: horns, trombones and whooshes went off on every
phone with nothing on screen to explain them. The fix is structural rather than
a promise. `static/js/alert.js` is the only file that plays a sound effect, and it
cannot do so without putting up a banner; the server puts a matching line on the
LATEST wheel for every Moment that carries a sound and for the red-zone riser and
scratch. These tests hold both halves.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from engine.commentary import Line
from engine.events import Moment
from engine.ticker import PER_POLL, Ticker

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "static" / "js"


def _code(path: Path) -> str:
    """The script with its comments removed. A test that greps source greps the
    prose explaining it too, which has bitten this repository twice."""
    text = path.read_text("utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def test_only_alert_js_plays_a_sound_effect():
    offenders = []
    for path in sorted(JS.glob("*.js")):
        if path.name.endswith(".min.js") or path.name in ("alert.js", "audio.js"):
            continue
        for call in re.findall(r"PUNT_AUDIO\.play\(([^)]*)\)", _code(path)):
            # A tap on the UI bus answers the reader's own finger; it needs no
            # banner to explain it.
            if "'tap'" in call and "bus: 'ui'" in call:
                continue
            offenders.append(f"{path.name}: play({call})")
    assert not offenders, "a sound with no banner: " + "; ".join(offenders)

    audio = _code(JS / "audio.js")
    assert "punt:moment" not in audio, "audio.js is playing Moments behind alert.js's back"


def test_every_sprite_is_explained():
    sprites = set(json.loads((ROOT / "static" / "audio" / "sprite.json").read_text())["sprite"])
    alert = _code(JS / "alert.js")
    table = alert[alert.index("const SOUNDS"):alert.index("};", alert.index("const SOUNDS"))]
    explained = set(re.findall(r"^\s*(\w+):", table, flags=re.M))
    missing = sprites - explained - {"tap"}
    assert not missing, f"sprites with no words for the banner: {sorted(missing)}"


def test_no_script_reads_a_manager_field():
    """`moment.managers` stopped existing when names were removed. Two scripts
    kept reading it, so the takeover's bottom line and the screen reader's
    sentence had been silently blank."""
    for path in sorted(JS.glob("*.js")):
        if not path.name.endswith(".min.js"):
            assert "managers" not in _code(path), path.name


def _moment(snap, kind="TOUCHDOWN", magnitude=0.2):
    side = snap.matchups[0].home
    return Moment(id=f"m-{kind}-{magnitude}", kind=kind, magnitude=magnitude,
                  teams=[snap.team(side.team_id).name], team_ids=[side.team_id],
                  player="p", delta_points=6.0, win_prob_delta=0.05)


def test_a_moment_that_sounded_is_never_cut_from_the_ticker(repo, no_network):
    """The wheel keeps eight lines a poll, by magnitude. A quiet touchdown on a
    busy poll used to lose its line to a bigger score move while every phone
    still played its horn."""
    snap = repo.snapshot()
    ticker = Ticker()
    ticker.observe(snap)  # the silent first look

    moments = [_moment(snap, magnitude=0.01 * i) for i in range(PER_POLL + 4)]
    lines = {m.id: Line(phrase_id="x", text=f"line {m.id}", voice="v", tone="t",
                        magnitude=m.magnitude, moment_id=m.id, kind=m.kind, audio="horn_01")
             for m in moments}
    changes = ticker.observe(snap, moments, lines=lines)

    sounded = [c for c in changes if c.sound]
    assert len(sounded) == len(moments)
    assert all(c.kind == "TOUCHDOWN" for c in sounded), "the wheel should name the kind"
    assert all(c.to_json()["sound"] == "horn_01" for c in sounded)


def test_the_red_zone_sounds_put_a_line_on_the_ticker(no_network):
    from tests.test_live import make_feed

    feed, transport, _ = make_feed(draws=40)
    changes, redzone = [], []

    def capture(payload):
        if payload["event"] == "change":
            changes.append(payload["data"])
        elif payload["event"] == "redzone":
            redzone.append(payload["data"])

    feed._broadcast = capture
    for position in range(0, int(transport.recording.duration), 60):
        transport.clock.seek(position)
        feed.poll_once()

    opens = [e for e in redzone if e["state"] == "enter"]
    stops = [e for e in redzone if e["state"] == "stop"]
    risers = [c for c in changes if c["sound"] == "riser"]
    scratches = [c for c in changes if c["sound"] == "scratch"]
    assert opens and stops
    assert len(risers) == len(opens), "a riser played with no line on the wheel"
    assert len(scratches) == len(stops), "a scratch played with no line on the wheel"
    names = {t.manager for t in feed.snapshot.teams if t.manager}
    for change in risers + scratches:
        assert change["team"] not in names
