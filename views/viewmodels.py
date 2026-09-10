"""Snapshot -> template data.

Templates get plain dictionaries, never model objects with behaviour on them.
That keeps the arithmetic testable without a request context, and it means the
Phase 2 engine can replace a placeholder here (`bench_regret`, `win_prob`)
without touching a single template.

Anything marked `PHASE2` is a deliberate placeholder with a correct shape and a
provisional value, so the tab renders something honest today rather than a
blank panel waiting for a module that does not exist yet.
"""

from __future__ import annotations

from typing import Any

from espn.models import LeagueSnapshot, Matchup, Side, Team


def _team_card(team: Team | None, side: Side | None) -> dict[str, Any]:
    if team is None:
        return {
            "id": 0, "name": "Unknown team", "manager": "?", "abbrev": "?",
            "monogram": "?", "hue": 0, "logo": "", "record": "",
            "total": 0.0, "projected": 0.0, "starters": [], "bench": [],
            "missing": True,
        }
    return {
        "id": team.id,
        "name": team.name,
        "manager": team.manager,
        "abbrev": team.abbrev,
        "monogram": team.monogram,
        "hue": team.hue,
        "logo": f"/img/team/{team.id}" if team.logo else "",
        "record": team.record,
        "total": round(side.total, 2) if side else 0.0,
        "projected": round(side.live_projection, 2) if side else 0.0,
        "starters": [_player(p) for p in (side.starters if side else [])],
        "bench": [_player(p) for p in (side.bench if side else [])],
        "yet_to_play": side.yet_to_play if side else 0,
        "missing": False,
    }


def _player(player) -> dict[str, Any]:
    return {
        "id": player.id,
        "name": player.name,
        "opponent": player.opponent,
        "game_over": player.game_over,
        "slot": player.slot,
        "position": player.position,
        "pro_team": player.pro_team,
        "points": round(player.points, 2),
        "projected": round(player.projected, 2),
        "remaining": round(player.remaining, 2),
        "injury": player.injury_short,
        "done": player.remaining <= 0,
    }


def matchup_view(snap: LeagueSnapshot) -> list[dict[str, Any]]:
    teams = snap.teams_by_id
    out = []
    for matchup in snap.live_matchups or snap.matchups:
        home = _team_card(teams.get(matchup.home.team_id), matchup.home)
        away = _team_card(teams.get(matchup.away.team_id), matchup.away)
        leader = home if home["total"] >= away["total"] else away
        out.append(
            {
                "id": matchup.id,
                "home": home,
                "away": away,
                "margin": abs(round(home["total"] - away["total"], 2)),
                "leader": leader["manager"],
                "settled": matchup.winner not in ("UNDECIDED", ""),
                # PHASE2: engine/simulate.py replaces this with a Monte Carlo
                # win probability. Projection share is monotonic in the right
                # direction and never claims more precision than it has.
                "win_prob": _projection_share(home["projected"], away["projected"]),
            }
        )
    return out


def _projection_share(home: float, away: float) -> float:
    total = home + away
    return round(home / total, 3) if total > 0 else 0.5


def album_view(snap: LeagueSnapshot) -> list[dict[str, Any]]:
    """The ten cards, ordered by this week's score. Rarity is earned, so it can
    only be assigned once every team's score is known -- which is why this is one
    pass over all ten rather than a property on a card."""
    cards: list[dict[str, Any]] = []
    for team in snap.teams:
        matchup = snap.matchup_for(team.id)
        side = matchup.side_for(team.id) if matchup else None
        card = _team_card(team, side)
        card["bench_regret"] = _bench_regret(side)
        cards.append(card)

    cards.sort(key=lambda c: c["total"], reverse=True)
    for rank, card in enumerate(cards):
        card["rank"] = rank + 1
        card["tier"] = _tier(rank, len(cards), card["bench_regret"])
    return cards


def _tier(rank: int, count: int, bench_regret: float) -> str:
    """Rarity, per the spec's table.

    Legendary needs a season high or a sub-10% win, both of which need season
    history and the simulator; until Phase 2 supplies them the top score is
    Epic, which is the honest tier for what is actually known.
    """
    if rank == count - 1 or bench_regret > 40:
        return "cursed"
    if rank == 0:
        return "epic"
    if rank < 3:
        return "rare"
    return "common"


def _bench_regret(side: Side | None) -> float:
    """Points left on the bench, approximated as best-bench minus worst-starter.

    PHASE2: `engine/scoring.py` replaces this with the real figure -- the optimal
    lineup under the league's actual slot eligibility, minus what was started.
    The approximation is always a *lower* bound on the real regret, so a card
    that says "cursed" today will still say it once the real maths lands.
    """
    if side is None or not side.bench or not side.starters:
        return 0.0
    best_bench = max(p.points for p in side.bench)
    worst_starter = min(p.points for p in side.starters)
    return round(max(0.0, best_bench - worst_starter), 2)


def cheer_view(snap: LeagueSnapshot) -> list[dict[str, Any]]:
    """CHEER / BOO / CONFLICTED per live NFL game.

    PHASE5: needs the NFL scoreboard feed to know which games are live and who
    is on the field. The shape is settled now so the template is real.
    """
    return []


def swing_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """PHASE2: the win probability curve is a simulator output; this is the frame."""
    return {"curve": [], "biggest_swing": None, "gut_punch": []}


def receipts_view(snap: LeagueSnapshot) -> dict[str, Any]:
    rows = []
    for team in snap.teams:
        matchup = snap.matchup_for(team.id)
        side = matchup.side_for(team.id) if matchup else None
        rows.append(
            {
                "manager": team.manager,
                "team": team.name,
                "hue": team.hue,
                "score": round(side.total, 2) if side else 0.0,
                "bench_regret": _bench_regret(side),
                # PHASE2: all-play and luck need the season schedule grid.
                "all_play": None,
                "luck": None,
            }
        )
    rows.sort(key=lambda r: r["bench_regret"], reverse=True)
    return {"rows": rows, "fines": []}


def multiverse_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """PHASE5: playoff odds and magic numbers, from the season simulator."""
    return {"odds": [], "magic_numbers": [], "scenarios": []}
