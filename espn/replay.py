"""Record a Sunday, then replay it on a Tuesday.

Development happens when nothing is live, so without this module nothing beyond
the fetch layer can be exercised at all: no event detection, no commentary, no
audio timing, no pack rip. It is the first milestone for that reason.

A recording is a directory of raw upstream payloads plus a manifest describing
when each arrived relative to the start of the capture. `ReplayTransport` then
implements the same `Transport` protocol as the live client and serves whichever
payload was current at the replay clock's position, so every layer above it
cannot tell the difference -- including the cache, which sees the same payload
changing over time exactly as it would on a real Sunday.

Recordings of the real league contain ten real people's ESPN display names and
account GUIDs, so `.gitignore` tracks only `demo-*` directories. The demo
fixture that ships with the repo is synthetic (see `tools/make_fixture.py`),
which is what lets a fresh clone run the whole app with no cookies at all.
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import RECORDINGS_DIR
from espn import feeds
from espn.client import Transport, UpstreamError

log = logging.getLogger(__name__)

MANIFEST = "manifest.json"


def _slot(feed_name: str, scoring_period: int | None) -> str:
    """The key a payload is filed under. Per-week feeds get one slot per week so
    that replaying week 11 never serves week 10's boxscore."""
    return f"{feed_name}@{scoring_period}" if scoring_period is not None else feed_name


@dataclass
class Entry:
    seq: int
    feed: str
    offset: float
    file: str
    captured_at: str = ""
    scoring_period: int | None = None

    @property
    def slot(self) -> str:
        return _slot(self.feed, self.scoring_period)

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "feed": self.feed,
            "offset": round(self.offset, 3),
            "file": self.file,
            "captured_at": self.captured_at,
            "scoring_period": self.scoring_period,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Entry":
        return cls(
            seq=int(raw.get("seq", 0)),
            feed=str(raw.get("feed", "")),
            offset=float(raw.get("offset", 0.0)),
            file=str(raw.get("file", "")),
            captured_at=str(raw.get("captured_at", "")),
            scoring_period=raw.get("scoring_period"),
        )


@dataclass
class Recording:
    """A directory of payloads, indexed for playback."""

    name: str
    directory: Path
    season: int = 0
    league_id: str = ""
    scoring_period: int | None = None
    synthetic: bool = False
    description: str = ""
    created: str = ""
    entries: list[Entry] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max((e.offset for e in self.entries), default=0.0)

    @property
    def slots(self) -> set[str]:
        return {e.slot for e in self.entries}

    def entries_for(self, slot: str) -> list[Entry]:
        return [e for e in self.entries if e.slot == slot]

    def at(self, slot: str, position: float) -> Entry | None:
        """The payload that was current at `position` seconds into the capture.

        Falls back to the *first* entry when the clock is still before it, which
        matters for feeds captured once at the top of the recording: without the
        fallback, a replay's opening seconds would have no league settings and
        every panel would render its empty state for no reason.
        """
        candidates = self.entries_for(slot)
        if not candidates:
            return None
        current = None
        for entry in candidates:
            if entry.offset <= position:
                current = entry
            else:
                break
        return current or candidates[0]

    def load_payload(self, entry: Entry) -> dict[str, Any]:
        """Read one payload, transparently gunzipping a `.json.gz`.

        A real Sunday is ~480 polls of a payload approaching a megabyte, which is
        not a thing anyone commits. Fantasy JSON is enormously repetitive and
        compresses roughly twentyfold, so the committed fixture is gzipped and
        the format is a per-file detail rather than a mode.
        """
        path = self.directory / entry.file
        try:
            if path.suffix == ".gz":
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
            else:
                with path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise UpstreamError(f"replay: cannot read {entry.file}: {exc}") from exc
        # A payload recorded as a bare list is how ESPN answers an unauthenticated
        # request; keep the same shape the live transport would produce.
        if isinstance(payload, list):
            payload = payload[0] if payload and isinstance(payload[0], dict) else {}
        return payload if isinstance(payload, dict) else {}

    def to_manifest(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "season": self.season,
            "league_id": self.league_id,
            "scoring_period": self.scoring_period,
            "synthetic": self.synthetic,
            "description": self.description,
            "created": self.created,
            "duration": round(self.duration, 3),
            "entries": [e.to_json() for e in self.entries],
        }

    @classmethod
    def load(cls, name_or_path: str) -> "Recording":
        directory = resolve_recording_dir(name_or_path)
        manifest_path = directory / MANIFEST
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"{directory} has no {MANIFEST}. Recordings are made with RECORD=1, "
                f"or generated with tools/make_fixture.py."
            )
        raw = json.loads(manifest_path.read_text("utf-8"))
        entries = sorted(
            (Entry.from_json(e) for e in raw.get("entries", [])),
            key=lambda e: (e.offset, e.seq),
        )
        return cls(
            name=str(raw.get("name") or directory.name),
            directory=directory,
            season=int(raw.get("season") or 0),
            league_id=str(raw.get("league_id") or ""),
            scoring_period=raw.get("scoring_period"),
            synthetic=bool(raw.get("synthetic")),
            description=str(raw.get("description") or ""),
            created=str(raw.get("created") or ""),
            entries=entries,
        )


