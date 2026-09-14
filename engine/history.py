"""What happened, week by week, kept past the point ESPN will tell you.

ESPN serves a past week's box score for as long as the league exists, so this is
not a cache of something already available. It holds the things that are gone
the moment the afternoon ends:

* the Moments -- the touchdowns, lead changes and bench disasters the engine
  detected as they happened, with the commentary it chose. None of that is
  recoverable from a final box score; a settled week looks like a column of
  totals however loud it was at the time.
* what the optimal lineup WAS. Bench regret is computed against slot rules and a
  roster that can be edited afterwards, so recomputing it in March gives a
  different number from the one everybody argued about in November.
* a record that survives ESPN. Leagues get deleted, seasons get archived, and
  cookies expire; this file does not.

SQLite because it is one file, it is in the standard library, and ten people do
not need a server. One gunicorn worker with threads, so `check_same_thread` is
off and a single lock serialises the writes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS weeks (
    season      INTEGER NOT NULL,
    week        INTEGER NOT NULL,
    recorded_at TEXT    NOT NULL,
    settled     INTEGER NOT NULL DEFAULT 0,
    league      TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (season, week)
);

CREATE TABLE IF NOT EXISTS team_weeks (
    season   INTEGER NOT NULL,
    week     INTEGER NOT NULL,
    team_id  INTEGER NOT NULL,
    team     TEXT    NOT NULL,
    manager  TEXT    NOT NULL DEFAULT '',
    score    REAL    NOT NULL DEFAULT 0,
    optimal  REAL    NOT NULL DEFAULT 0,
    regret   REAL    NOT NULL DEFAULT 0,
    opponent INTEGER,
    won      INTEGER,
    PRIMARY KEY (season, week, team_id)
);

CREATE TABLE IF NOT EXISTS moments (
    id       TEXT    PRIMARY KEY,
    season   INTEGER NOT NULL,
    week     INTEGER NOT NULL,
    at       TEXT    NOT NULL,
    kind     TEXT    NOT NULL,
    team_id  INTEGER,
    team     TEXT    NOT NULL DEFAULT '',
    player   TEXT    NOT NULL DEFAULT '',
    delta    REAL    NOT NULL DEFAULT 0,
    said     TEXT    NOT NULL DEFAULT '',
    payload  TEXT    NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS moments_by_week ON moments (season, week, at);
"""


