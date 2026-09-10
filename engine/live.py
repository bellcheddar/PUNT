"""The live loop: one poller, one event engine, many listeners.

Everything else in `engine/` is pure -- give it a snapshot, get numbers back.
This is the piece that owns time.

A background thread polls at the configured cadence, feeds each snapshot to the
`EventEngine`, and fans the resulting Moments out to whoever is listening. The
alternative, detecting events lazily inside a request, does not work for this
app: SSE is supposed to deliver a touchdown the moment it lands, and lazily means
"whenever somebody's phone happens to ask", which is exactly the twenty-five
second lag the stream exists to remove.

One thread per process, and the deployment runs one worker with threads rather
than several processes, so ESPN sees one poll regardless of how many phones are
in the room. A second worker would need the moment buffer moved into Redis; the
gunicorn config says so where somebody would be about to change it.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from engine.commentary import Commentator, Line, PhraseBank
from engine.events import EventEngine, Moment
from espn.models import LeagueSnapshot

log = logging.getLogger(__name__)

#: How many recent Moments to keep for a phone that has just joined. A whole
#: Sunday is a few hundred, so this is the last hour or so of a busy afternoon:
#: enough to fill the commentary feed on first paint without replaying the day.
BUFFER = 120

#: A listener that has not been read from in this many queued messages has gone
#: away (a phone in a pocket, a closed tab). Dropping it is correct; blocking the
#: poller on it would stop the whole room's updates for one dead connection.
LISTENER_BACKLOG = 64


# eq=False keeps identity hashing, so listeners can live in a set. The generated
# __eq__ would compare two clients by the contents of their queues, which is both
# meaningless and unhashable.
@dataclass(eq=False)
class Listener:
    """One connected SSE client."""

    queue: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=LISTENER_BACKLOG))
    dropped: int = 0

    def offer(self, payload: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(payload)
        except queue.Full:
            self.dropped += 1


class LiveFeed:
    """Polls, detects, buffers and broadcasts."""

    def __init__(
        self,
        fetch: Callable[[], LeagueSnapshot],
        poll_seconds: float = 30.0,
        engine: EventEngine | None = None,
        commentator: Commentator | None = None,
    ) -> None:
        self.fetch = fetch
        self.poll_seconds = max(5.0, poll_seconds)
        self.engine = engine or EventEngine()
        # The line is chosen on the server, not on each phone. Ten phones in one
        # room must hear the same sentence: picking client-side would give ten
        # different lines for the same touchdown, which is worse than silence.
        self.commentator = commentator
        self.lines: dict[str, Line] = {}
        self.moments: deque[Moment] = deque(maxlen=BUFFER)
        self.snapshot: LeagueSnapshot | None = None
        self.polls = 0
        self.failures = 0
        self.last_poll_at: float | None = None
        self.last_error: str = ""

        self._listeners: set[Listener] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="punt-live", daemon=True)
        self._thread.start()
        log.info("live feed started, polling every %.0fs", self.poll_seconds)

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- listeners ---------------------------------------------------------

    def listen(self) -> Iterator[dict[str, Any]]:
        """Subscribe. Yields payloads until the client disconnects.

        New listeners get the recent buffer immediately, so a phone that unlocks
        mid-afternoon sees the commentary feed populated rather than an empty
        panel until the next thing happens.
        """
        listener = Listener()
        with self._lock:
            self._listeners.add(listener)
            backlog = list(self.moments)[-20:]
        try:
            for moment in backlog:
                payload = moment.to_json()
                line = self.lines.get(moment.id)
                if line is not None:
                    payload["line"] = line.to_json()
                # `replayed` so the client shows it in the feed without firing a
                # horn for a touchdown that happened forty minutes ago.
                listener.offer({"event": "moment", "data": payload, "replayed": True})
            while True:
                try:
                    yield listener.queue.get(timeout=15.0)
                except queue.Empty:
                    # A comment frame keeps proxies and phones from timing the
                    # connection out during a quiet stretch of a Sunday evening.
                    yield {"event": "keepalive", "data": {"ts": time.time()}}
        finally:
            with self._lock:
                self._listeners.discard(listener)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener.offer(payload)

    # -- the loop ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - the loop must outlive any one poll
                self.failures += 1
                self.last_error = str(exc)
                log.exception("live poll failed (%d in a row)", self.failures)
            # Sleep the remainder, so a slow poll does not push the cadence out.
            self._stop.wait(max(1.0, self.poll_seconds - (time.monotonic() - started)))

    def poll_once(self) -> list[Moment]:
        """One cycle. Public so a test can drive the loop without a thread."""
        snapshot = self.fetch()
        self.snapshot = snapshot
        self.polls += 1
        self.last_poll_at = time.time()

        moments = self.engine.ingest(snapshot)
        if moments:
            self.moments.extend(moments)
            for moment in moments:
                payload = moment.to_json()
                line = self._commentate(moment, snapshot.scoring_period)
                if line is not None:
                    payload["line"] = line.to_json()
                self._broadcast({"event": "moment", "data": payload})
        self._broadcast({
            "event": "tick",
            "data": {
                "scoring_period": snapshot.scoring_period,
                "stale": snapshot.stale,
                "scores": {
                    str(side.team_id): round(side.total, 2)
                    for m in snapshot.matchups for side in (m.home, m.away)
                },
            },
        })
        self.failures = 0
        self.last_error = ""
        return moments

    def _commentate(self, moment: Moment, week: int) -> Line | None:
        """One line for this Moment, remembered so the feed and the stream agree.

        Silence is a valid answer and is returned as one: a Moment with no line
        still reaches the stream, still moves the scores, and simply says nothing.
        """
        if self.commentator is None:
            return None
        try:
            line = self.commentator.say(moment, week=week)
        except Exception:  # noqa: BLE001 - a bad phrase must not stop the poll
            log.exception("commentary failed for %s", moment.kind)
            return None
        if line is not None:
            self.lines[moment.id] = line
            # Bounded alongside the moment buffer, or a long Sunday leaks one
            # entry per event for as long as the process lives.
            if len(self.lines) > BUFFER * 2:
                keep = {m.id for m in self.moments}
                self.lines = {k: v for k, v in self.lines.items() if k in keep}
        return line

    def line_for(self, moment: Moment) -> Line | None:
        return self.lines.get(moment.id)

    def recent(self, limit: int = 30, kinds: set[str] | None = None) -> list[Moment]:
        items = [m for m in self.moments if not kinds or m.kind in kinds]
        return list(reversed(items[-limit:]))

    def stats(self) -> dict[str, Any]:
        with self._lock:
            listeners = len(self._listeners)
            dropped = sum(l.dropped for l in self._listeners)
        return {
            "running": self.running,
            "polls": self.polls,
            "failures": self.failures,
            "last_error": self.last_error,
            "last_poll_at": self.last_poll_at,
            "buffered_moments": len(self.moments),
            "listeners": listeners,
            "dropped_messages": dropped,
            "poll_seconds": self.poll_seconds,
        }