def resolve_recording_dir(name_or_path: str) -> Path:
    """Accept a bare recording name, a relative path or an absolute one."""
    candidate = Path(name_or_path).expanduser()
    if candidate.is_dir():
        return candidate
    return RECORDINGS_DIR / name_or_path


def list_recordings() -> list[str]:
    if not RECORDINGS_DIR.is_dir():
        return []
    return sorted(d.name for d in RECORDINGS_DIR.iterdir() if (d / MANIFEST).is_file())


class ReplayClock:
    """Wall time scaled by `speed`, pausable, and seekable for tests.

    Replay speed is not a cosmetic convenience. A four-hour Sunday at 60x is four
    minutes, which is the difference between an event-detection change being
    testable in a loop and being testable once a week.
    """

    def __init__(self, speed: float = 1.0, start: float = 0.0) -> None:
        self.speed = max(0.0, speed)
        self._position = start
        self._resumed_at = time.monotonic()
        self._running = self.speed > 0

    @property
    def position(self) -> float:
        if not self._running:
            return self._position
        return self._position + (time.monotonic() - self._resumed_at) * self.speed

    def seek(self, position: float) -> None:
        self._position = max(0.0, position)
        self._resumed_at = time.monotonic()

    def advance(self, seconds: float) -> None:
        self.seek(self.position + seconds)

    def pause(self) -> None:
        self._position = self.position
        self._running = False

    def resume(self) -> None:
        self._resumed_at = time.monotonic()
        self._running = self.speed > 0


class ReplayTransport(Transport):
    """A `Transport` that serves a recording instead of the network."""

    def __init__(self, recording: Recording, speed: float = 1.0) -> None:
        self.recording = recording
        self.clock = ReplayClock(speed=speed)
        self.reads = 0

    @classmethod
    def load(cls, name_or_path: str, speed: float = 1.0) -> "ReplayTransport":
        return cls(Recording.load(name_or_path), speed=speed)

    @property
    def position(self) -> float:
        return min(self.clock.position, self.recording.duration)

    @property
    def finished(self) -> bool:
        return self.clock.position >= self.recording.duration

    @property
    def progress(self) -> float:
        duration = self.recording.duration
        return 1.0 if duration <= 0 else min(1.0, self.clock.position / duration)

    def fetch(
        self, feed: feeds.Feed, season: int, league_id: str, scoring_period: int | None
    ) -> dict[str, Any]:
        self.reads += 1
        slot = _slot(feed.name, scoring_period if feed.per_week else None)
        entry = self.recording.at(slot, self.position)
        if entry is None and feed.per_week:
            # A recording of a single week is the normal case, so a request for
            # some other week should fall back to what was captured rather than
            # rendering an empty page: the alternative is that the app looks
            # broken whenever `scoringPeriodId` drifts by one.
            for candidate_slot in sorted(self.recording.slots):
                if candidate_slot.startswith(feed.name + "@"):
                    entry = self.recording.at(candidate_slot, self.position)
                    break
        if entry is None:
            raise UpstreamError(
                f"replay: {self.recording.name} contains no {feed.name} payload"
            )
        return self.recording.load_payload(entry)

    def describe(self) -> dict[str, Any]:
        return {
            "recording": self.recording.name,
            "synthetic": self.recording.synthetic,
            "speed": self.clock.speed,
            "position": round(self.position, 1),
            "duration": round(self.recording.duration, 1),
            "progress": round(self.progress, 3),
            "finished": self.finished,
            "reads": self.reads,
        }


