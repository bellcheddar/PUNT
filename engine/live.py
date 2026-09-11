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
from engine.factpack import FactPack, build as build_factpack
from engine.speech import SpeechCache
from engine.ticker import Ticker
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
        speech: SpeechCache | None = None,
        on_week_change: Callable[[int | None, LeagueSnapshot], None] | None = None,
        after_poll: Callable[[LeagueSnapshot], None] | None = None,
        ranks: Callable[[LeagueSnapshot], dict[int, int]] | None = None,
    ) -> None:
        #: Album positions, supplied by the caller. The ticker reports a team
        #: moving up the album, and the rating that decides the album lives in a
        #: view model: `engine/` cannot import one without a cycle, so the
        #: dependency is injected rather than imported.
        self.ranks = ranks
        self.fetch = fetch
        #: Called at the end of every successful poll, after the Moments have
        #: been detected and broadcast. Broadcasting first is deliberate: the
        #: phones in the room get the touchdown before anything touches a disk.
        self.after_poll = after_poll
        #: Called with (the week that just ended, the first snapshot of the new
        #: one) the moment ESPN's scoring period moves on. The hook exists so
        #: the feed does not have to know what a database is: everything it
        #: keeps is per-week and in memory, and something else decides whether
        #: the week that just ended is worth writing down.
        self.on_week_change = on_week_change
        self.poll_seconds = max(5.0, poll_seconds)
        self.engine = engine or EventEngine()
        # The line is chosen on the server, not on each phone. Ten phones in one
        # room must hear the same sentence: picking client-side would give ten
        # different lines for the same touchdown, which is worse than silence.
        self.commentator = commentator
        self.speech = speech
        self.lines: dict[str, Line] = {}
        self.moments: deque[Moment] = deque(maxlen=BUFFER)
        self.snapshot: LeagueSnapshot | None = None
        #: What has moved since the last poll, which is a different question
        #: from what has happened. See `engine/ticker.py`.
        self.ticker = Ticker()
        self.polls = 0
        self.failures = 0
        self.last_poll_at: float | None = None
        self.last_error: str = ""

        #: The week's rare Moments, kept whole, plus exact counts of all of them.
        #: The rolling `moments` buffer holds the last two hours and is the wrong
        #: source for a weekly recap: by Monday it contains the tail of Sunday
        #: night and nothing else. These are the kinds the fact pack actually
        #: reads, and there are only ever a few dozen of them in a week.
        self.week: int | None = None
        self.notable: list[Moment] = []
        self.week_counts: dict[str, int] = {}
        #: The lowest win probability each team has seen this week.
        #:
        #: A Legendary card is a win from under 10%, and "under 10%" is a thing
        #: that was true at some point, not a thing that is true now. Reading the
        #: current number instead marked whoever was *losing* as legendary, and
        #: marked nobody at all once the games finished and every probability was
        #: 1.0 or 0.0 -- so the tier could never actually be awarded.
        self.week_low: dict[int, float] = {}

        #: pro_team_id -> what we knew when the drive reached the red zone.
        #: Diffed each poll to open and close the countdown overlay.
        self._redzone: dict[int, dict[str, Any]] = {}

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
                    payload["line"] = self._with_speech(line)
                # `replayed` so the client shows it in the feed without firing a
                # horn for a touchdown that happened forty minutes ago.
                listener.offer({"event": "moment", "data": payload, "replayed": True})
            while True:
                try:
                    yield listener.queue.get(timeout=15.0)
                except queue.Empty:
                    # A comment frame keeps proxies and phones from timing the
                    # connection out during a quiet stretch of a Sunday evening.
                    yield {"event": "keepalive", "data": {"ts": time.time()}}  # cold: a fifteen-second quiet stretch; the driver never waits that long
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

        self._accumulate(snapshot, moments := self.engine.ingest(snapshot))
        if moments:
            self.moments.extend(moments)
            for moment in moments:
                payload = moment.to_json()
                line = self._commentate(moment, snapshot.scoring_period)
                if line is not None:
                    payload["line"] = self._with_speech(line)
                self._broadcast({"event": "moment", "data": payload})
            # Written on every poll that fired something rather than at shutdown,
            # because the restart this protects against is usually the kind that
            # does not get to run a shutdown hook.
            self.engine.persist()
        # After the lines are chosen, so the commentary can be folded into the
        # same ticker: the room should read one strip, not two.
        self._tick(snapshot, moments)
        self._redzone_events(snapshot)
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
        if self.after_poll is not None:
            try:
                self.after_poll(snapshot)
            except Exception:  # noqa: BLE001
                # Same rule as the week-change hook: a recorder that cannot
                # write must not take the scoreboard down with it.
                log.exception("the after-poll hook failed")
        self.failures = 0
        self.last_error = ""
        return moments

    def _tick(self, snapshot: LeagueSnapshot, moments: list[Moment]) -> None:
        """Run the differ and push whatever it found to every phone.

        Everything expensive is computed once here and handed in. Both of these
        are memoised on the state that produced them, so the panels that poll
        for the same figures a moment later get them free.
        """
        from engine.simulate import playoff_odds, team_probabilities  # noqa: PLC0415

        try:
            odds = playoff_odds(snapshot) if snapshot.season_schedule else {}
            changes = self.ticker.observe(
                snapshot, moments,
                probabilities=team_probabilities(snapshot),
                odds=odds,
                ranks=self.ranks(snapshot) if self.ranks else None,
                lines=self.lines,
            )
        except Exception:  # noqa: BLE001
            # The ticker is a strip along the top. The scores are the product.
            log.exception("the ticker failed")
            return
        for change in changes:
            self._broadcast({"event": "change", "data": change.to_json()})

    #: Moment kinds the weekly fact pack reads. Everything else is counted but
    #: not kept: there are 117 big plays in a Sunday and the recap needs the
    #: number, not the list.
    NOTABLE = frozenset({"GOOSE_EGG", "DOOM", "CLINCH", "LEAD_CHANGE", "BENCH_DISASTER"})

    #: A ceiling, so a pathological week cannot grow this without bound.
    MAX_NOTABLE = 400

    def _accumulate(self, snapshot: LeagueSnapshot, moments: list[Moment]) -> None:
        """Keep the week's rare Moments and exact per-kind counts."""
        if snapshot.scoring_period != self.week:
            self._roll(snapshot)

        for team_id, probability in self.engine.win_probabilities.items():
            previous = self.week_low.get(team_id)
            if previous is None or probability < previous:
                self.week_low[team_id] = probability
        for moment in moments:
            self.week_counts[moment.kind] = self.week_counts.get(moment.kind, 0) + 1
            if moment.kind in self.NOTABLE and len(self.notable) < self.MAX_NOTABLE:
                self.notable.append(moment)

    def _roll(self, snapshot: LeagueSnapshot) -> None:
        """ESPN has moved to a new scoring period. Start again.

        The rollover happens on a Tuesday morning with nobody watching, so this
        has to be automatic and it has to be complete. Three of these used to be
        cleared and three did not, which is the kind of half-reset that looks
        fine on the Tuesday and produces last Sunday's commentary underneath
        this Sunday's scores on the following weekend, when the two-hour buffer
        finally has something to push out.
        """
        ended, self.week = self.week, snapshot.scoring_period
        if ended is not None:
            log.info("week %s has ended; rolling on to week %s", ended, self.week)

        # Per-week and therefore emptied. `notable` and `week_counts` feed the
        # recap, `week_low` mints Legendary cards, `moments` and `lines` are the
        # commentary feed, and `_redzone` is a live overlay that cannot possibly
        # still be open a week later.
        self.notable = []
        self.week_counts = {}
        self.week_low = {}
        self.moments.clear()
        self.lines.clear()
        self._redzone.clear()
        self.ticker.reset()

        # Deliberately NOT cleared: the event engine's set of fired ids. Every
        # Moment id is hashed with its week, so last week's ids cannot suppress
        # this week's plays, and throwing the set away would mean a restart
        # during the rollover replays whatever it had already announced.
        if self.on_week_change is not None:
            try:
                self.on_week_change(ended, snapshot)
            except Exception:  # noqa: BLE001
                # A failed write must not stop the scores. The hook is a record
                # of the afternoon; the afternoon is the product.
                log.exception("the week-change hook failed")

    def factpack(self, snapshot: LeagueSnapshot | None = None) -> FactPack | None:
        """The week as facts, or nothing if there is not a week yet."""
        snapshot = snapshot or self.snapshot
        if snapshot is None or not snapshot.teams:
            return None  # cold: the factpack before the first poll returns
        pack = build_factpack(snapshot, self.notable)
        # Counts come from the accumulator rather than from `notable`, which is
        # deliberately a subset: the recap says "74 touchdowns" and there is no
        # list of 74 touchdowns anywhere in memory.
        pack.counts = {kind.lower(): count for kind, count in sorted(self.week_counts.items())}
        return pack

    #: A points jump at least this big while a drive was inside the five is a
    #: touchdown rather than a two-yard carry. The same threshold the event
    #: engine uses, and for the same reason: the feed carries totals, not plays.
    SCORE_DELTA = 5.9

    def _redzone_events(self, snapshot: LeagueSnapshot) -> None:
        """Open and close the countdown overlay.

        The overlay is a promise: it says something is about to happen. So the
        close has to be as reliable as the open, and it has to say which way it
        went -- a drive that stalls on the two gets a record scratch, not a horn.
        Left open, it would sit over the scores for the rest of the afternoon.
        """
        live_now: dict[int, dict[str, Any]] = {}

        for game in snapshot.red_zone_games:
            if not game.possession:
                continue
            involved = []
            for matchup in snapshot.matchups:
                for side in (matchup.home, matchup.away):
                    team = snapshot.team(side.team_id)
                    for player in side.starters:
                        if player.pro_team_id == game.pro_team_id:
                            involved.append({
                                "player": player.name,
                                "player_id": player.id,
                                "points": round(player.points, 2),
                                "manager": team.manager if team else "?",
                                "team_id": side.team_id,
                            })
            # Nobody in the league owns anybody on this drive, so nobody in the
            # bar cares. The overlay is for the room, not for the football.
            if involved:
                live_now[game.pro_team_id] = {"game": game, "involved": involved}

        for pro_team_id, info in live_now.items():
            if pro_team_id in self._redzone:
                continue
            self._redzone[pro_team_id] = {
                "at": time.time(),
                "points": {p["player_id"]: p["points"] for p in info["involved"]},
                "involved": info["involved"],
            }
            self._broadcast({"event": "redzone", "data": {
                "state": "enter",
                "pro_team": info["game"].abbrev,
                "opponent": info["game"].opponent,
                "quarter": info["game"].period,
                "clock": info["game"].clock,
                "down_distance": info["game"].down_distance,
                "involved": info["involved"],
            }})

        for pro_team_id in [k for k in self._redzone if k not in live_now]:
            opened = self._redzone.pop(pro_team_id)
            scored = False
            for matchup in snapshot.matchups:
                for side in (matchup.home, matchup.away):
                    for player in side.players:
                        before = opened["points"].get(player.id)
                        if before is not None and player.points - before >= self.SCORE_DELTA:
                            scored = True
            self._broadcast({"event": "redzone", "data": {
                "state": "score" if scored else "stop",
                "involved": opened["involved"],
                "seconds": round(time.time() - opened["at"], 1),
            }})

    def _commentate(self, moment: Moment, week: int) -> Line | None:
        """One line for this Moment, remembered so the feed and the stream agree.

        Silence is a valid answer and is returned as one: a Moment with no line
        still reaches the stream, still moves the scores, and simply says nothing.
        """
        if self.commentator is None:
            return None  # cold: the app always has a commentator; running without one is a supported config
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

    def _with_speech(self, line: Line) -> dict:
        """The line, plus where its audio will be.

        Asking for the URL is what starts synthesis, and it happens here rather
        than when a phone requests the file: the SSE frame then has to cross the
        room and the browser has to issue a request, which is most of a second in
        which the renderer is already working."""
        payload = line.to_json()
        if self.speech is not None:
            url = self.speech.url_for(line.text, line.voice)
            if url:
                payload["speech"] = url  # cold: no TTS backend here, so there is never a URL to attach
        return payload

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
            "speech": self.speech.stats() if self.speech is not None else None,
            "red_zone": sorted(self._redzone),
        }
