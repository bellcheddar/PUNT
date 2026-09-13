"""Everything true about a week, in one structured object.

This is the retrieval half of the weekly recap, and it is also the *safety* half.
The recap is the only place in the app where generated prose is produced rather
than slot-filled, so the rule is that a sentence may only contain numbers and
names that appear here. That makes the fact pack the definition of what is true,
which is a far stronger guarantee than asking a model to be careful.

It is assembled from a settled snapshot plus the afternoon's Moment timeline, so
nothing in it is an opinion: every figure is either read from ESPN or computed by
`engine/scoring.py` from figures that were.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from engine.scoring import all_play, luck_index, optimal_lineup
from espn.models import LeagueSnapshot


@dataclass
class FactPack:
    """The week, as facts. `to_json` is what the recap prompt is built from."""

    season: int
    week: int
    league: str
    teams: list[dict[str, Any]] = field(default_factory=list)
    matchups: list[dict[str, Any]] = field(default_factory=list)
    highlights: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    generated_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "season": self.season,
            "week": self.week,
            "league": self.league,
            "generated_at": self.generated_at,
            "teams": self.teams,
            "matchups": self.matchups,
            "highlights": self.highlights,
            "counts": self.counts,
        }

    def dumps(self, indent: int = 2) -> str:
        return json.dumps(self.to_json(), indent=indent)

    # -- what the validator is allowed to accept ---------------------------

    def numbers(self) -> set[str]:
        """Every numeral that appears anywhere in the pack, as written.

        Collected by walking the object rather than by listing fields, so a fact
        added later is automatically sayable. The alternative -- a hand-kept list
        -- drifts, and the direction it drifts in is a true sentence being
        rejected, which is the failure that makes people turn a validator off.
        """
        found: set[str] = set()
        _walk_numbers(self.to_json(), found)
        return found

    def names(self) -> set[str]:
        """Every proper noun the recap may use: teams, team names, players."""
        found: set[str] = set()
        _walk_names(self.to_json(), found)
        return found


def _walk_numbers(value: Any, into: set[str]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        into.add(str(value))
    elif isinstance(value, float):
        # Both the exact figure and its rounded forms. A recap that says "112
        # points" about a 112.43 score is correct and is the sentence a person
        # would write; rejecting it would make the validator an enemy.
        into.add(f"{value:g}")
        into.add(f"{value:.1f}")
        into.add(f"{value:.0f}")
        into.add(str(int(value)))
    elif isinstance(value, dict):
        for item in value.values():
            _walk_numbers(item, into)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_numbers(item, into)


#: Keys whose string values are names a recap may use. Everything else in the
#: pack is a label, a slot or a kind, and is not a proper noun.
NAME_KEYS = frozenset({
    "team", "league", "player", "benched", "started",
    "opponent", "winner", "loser", "teams", "pro_team",
})


def _walk_names(value: Any, into: set[str], key: str | None = None) -> None:
    if isinstance(value, str):
        if key in NAME_KEYS:
            into.add(value)
            # Each word separately too: a recap will write "Priya" where the pack
            # says "Priya" but also "Braithwaite" where it says "Wilder
            # Braithwaite", and a surname on its own is not an invention.
            into.update(value.replace("'s", "").split())
    elif isinstance(value, dict):
        for k, item in value.items():
            _walk_names(item, into, k)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk_names(item, into, key)


def build(snapshot: LeagueSnapshot, moments: Iterable | None = None) -> FactPack:
    """Assemble the week from a settled snapshot and its Moment timeline."""
    slots = snapshot.settings.starting_slots
    moments = list(moments or [])

    scores: dict[int, float] = {}
    for matchup in snapshot.live_matchups or snapshot.matchups:
        for side in (matchup.home, matchup.away):
            scores[side.team_id] = round(side.total, 2)
    records = all_play(scores)

    teams: list[dict[str, Any]] = []
    lineups: dict[int, Any] = {}
    for team in snapshot.teams:
        matchup = snapshot.matchup_for(team.id)
        side = matchup.side_for(team.id) if matchup else None
        opponent = matchup.opponent_of(team.id) if matchup else None
        if side is None:
            continue  # cold: every team in the fixture has a side in a matchup
        lineup = optimal_lineup(side.players, slots)
        lineups[team.id] = lineup
        record = records.get(team.id)
        weeks = max(1, team.wins + team.losses + team.ties)
        opponent_team = snapshot.team(opponent.team_id) if opponent else None
        teams.append({
            "team": team.name,
            "score": round(side.total, 2),
            "optimal": lineup.total,
            "bench_regret": lineup.regret,
            "record": team.record,
            "all_play": record.record if record else "",
            "luck": luck_index(team.wins, record.win_pct, weeks) if record else 0.0,
            "opponent": opponent_team.name if opponent_team else "",
            "margin": round(side.total - opponent.total, 2) if opponent else 0.0,
            "won": bool(opponent and side.total > opponent.total),
        })

    teams.sort(key=lambda t: t["score"], reverse=True)

    matchups: list[dict[str, Any]] = []
    for matchup in snapshot.live_matchups or snapshot.matchups:
        home, away = matchup.home, matchup.away
        home_team, away_team = snapshot.team(home.team_id), snapshot.team(away.team_id)
        if not home_team or not away_team:
            continue  # cold: every matchup in the fixture has two teams behind it
        winner, loser = ((home_team, away_team) if home.total >= away.total
                         else (away_team, home_team))
        matchups.append({
            "winner": winner.name,
            "loser": loser.name,
            "margin": round(abs(home.total - away.total), 2),
            "scores": [round(home.total, 2), round(away.total, 2)],
        })

    highlights = _highlights(snapshot, teams, matchups, lineups, moments)
    counts: dict[str, int] = {}
    for moment in moments:
        counts[moment.kind.lower()] = counts.get(moment.kind.lower(), 0) + 1

    return FactPack(
        season=snapshot.season,
        week=snapshot.scoring_period,
        league=snapshot.settings.name,
        teams=teams,
        matchups=matchups,
        highlights=highlights,
        counts=counts,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _highlights(snapshot, teams, matchups, lineups, moments) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if teams:
        out["highest"] = {"team": teams[0]["team"], "score": teams[0]["score"]}
        out["lowest"] = {"team": teams[-1]["team"], "score": teams[-1]["score"]}
    if matchups:
        closest = min(matchups, key=lambda m: m["margin"])
        widest = max(matchups, key=lambda m: m["margin"])
        out["closest"] = closest
        out["blowout"] = widest

    worst_regret = max(teams, key=lambda t: t["bench_regret"], default=None)
    if worst_regret and worst_regret["bench_regret"] > 0:
        team = next((t for t in snapshot.teams if t.name == worst_regret["team"]), None)
        lineup = lineups.get(team.id) if team else None
        swap = lineup.worst_swap if lineup else None
        out["bench_disaster"] = {
            "team": worst_regret["team"],
            "regret": worst_regret["bench_regret"],
            **({"benched": swap[1].name, "benched_points": round(swap[1].points, 2),
                "started": swap[0].name, "started_points": round(swap[0].points, 2),
                "slot": swap[0].slot} if swap else {}),
        }

    # The week's single best player, and who had him.
    best = None
    for matchup in snapshot.live_matchups or snapshot.matchups:
        for side in (matchup.home, matchup.away):
            team = snapshot.team(side.team_id)
            for player in side.starters:
                if best is None or player.points > best["points"]:
                    best = {"player": player.name, "points": round(player.points, 2),
                            "team": team.name if team else "", "slot": player.slot}
    if best:
        out["best_player"] = best

    geese = [m for m in moments if m.kind == "GOOSE_EGG"]
    if geese:
        out["goose_eggs"] = [
            {"team": m.teams[0] if m.teams else "", "player": m.player or "",
             "projected": m.context.get("projected", 0.0)}
            for m in geese
        ]

    swings = [m for m in moments if abs(m.win_prob_delta) > 0.05]
    if swings:
        biggest = max(swings, key=lambda m: abs(m.win_prob_delta))
        out["biggest_swing"] = {
            "team": biggest.teams[0] if biggest.teams else "",
            "player": biggest.player or "",
            "kind": biggest.kind.lower().replace("_", " "),
            "swing": round(abs(biggest.win_prob_delta) * 100, 1),
        }

    doomed = [m for m in moments if m.kind == "DOOM"]
    if doomed:
        out["doomed"] = sorted({m.teams[0] for m in doomed if m.teams})
    return out