class RecordingTransport(Transport):
    """Wraps a live transport and writes everything it sees to disk.

    Enabled with `RECORD=1`. Deliberately a wrapper rather than a flag inside
    `LiveTransport`, so that what gets recorded is exactly what the app consumed,
    with no chance of the two diverging.
    """

    def __init__(self, inner: Transport, name: str | None = None, directory: Path | None = None) -> None:
        self.inner = inner
        stamp = datetime.now(timezone.utc)
        self.name = name or stamp.strftime("%Y-%m-%d-%H%M%S")
        self.directory = directory or (RECORDINGS_DIR / self.name)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._started = time.monotonic()
        self._seq = 0
        self._lock = threading.Lock()
        self._entries: list[Entry] = []
        self._meta: dict[str, Any] = {"created": stamp.isoformat(timespec="seconds")}

    def fetch(
        self, feed: feeds.Feed, season: int, league_id: str, scoring_period: int | None
    ) -> dict[str, Any]:
        payload = self.inner.fetch(feed, season, league_id, scoring_period)
        try:
            self._write(feed, payload, season, league_id, scoring_period)
        except OSError as exc:
            # Recording is a development convenience. It must never be the reason
            # a live Sunday goes down, so a full disk logs and carries on.
            log.error("recording write failed (continuing live): %s", exc)
        return payload

    def _write(
        self,
        feed: feeds.Feed,
        payload: dict[str, Any],
        season: int,
        league_id: str,
        scoring_period: int | None,
    ) -> None:
        with self._lock:
            seq = self._seq
            self._seq += 1
            offset = time.monotonic() - self._started
            filename = f"{seq:04d}_{feed.name}.json"
            (self.directory / filename).write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
            self._entries.append(
                Entry(
                    seq=seq,
                    feed=feed.name,
                    offset=offset,
                    file=filename,
                    captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    scoring_period=scoring_period if feed.per_week else None,
                )
            )
            self._meta.update({"season": season, "league_id": league_id})
            self._flush_manifest()

    def _flush_manifest(self) -> None:
        """Rewritten after every capture, not at shutdown.

        A recording is most valuable exactly when the process it was capturing
        died unexpectedly, so a manifest that only exists after a clean exit is
        the wrong design.
        """
        recording = Recording(
            name=self.name,
            directory=self.directory,
            season=int(self._meta.get("season") or 0),
            league_id=str(self._meta.get("league_id") or ""),
            synthetic=False,
            description="Captured live with RECORD=1",
            created=str(self._meta.get("created") or ""),
            entries=list(self._entries),
        )
        (self.directory / MANIFEST).write_text(
            json.dumps(recording.to_manifest(), indent=2), encoding="utf-8"
        )


def write_recording(recording: Recording, payloads: dict[str, dict[str, Any]]) -> Path:
    """Persist a recording built in memory. Used by `tools/make_fixture.py`."""
    recording.directory.mkdir(parents=True, exist_ok=True)
    for entry in recording.entries:
        blob = payloads[entry.file]
        target = recording.directory / entry.file
        text = json.dumps(blob, separators=(",", ":"))
        if target.suffix == ".gz":
            # mtime=0 so a regenerated fixture is byte-identical and does not
            # show up as a spurious diff on every run.
            with gzip.GzipFile(target, "wb", compresslevel=9, mtime=0) as raw:
                raw.write(text.encode("utf-8"))
        else:
            target.write_text(text, encoding="utf-8")
    manifest_path = recording.directory / MANIFEST
    manifest_path.write_text(json.dumps(recording.to_manifest(), indent=2), encoding="utf-8")
    return manifest_path
