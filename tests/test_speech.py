"""The speech cache.

The behaviour that matters is what happens when synthesis is slow, missing or
broken, because all three are normal: Piper is not installed everywhere, a
request can arrive mid-render, and the line is still on screen either way.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from engine.speech import SpeechBackend, SpeechCache, phrase_hash


def test_the_key_is_what_will_actually_be_spoken():
    """Not the phrase id. Two managers triggering the same phrase produce
    different sentences and must produce different audio; the same sentence
    twice must hit the cache."""
    a = "Wilder Braithwaite finds the end zone! 8.4 for Priya."
    b = "Wilder Braithwaite finds the end zone! 8.4 for Gus."
    assert phrase_hash(a, "pbp") == phrase_hash(a, "pbp")
    assert phrase_hash(a, "pbp") != phrase_hash(b, "pbp")
    assert phrase_hash(a, "pbp") != phrase_hash(a, "colour")
    assert len(phrase_hash(a, "pbp")) == 24


def test_no_backend_is_a_supported_state(tmp_path, monkeypatch):
    """The line is still displayed. It is simply not spoken, and nothing
    anywhere waits on it."""
    monkeypatch.setattr("engine.speech._which", lambda *names: None)
    cache = SpeechCache(directory=tmp_path)
    assert cache.backend.kind == "none"
    assert cache.backend.available is False
    assert cache.url_for("anything at all", "pbp") is None
    assert cache.wait_for(phrase_hash("anything at all", "pbp"), timeout=0.1) is None


def test_an_already_rendered_line_is_an_instant_hit(tmp_path, monkeypatch):
    monkeypatch.setattr("engine.speech._which", lambda *names: "/bin/true")
    cache = SpeechCache(directory=tmp_path)
    text, voice = "Touchdown.", "pbp"
    digest = phrase_hash(text, voice)
    cache.path_for(digest).write_bytes(b"not really an mp3")

    started = time.monotonic()
    url = cache.url_for(text, voice)
    assert url == f"/audio/phrase/{digest}.mp3"
    assert time.monotonic() - started < 0.05
    assert cache.hits == 1
    assert cache.renders == 0


def test_waiting_gives_up_rather_than_holding_a_worker(tmp_path, monkeypatch):
    """A request that waits indefinitely holds a worker open through the loudest
    minute of the afternoon, which is exactly when the app must not be short of
    workers. A silent line is the better failure."""
    monkeypatch.setattr("engine.speech._which", lambda *names: "/bin/true")
    cache = SpeechCache(directory=tmp_path)

    started = time.monotonic()
    assert cache.wait_for("deadbeefdeadbeefdeadbeef", timeout=0.2) is None
    assert time.monotonic() - started < 1.0


def test_a_render_in_flight_is_not_started_twice(tmp_path, monkeypatch):
    """Ten phones asking for the same line must not start ten renders."""
    monkeypatch.setattr("engine.speech._which", lambda *names: "/bin/true")
    cache = SpeechCache(directory=tmp_path)

    calls = []
    gate = threading.Event()

    def slow_render(text, voice, target):
        calls.append(text)
        gate.wait(2.0)
        return False

    cache.backend.render = slow_render
    for _ in range(10):
        cache.url_for("the same line", "pbp")
    gate.set()
    time.sleep(0.2)
    assert len(calls) == 1, f"{len(calls)} renders started for one line"


def test_a_failing_backend_does_not_raise(tmp_path, monkeypatch):
    """Synthesis runs on a pool behind the poller. An exception there must not
    reach the poll that scheduled it."""
    monkeypatch.setattr("engine.speech._which", lambda *names: "/bin/true")
    cache = SpeechCache(directory=tmp_path)
    cache.backend.render = lambda *args: (_ for _ in ()).throw(RuntimeError("no voice"))

    cache.url_for("something", "pbp")
    time.sleep(0.3)
    assert cache.stats()["in_flight"] == 0


@pytest.mark.skipif(not SpeechBackend().available, reason="no TTS backend on this machine")
def test_a_real_line_renders_to_a_playable_file(tmp_path):
    """End to end, when a backend exists. Skipped rather than failed elsewhere:
    a machine without Piper is a supported machine."""
    cache = SpeechCache(directory=tmp_path)
    text = "Regret Merchants are through a hundred."
    cache.url_for(text, "pbp")
    path = cache.wait_for(phrase_hash(text, "pbp"), timeout=30)
    assert path is not None and path.is_file()
    assert path.stat().st_size > 2000, "an mp3 that small is not speech"
    assert path.read_bytes()[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3"), "not an mp3"
    assert not list(tmp_path.glob("*.part")), "a staging file was left behind"
