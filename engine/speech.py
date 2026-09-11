"""Text to speech for the commentary bus, cached by the hash of what was said.

The build spec asks for Piper, pre-synthesised at deploy time so live playback is
a static file fetch. Two things stop that being the whole design:

* **Most lines are not static.** They carry a player's name, and a week's player
  universe is a hundred and sixty names that are not known until Sunday. Only the
  slot-free lines (all of `filler.yaml`) and the manager-only ones can genuinely
  be pre-rendered.
* **Piper is not installed here.** So the backend is pluggable, the macOS `say`
  binary stands in for local work, and the absence of any backend is a supported
  state rather than a crash: the line is still displayed, it is simply not spoken.

The compromise that makes it fast anyway: synthesis starts the moment the *server*
picks the line, on a small thread pool, not when a phone asks for the audio. By
the time the SSE frame has crossed the room and the browser has issued a request,
the file is usually already there.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Where rendered speech lands. Gitignored: it is derived, and a season of it is
#: a few hundred megabytes.
CACHE_DIR = Path(__file__).resolve().parent.parent / "static" / "audio" / "phrase"

#: How long a request will wait for synthesis that is already in flight. Past
#: this the client gets a 404 and simply does not hear that line, which is a far
#: better outcome than a request holding a worker open during a touchdown.
WAIT_SECONDS = 2.5


@dataclass(frozen=True)
class Voice:
    """One of the two voices. `pbp` is fast and higher energy for play calls,
    `colour` is slower and drier for everything else."""

    name: str
    say_voice: str
    say_rate: int
    piper_model: str


VOICES = {
    "pbp": Voice("pbp", say_voice="Daniel", say_rate=210, piper_model="en_GB-alan-medium"),
    "colour": Voice("colour", say_voice="Serena", say_rate=170, piper_model="en_GB-jenny_dioco-medium"),
}


def phrase_hash(text: str, voice: str) -> str:
    """Keyed on the rendered text, not the phrase id.

    Two managers triggering the same phrase produce different sentences and must
    produce different audio; the same sentence twice must hit the cache. Hashing
    what will actually be spoken is the only key that gets both right.
    """
    return hashlib.blake2b(f"{voice}|{text}".encode(), digest_size=12).hexdigest()


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


class SpeechBackend:
    """Whatever can turn text into an mp3 on this machine."""

    def __init__(self) -> None:
        self.piper = _which("piper", "piper-tts")
        self.say = _which("say")
        self.ffmpeg = _which("ffmpeg")
        if self.piper:
            self.kind = "piper"  # cold: piper is not installed on this Mac; it is what ships on the droplet
        elif self.say and self.ffmpeg:
            self.kind = "say"
        else:
            self.kind = "none"  # cold: this Mac has `say` and ffmpeg, so there is always a backend here

    @property
    def available(self) -> bool:
        return self.kind != "none"

    def render(self, text: str, voice: Voice, target: Path) -> bool:
        try:
            if self.kind == "piper":
                return self._render_piper(text, voice, target)  # cold: the piper render path: needs the binary and a voice model
            if self.kind == "say":
                return self._render_say(text, voice, target)
        except subprocess.CalledProcessError as exc:
            # The exit code on its own says nothing useful; the tail of stderr
            # says exactly what was wrong.
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip()[-300:]
            log.warning("speech synthesis failed (exit %s): %s", exc.returncode, detail)
        except (subprocess.SubprocessError, OSError) as exc:
            log.warning("speech synthesis failed: %s", exc)
        return False

    def _render_piper(self, text: str, voice: Voice, target: Path) -> bool:
        with tempfile.TemporaryDirectory() as work:  # cold: same
            wav = Path(work) / "out.wav"  # cold: same
            subprocess.run(  # cold: same
                [self.piper, "--model", voice.piper_model, "--output_file", str(wav)],
                input=text.encode(), check=True, capture_output=True, timeout=60,
            )
            return self._encode(wav, target)  # cold: same

    def _render_say(self, text: str, voice: Voice, target: Path) -> bool:
        """macOS only, and a stand-in rather than the shipping path.

        Useful because it makes the whole pipeline -- hashing, caching, the
        route, the client fetch, the duck -- testable end to end on this machine
        without installing anything.
        """
        with tempfile.TemporaryDirectory() as work:
            aiff = Path(work) / "out.aiff"
            subprocess.run(
                [self.say, "-v", voice.say_voice, "-r", str(voice.say_rate),
                 "-o", str(aiff), text],
                check=True, capture_output=True, timeout=60,
            )
            return self._encode(aiff, target)

    def _encode(self, source: Path, target: Path) -> bool:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written to a temporary name and moved into place, so a request that
        # arrives mid-encode never gets a half-written file. The move is atomic
        # within one filesystem.
        staging = target.with_suffix(".part")
        # `-f mp3` is not optional here: ffmpeg infers the output format from the
        # extension, and the staging file deliberately has none it recognises.
        # Without it this exits 234 with nothing in the message but the number.
        subprocess.run(
            [self.ffmpeg or "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
             "-codec:a", "libmp3lame", "-b:a", "64k", "-ar", "44100", "-ac", "1",
             "-f", "mp3", str(staging)],
            check=True, capture_output=True, timeout=120,
        )
        staging.replace(target)
        return target.is_file()


class SpeechCache:
    """Renders on a small pool, serves from disk, never blocks a poll."""

    def __init__(self, directory: Path = CACHE_DIR, workers: int = 2) -> None:
        self.directory = directory
        self.backend = SpeechBackend()
        self.hits = 0
        self.renders = 0
        self.failures = 0
        self._inflight: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        # Two workers: enough that a burst of three touchdowns does not queue
        # behind one another, few enough that synthesis cannot starve the poller.
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="punt-tts")
        if self.backend.available:
            directory.mkdir(parents=True, exist_ok=True)
        log.info("speech backend: %s", self.backend.kind)

    def path_for(self, digest: str) -> Path:
        return self.directory / f"{digest}.mp3"

    def url_for(self, text: str, voice: str) -> str | None:
        """The URL a client should fetch, and the point synthesis starts.

        Called when the *server* picks the line rather than when a phone asks for
        it, which is what hides the synthesis latency behind the SSE round trip.
        """
        if not self.backend.available or not text.strip():
            return None
        digest = phrase_hash(text, voice)
        self.ensure(digest, text, voice)
        return f"/audio/phrase/{digest}.mp3"

    def ensure(self, digest: str, text: str, voice: str) -> None:
        target = self.path_for(digest)
        if target.is_file():
            self.hits += 1
            return
        with self._lock:
            if digest in self._inflight:
                return
            done = threading.Event()
            self._inflight[digest] = done
        self._pool.submit(self._render, digest, text, voice, done)

    def _render(self, digest: str, text: str, voice: str, done: threading.Event) -> None:
        try:
            profile = VOICES.get(voice) or VOICES["pbp"]
            started = time.monotonic()
            if self.backend.render(text, profile, self.path_for(digest)):
                self.renders += 1
                log.debug("spoke %r in %.2fs", text[:40], time.monotonic() - started)
            else:
                self.failures += 1
        finally:
            done.set()
            with self._lock:
                self._inflight.pop(digest, None)

    def wait_for(self, digest: str, timeout: float = WAIT_SECONDS) -> Path | None:
        """Block briefly for synthesis already in flight, then give up.

        Giving up is the important half. A request that waits indefinitely for a
        line holds a worker open through the loudest minute of the afternoon,
        which is exactly when the app must not be short of workers.
        """
        target = self.path_for(digest)
        if target.is_file():
            return target
        with self._lock:
            event = self._inflight.get(digest)
        if event is not None and event.wait(timeout) and target.is_file():
            return target
        return None

    def stats(self) -> dict:
        with self._lock:
            inflight = len(self._inflight)
        cached = len(list(self.directory.glob("*.mp3"))) if self.directory.is_dir() else 0
        return {
            "backend": self.backend.kind,
            "cached": cached,
            "hits": self.hits,
            "renders": self.renders,
            "failures": self.failures,
            "in_flight": inflight,
        }
