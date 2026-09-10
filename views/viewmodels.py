"""Snapshot -> template data.

Templates get plain dictionaries, never model objects with behaviour on them.
That keeps the arithmetic testable without a request context, and it is what let
the Phase 2 engine replace the Phase 1 placeholders here without touching a
single template.

Anything still marked `PHASE5` is a deliberate placeholder with a correct shape
and a provisional value, so the tab renders something honest today rather than a
blank panel waiting for a module that does not exist yet.
"""

from __future__ import annotations

from typing import Any

from engine.scoring import all_play, luck_index, optimal_lineup
from engine.simulate import probabilities_for
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
        "in_play": side.in_play if side else 0,
        "yet_to_kick_off": side.yet_to_kick_off if side else 0,
        "missing": False,
    }


def _player(player) -> dict[str, Any]:
    return {
        "id": player.id,
        "name": player.name,
        "pro_opponent": player.opponent,
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
    probabilities = probabilities_for(snap)
    out = []
    for matchup in snap.live_matchups or snap.matchups:
        home = _team_card(teams.get(matchup.home.team_id), matchup.home)
        away = _team_card(teams.get(matchup.away.team_id), matchup.away)
        probability = probabilities[matchup.id]
        leader = home if home["total"] >= away["total"] else away
        out.append(
            {
                "id": matchup.id,
                "home": home,
                "away": away,
                "margin": abs(round(home["total"] - away["total"], 2)),
                "leader": leader["manager"],
                "settled": probability.settled or matchup.winner not in ("UNDECIDED", ""),
                "win_prob": probability.home_win,
                "away_win_prob": probability.away_win,
                "projected_home": probability.home_mean,
                "projected_away": probability.away_mean,
            }
        )
    return out


def album_view(snap: LeagueSnapshot) -> list[dict[str, Any]]:
    """The ten cards, ordered by this week's score. Rarity is earned, so it can
    only be assigned once every team's score is known -- which is why this is one
    pass over all ten rather than a property on a card."""
    slots = snap.settings.starting_slots
    probabilities = probabilities_for(snap)
    cards: list[dict[str, Any]] = []

    for team in snap.teams:
        matchup = snap.matchup_for(team.id)
        side = matchup.side_for(team.id) if matchup else None
        card = _team_card(team, side)
        lineup = optimal_lineup(side.players, slots) if side else None
        card["bench_regret"] = lineup.regret if lineup else 0.0
        card["optimal"] = lineup.total if lineup else 0.0
        swap = lineup.worst_swap if lineup else None
        card["worst_swap"] = (
            {"started": swap[0].name, "started_points": round(swap[0].points, 2),
             "benched": swap[1].name, "benched_points": round(swap[1].points, 2),
             "slot": swap[0].slot}
            if swap else None
        )
        card["win_prob"] = (
            probabilities[matchup.id].for_team(team.id) if matchup and matchup.id in probabilities else None
        )
        cards.append(card)

    cards.sort(key=lambda c: c["total"], reverse=True)
    for rank, card in enumerate(cards):
        card["rank"] = rank + 1
        card["tier"] = _tier(rank, len(cards), card["bench_regret"], card["win_prob"])
    return cards


#: A win from below this probability mints a Legendary card. It is checked at
#: the point the card is rendered, which means a manager who was under it earlier
#: and is comfortable now does not qualify -- Phase 5 keeps the running minimum
#: per week, which is the version the spec actually describes.
LEGENDARY_WIN_PROB = 0.10


def _tier(rank: int, count: int, bench_regret: float, win_prob: float | None) -> str:
    """Rarity, per the spec's table.

    Cursed is checked first and deliberately outranks Legendary: a manager who
    left forty points on the bench does not get a holographic card for it,
    whatever else happened.
    """
    if rank == count - 1 or bench_regret > 40:
        return "cursed"
    if win_prob is not None and 0.0 < win_prob < LEGENDARY_WIN_PROB:
        return "legendary"
    if rank == 0:
        return "epic"
    if rank < 3:
        return "rare"
    return "common"


def cheer_view(snap: LeagueSnapshot) -> list[dict[str, Any]]:
    """CHEER / BOO / CONFLICTED per live NFL game.

    PHASE5: needs the NFL scoreboard feed to know which games are live and who
    is on the field. The shape is settled now so the template is real.
    """
    return []


def swing_view(snap: LeagueSnapshot, live=None) -> dict[str, Any]:
    """Win probability now, plus the day's biggest swings from the Moment buffer.

    The curve itself is drawn from Moments rather than kept as a separate time
    series: every Moment already carries the win-probability change it caused, so
    the series and the annotations on it cannot drift apart.
    """
    teams = snap.teams_by_id
    rows = []
    for matchup in snap.live_matchups or snap.matchups:
        for side, opponent in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
            team = teams.get(side.team_id)
            if team is None:
                continue
            rows.append({
                "manager": team.manager,
                "team": team.name,
                "hue": team.hue,
                "score": round(side.total, 2),
                "deficit": round(side.total - opponent.total, 2),
                "in_play": side.in_play,
            })

    probabilities = probabilities_for(snap)
    by_team = {}
    for matchup in snap.live_matchups or snap.matchups:
        probability = probabilities[matchup.id]
        by_team[matchup.home.team_id] = probability.home_win
        by_team[matchup.away.team_id] = probability.away_win
    for row in rows:
        team_id = next((t.id for t in snap.teams if t.manager == row["manager"]), None)
        row["win_prob"] = by_team.get(team_id)

    rows.sort(key=lambda r: (r["win_prob"] is None, r["win_prob"] or 0))

    swings = []
    if live is not None:
        for moment in live.recent(limit=60):
            if abs(moment.win_prob_delta) < 0.05:
                continue
            swings.append({
                "kind": moment.kind,
                "managers": moment.managers,
                "player": moment.player,
                "delta": moment.win_prob_delta,
                "ts": moment.ts.isoformat(timespec="seconds"),
            })
        swings.sort(key=lambda s: -abs(s["delta"]))

    return {"rows": rows, "biggest_swing": swings[0] if swings else None, "swings": swings[:8]}


def receipts_view(snap: LeagueSnapshot) -> dict[str, Any]:
    slots = snap.settings.starting_slots
    scores = {}
    for matchup in snap.live_matchups or snap.matchups:
        for side in (matchup.home, matchup.away):
            scores[side.team_id] = round(side.total, 2)
    records = all_play(scores)

    rows = []
    for team in snap.teams:
        matchup = snap.matchup_for(team.id)
        side = matchup.side_for(team.id) if matchup else None
        lineup = optimal_lineup(side.players, slots) if side else None
        swap = lineup.worst_swap if lineup else None
        record = records.get(team.id)
        weeks = max(1, team.wins + team.losses + team.ties)
        rows.append(
            {
                "manager": team.manager,
                "team": team.name,
                "hue": team.hue,
                "score": round(side.total, 2) if side else 0.0,
                "optimal": lineup.total if lineup else 0.0,
                "bench_regret": lineup.regret if lineup else 0.0,
                "worst_swap": (
                    f"{swap[1].name} ({swap[1].points:.1f}) for {swap[0].name} "
                    f"({swap[0].points:.1f}) at {swap[0].slot}"
                    if swap else ""
                ),
                # All-play is this week only: the season grid needs the mSchedule
                # feed, which lands with the Multiverse tab in Phase 5.
                "all_play": record.record if record else "",
                "all_play_pct": record.win_pct if record else None,
                "luck": luck_index(team.wins, record.win_pct, weeks) if record else None,
            }
        )
    rows.sort(key=lambda r: r["bench_regret"], reverse=True)
    return {"rows": rows, "fines": []}


def multiverse_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """PHASE5: playoff odds and magic numbers, from the season simulator.

    Genuinely blocked rather than merely unbuilt: every figure on this tab needs
    the full season schedule grid from the `mSchedule` feed, and the demo
    recording is one week.
    """
    return {"odds": [], "magic_numbers": [], "scenarios": []}


def moments_view(live, limit: int = 25) -> list[dict[str, Any]]:
    """The commentary feed. Phase 4 replaces the plain descriptions with the
    phrase bank; the shape is the same either way."""
    if live is None:
        return []
    out = []
    for moment in live.recent(limit=limit):
        line = live.line_for(moment)
        out.append({
            "kind": moment.kind,
            "magnitude": round(moment.magnitude, 2),
            "managers": moment.managers,
            "player": moment.player,
            "delta": round(moment.delta_points, 2),
            "context": moment.context,
            "ts": moment.ts.isoformat(timespec="seconds"),
            # The phrase bank's line when there is one. The templated description
            # in the partial is the fallback for a Moment the bank had nothing
            # to say about, which is a deliberate state rather than a gap.
            "text": line.text if line else "",
            "audio": line.audio if line else "",
            "tone": list(line.tone) if line else [],
        })
    return out