class History:
    """The season so far. Every method is safe to call when the file is not
    writable: a read-only disk must not take the afternoon down."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self._settled_key: tuple | None = None
        self.problems: list[str] = []
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(path), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(SCHEMA)
            self._db.commit()
        except (sqlite3.Error, OSError) as exc:
            # A history that cannot be written is a missing feature, not a
            # broken app: everything on the page comes from ESPN either way.
            # OSError as well as sqlite3.Error, because the first thing that
            # fails is `mkdir` -- a read-only volume or a file where the
            # directory should be raises before sqlite is ever reached.
            self.problems.append(f"history unavailable: {exc}")
            log.warning("history unavailable at %s: %s", path, exc)
            self._db = None

    @property
    def available(self) -> bool:
        return self._db is not None

    # -- writing -----------------------------------------------------------

    def record(self, snapshot, lineups: dict[int, Any] | None = None) -> None:
        """Upsert this week. Called every poll, so the current week is always
        current and a finished one simply stops changing."""
        if self._db is None or not snapshot.teams:
            return
        season, week = snapshot.season, snapshot.scoring_period
        settled = bool(snapshot.settled_weeks.get(snapshot.settings.current_matchup_period))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lineups = lineups or {}

        rows = []
        for matchup in snapshot.live_matchups or snapshot.matchups:
            for side, other in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
                team = snapshot.team(side.team_id)
                lineup = lineups.get(side.team_id)
                won = None
                if matchup.winner not in ("UNDECIDED", ""):
                    won = 1 if side.total > other.total else 0 if side.total < other.total else None
                rows.append((
                    season, week, side.team_id,
                    team.name if team else f"team {side.team_id}",
                    # Never the manager. The column predates the rule that PUNT
                    # shows team names only, and a name kept here is a name one
                    # `SELECT *` away from a page. Left in the schema, blank, so
                    # an existing file opens without a migration.
                    "",
                    round(side.total, 2),
                    round(lineup.total, 2) if lineup else round(side.total, 2),
                    round(lineup.regret, 2) if lineup else 0.0,
                    other.team_id, won,
                ))

        try:
            with self._lock, self._db:
                self._db.execute(
                    "INSERT INTO weeks (season, week, recorded_at, settled, league) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(season, week) DO UPDATE SET "
                    "recorded_at = excluded.recorded_at, settled = excluded.settled, "
                    "league = excluded.league",
                    (season, week, now, int(settled), snapshot.settings.name),
                )
                self._db.executemany(
                    "INSERT INTO team_weeks "
                    "(season, week, team_id, team, manager, score, optimal, regret, opponent, won) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(season, week, team_id) DO UPDATE SET "
                    "team = excluded.team, manager = excluded.manager, score = excluded.score, "
                    "optimal = excluded.optimal, regret = excluded.regret, "
                    "opponent = excluded.opponent, won = excluded.won",
                    rows,
                )
        except sqlite3.Error as exc:
            log.warning("could not record week %s: %s", week, exc)
        self._settle(snapshot, now)

    def _settle(self, snapshot, now: str) -> None:
        """Write the result of every finished week, once its result exists.

        `record` only ever sees the week in progress, and ESPN does not declare
        a winner until the week is processed -- in the small hours of Tuesday,
        after the poller has already rolled onto the next week. So on the first
        real Sunday every stored row kept `won` empty and `settled` false for
        good, and a stat correction to a finished week never reached the file.

        The season schedule carries every settled week's final score and
        winner, so each poll reconciles the file with it. Only the result is
        touched: `optimal` and `regret` were measured against the lineup as it
        was on the day and cannot be rebuilt from a box score, so an existing
        row keeps them and a week PUNT never saw gets its score as its optimal.
        Skipped entirely while the settled scores have not changed.
        """
        settled = snapshot.settled_weeks
        if not settled:
            return  # cold: the demo is week 11, so a Sunday replay always has ten settled weeks
        rows, heads = [], []
        for week, games in sorted(settled.items()):
            heads.append((snapshot.season, week, now, snapshot.settings.name))
            for matchup in games:
                for side, other in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
                    team = snapshot.team(side.team_id)
                    won = 1 if side.total > other.total else 0 if side.total < other.total else None
                    rows.append((snapshot.season, week, side.team_id,
                                 team.name if team else f"team {side.team_id}",
                                 round(side.total, 2), round(side.total, 2),
                                 other.team_id, won))
        key = (snapshot.season, tuple(rows))
        if key == self._settled_key:
            return
        try:
            with self._lock, self._db:
                self._db.executemany(
                    "INSERT INTO weeks (season, week, recorded_at, settled, league) "
                    "VALUES (?, ?, ?, 1, ?) ON CONFLICT(season, week) DO UPDATE SET settled = 1",
                    heads,
                )
                self._db.executemany(
                    "INSERT INTO team_weeks (season, week, team_id, team, score, optimal, opponent, won) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(season, week, team_id) DO UPDATE SET "
                    "team = excluded.team, score = excluded.score, "
                    "opponent = excluded.opponent, won = excluded.won",
                    rows,
                )
            self._settled_key = key
        except sqlite3.Error as exc:
            log.warning("could not settle finished weeks: %s", exc)  # cold: a disk error; `record` just wrote to the same file

    def remember(self, season: int, week: int, moments, lines: dict[str, Any]) -> None:
        """Keep the Moments. These are the part that cannot be rebuilt later."""
        if self._db is None or not moments:
            return
        rows = []
        for moment in moments:
            line = lines.get(moment.id)
            rows.append((
                moment.id, season, week, moment.ts.isoformat(timespec="seconds"),
                moment.kind, moment.team_ids[0] if moment.team_ids else None,
                moment.teams[0] if moment.teams else "",
                moment.player or "", round(moment.delta_points, 2),
                line.text if line else "",
                json.dumps(moment.context or {}, separators=(",", ":")),
            ))
        try:
            with self._lock, self._db:
                # The id is a hash of the play's own facts, so a re-poll that
                # re-detects the same thing writes it once.
                self._db.executemany(
                    "INSERT INTO moments (id, season, week, at, kind, team_id, team, player, "
                    "delta, said, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO NOTHING", rows,
                )
        except sqlite3.Error as exc:
            log.warning("could not record moments: %s", exc)

    # -- reading -----------------------------------------------------------

    def weeks(self, season: int) -> list[dict[str, Any]]:
        """Every week this season that has been seen, newest first."""
        if self._db is None:
            return []
        try:
            with self._lock:
                rows = self._db.execute(
                    "SELECT w.season, w.week, w.recorded_at, w.settled, "
                    "  (SELECT COUNT(*) FROM moments m "
                    "    WHERE m.season = w.season AND m.week = w.week) AS moments "
                    "FROM weeks w WHERE w.season = ? ORDER BY w.week DESC",
                    (season,),
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error:
            return []

    def week(self, season: int, week: int) -> dict[str, Any]:
        """One recorded week: the table as it finished, and what was said."""
        if self._db is None:
            return {}
        try:
            with self._lock:
                teams = self._db.execute(
                    "SELECT * FROM team_weeks WHERE season = ? AND week = ? "
                    "ORDER BY score DESC", (season, week),
                ).fetchall()
                moments = self._db.execute(
                    "SELECT * FROM moments WHERE season = ? AND week = ? ORDER BY at",
                    (season, week),
                ).fetchall()
                head = self._db.execute(
                    "SELECT * FROM weeks WHERE season = ? AND week = ?", (season, week),
                ).fetchone()
        except sqlite3.Error:
            return {}
        return {
            "week": week, "season": season,
            "recorded_at": head["recorded_at"] if head else "",
            "settled": bool(head["settled"]) if head else False,
            # Not `manager`, even blank: old files still hold names in it.
            "teams": [{k: t[k] for k in t.keys() if k != "manager"} for t in teams],
            "moments": [dict(m) for m in moments],
        }

    def stats(self) -> dict[str, Any]:
        if self._db is None:
            return {"available": False, "problems": self.problems}
        try:
            with self._lock:
                weeks = self._db.execute("SELECT COUNT(*) FROM weeks").fetchone()[0]
                moments = self._db.execute("SELECT COUNT(*) FROM moments").fetchone()[0]
            return {"available": True, "weeks": weeks, "moments": moments,
                    "path": str(self.path)}
        except sqlite3.Error as exc:
            return {"available": False, "problems": [str(exc)]}
