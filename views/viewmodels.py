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

from engine.scoring import all_play, luck_index, optimal_lineup, standings
from engine.simulate import playoff_odds, probabilities_for
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


def album_view(snap: LeagueSnapshot, live=None) -> list[dict[str, Any]]:
    """The ten cards, ordered by this week's score. Rarity is earned, so it can
    only be assigned once every team's score is known -- which is why this is one
    pass over all ten rather than a property on a card."""
    slots = snap.settings.starting_slots
    probabilities = probabilities_for(snap)
    # The week's low-water mark, and each team's best settled week. Both are
    # needed for Legendary and neither can be read off the current snapshot.
    week_low = dict(live.week_low) if live is not None else {}
    best_week = _best_previous_week(snap)
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
        probability = (
            probabilities[matchup.id].for_team(team.id)
            if matchup and matchup.id in probabilities else None
        )
        card["win_prob"] = probability
        card["week_low"] = week_low.get(team.id)
        card["season_high"] = bool(
            best_week.get(team.id) and card["total"] > best_week[team.id]
        )
        card["winning"] = bool(probability is not None and probability > 0.5)
        cards.append(card)

    cards.sort(key=lambda c: c["total"], reverse=True)
    for rank, card in enumerate(cards):
        card["rank"] = rank + 1
        card["tier"] = _tier(
            rank, len(cards), card["bench_regret"],
            winning=card["winning"], week_low=card["week_low"],
            season_high=card["season_high"],
        )
    return cards


def _best_previous_week(snap: LeagueSnapshot) -> dict[int, float]:
    """Each team's best score in a week that is *not* this one.

    The current week has to be excluded explicitly. The moment the last game
    ends, ESPN marks the week complete and it joins `settled_weeks`, so a team's
    own live score enters its own season best and "beat your season high" becomes
    "beat your own score", which is false for everybody forever. The Legendary
    card was therefore visible all afternoon and gone at the final whistle --
    exactly backwards for a trophy, and invisible to a unit test that passes the
    flag in by hand.
    """
    this_week = snap.settings.current_matchup_period
    best: dict[int, float] = {}
    for week, games in snap.settled_weeks.items():
        if week == this_week:
            continue
        for matchup in games:
            for side in (matchup.home, matchup.away):
                if side.total > best.get(side.team_id, 0.0):
                    best[side.team_id] = round(side.total, 2)
    return best


#: A win from below this probability mints a Legendary card.
#:
#: "From under 10%" is a thing that was true at some point in the afternoon, not
#: a thing that is true now. Reading the current number instead -- which this did
#: for a while -- marks whoever is *losing* as legendary, and marks nobody at all
#: once the games finish and every probability is 1.0 or 0.0. The tier was
#: unreachable, which is how `tools/deadcode.py` found it.
LEGENDARY_WIN_PROB = 0.10


def _tier(rank: int, count: int, bench_regret: float, *,
          winning: bool, week_low: float | None, season_high: bool) -> str:
    """Rarity, per the spec's table: season-high score, or a win from under 10%.

    Cursed is checked first and deliberately outranks Legendary: a manager who
    left forty points on the bench does not get a holographic card for it,
    whatever else happened.
    """
    if rank == count - 1 or bench_regret > 40:
        return "cursed"
    if season_high:
        return "legendary"
    if winning and week_low is not None and week_low < LEGENDARY_WIN_PROB:
        return "legendary"
    if rank == 0:
        return "epic"
    if rank < 3:
        return "rare"
    return "common"


def cheer_view(snap: LeagueSnapshot, team_id: int | None = None) -> list[dict[str, Any]]:
    """CHEER / BOO / CONFLICTED per live NFL game, from one manager's point of view.

    The question this tab answers is the one people actually ask out loud in a
    bar: "wait, do I want this to happen?" It is genuinely hard to hold in your
    head, because a single NFL game can carry one of your starters and two of
    your opponent's, and the answer flips depending on which of them touches
    the ball.

    With no team chosen it falls back to a league-wide view: who has a stake in
    each game at all. That is still useful on the bar screen, where the question
    is "does anybody in this room care about this game".
    """
    opponent_id = None
    if team_id is not None:
        matchup = snap.matchup_for(team_id)
        other = matchup.opponent_of(team_id) if matchup else None
        opponent_id = other.team_id if other else None

    # pro_team_id -> [(team_id, manager, player)]
    stakes: dict[int, list[tuple[int, str, Any]]] = {}
    for matchup in snap.live_matchups or snap.matchups:
        for side in (matchup.home, matchup.away):
            team = snap.team(side.team_id)
            for player in side.starters:
                stakes.setdefault(player.pro_team_id, []).append(
                    (side.team_id, team.manager if team else "?", player)
                )

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pro_team_id, game in sorted(snap.games.items(), key=lambda kv: kv[1].abbrev):
        if game.finished or game.state == "pre":
            continue
        # One row per fixture, not per team: the same game appears twice in the
        # scoreboard, once from each side.
        fixture = "-".join(sorted([game.abbrev, game.opponent or ""]))
        if fixture in seen:
            continue
        seen.add(fixture)

        # Both halves of the fixture carry players, so collect from each.
        involved = list(stakes.get(pro_team_id, []))
        for other_id, other in snap.games.items():
            if other.abbrev == game.opponent:
                involved += stakes.get(other_id, [])

        mine = [p for tid, _, p in involved if tid == team_id]
        theirs = [p for tid, _, p in involved if opponent_id is not None and tid == opponent_id]
        others = sorted({m for tid, m, _ in involved if tid not in (team_id, opponent_id)})

        if team_id is None:
            verdict = "STAKE" if involved else ""
            reason = (f"{len(involved)} starter{'s' if len(involved) != 1 else ''} "
                      f"across {len(set(tid for tid, _, _ in involved))} managers")
        elif mine and theirs:
            verdict = "CONFLICTED"
            reason = (f"You have {_names(mine)}. Your opponent has {_names(theirs)}.")
        elif mine:
            verdict = "CHEER"
            reason = f"You have {_names(mine)} and your opponent has nobody."
        elif theirs:
            verdict = "BOO"
            reason = f"Your opponent has {_names(theirs)}. You have nobody."
        else:
            verdict = "NOTHING"
            reason = ("Nothing of yours and nothing of your opponent's. "
                      + (f"{', '.join(others[:3])} care." if others else "Nobody in the league cares."))

        rows.append({
            "fixture": f"{game.opponent or '?'} at {game.abbrev}",
            "quarter": game.period,
            "clock": game.clock,
            "red_zone": game.red_zone,
            "verdict": verdict,
            "reason": reason,
            "mine": [p.name for p in mine],
            "theirs": [p.name for p in theirs],
            "others": others,
        })

    order = {"CONFLICTED": 0, "CHEER": 1, "BOO": 2, "STAKE": 3, "NOTHING": 4}
    rows.sort(key=lambda r: (order.get(r["verdict"], 9), r["fixture"]))
    return rows


def _names(players: list) -> str:
    """A readable list of player names, truncated before it becomes a paragraph."""
    names = [p.name for p in players]
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:2])} and {len(names) - 2} more"


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
        # A lead change and the touchdown that caused it carry the same delta,
        # because they are the same event described twice. Keep the one that
        # names a player: "Ander Wetherby, +33%" is a thing that happened,
        # "Noor took the lead, +33%" is its consequence.
        seen: set[tuple[int, float]] = set()
        for moment in live.recent(limit=60):
            if abs(moment.win_prob_delta) < 0.05 or not moment.team_ids:
                continue
            key = (moment.team_ids[0], round(moment.win_prob_delta, 4))
            if key in seen:
                continue
            seen.add(key)
            swings.append({
                "kind": moment.kind,
                "managers": moment.managers,
                "player": moment.player,
                "delta": moment.win_prob_delta,
                "ts": moment.ts.isoformat(timespec="seconds"),
            })
        # Player moments first within an equal delta, then by size.
        swings.sort(key=lambda s: (-abs(s["delta"]), s["player"] is None))

    return {"rows": rows, "biggest_swing": swings[0] if swings else None, "swings": swings[:8]}


def receipts_view(snap: LeagueSnapshot) -> dict[str, Any]:
    slots = snap.settings.starting_slots
    scores = {}
    for matchup in snap.live_matchups or snap.matchups:
        for side in (matchup.home, matchup.away):
            scores[side.team_id] = round(side.total, 2)
    records = all_play(scores)
    # Season figures where the grid is available, this week's where it is not.
    season = {r.team_id: r for r in standings(snap)} if snap.season_schedule else {}

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
                "luck": (season[team.id].luck if team.id in season
                         else (luck_index(team.wins, record.win_pct, weeks) if record else None)),
                "season_all_play": season[team.id].all_play.record if team.id in season else "",
            }
        )
    rows.sort(key=lambda r: r["bench_regret"], reverse=True)
    return {"rows": rows, "fines": []}


def multiverse_view(snap: LeagueSnapshot, draws: int = 2500) -> dict[str, Any]:
    """Playoff odds, the table, and what each manager still needs.

    Everything here needs the season grid from `mSchedule`. When that feed is
    missing -- a recording of a single week, or an outage -- the tab says so
    rather than showing a table of zeros that looks like a league where nobody
    has played yet.
    """
    if not snap.season_schedule:
        return {"available": False, "rows": [], "weeks_left": 0, "playoff_places": 0}

    table = standings(snap)
    odds = playoff_odds(snap, draws=draws)
    teams = snap.teams_by_id
    places = snap.settings.playoff_team_count or 6

    rows: list[dict[str, Any]] = []
    for position, record in enumerate(table, start=1):
        team = teams.get(record.team_id)
        chance = odds.get(record.team_id)
        rows.append({
            "position": position,
            "id": record.team_id,
            "manager": team.manager if team else "?",
            "team": team.name if team else "",
            "hue": team.hue if team else 0,
            "record": record.record,
            "points_for": round(record.points_for, 1),
            "all_play": record.all_play.record,
            "luck": record.luck,
            "odds": chance.odds if chance else None,
            "mean_wins": round(chance.mean_wins, 1) if chance else None,
            "magic": chance.magic_number if chance else None,
            "clinched": bool(chance and chance.clinched),
            "eliminated": bool(chance and chance.eliminated),
            "in_places": position <= places,
            "verdict": _playoff_verdict(chance),
        })

    weeks_left = max((c.remaining for c in odds.values()), default=0)
    return {
        "available": True,
        "rows": rows,
        "weeks_left": weeks_left,
        "playoff_places": places,
        "draws": draws,
        "luckiest": max(rows, key=lambda r: r["luck"]) if rows else None,
        "unluckiest": min(rows, key=lambda r: r["luck"]) if rows else None,
    }


def _playoff_verdict(chance) -> str:
    """One phrase, because a column of percentages is a spreadsheet and this app
    exists because ESPN already is one."""
    if chance is None:
        return ""
    if chance.clinched:
        return "IN"
    if chance.eliminated:
        return "OUT"
    if chance.magic_number == 0:
        return "WIN NOTHING"
    if chance.magic_number is not None:
        return f"WIN {chance.magic_number}"
    return "NEEDS HELP"


def watch_now(snap: LeagueSnapshot, limit: int = 5) -> list[dict[str, Any]]:
    """What is worth looking up for, most urgent first.

    Ordered by how soon it resolves rather than by how big it is: a drive inside
    the five settles in ninety seconds and a four-point matchup settles in three
    hours, so the drive goes first even though the matchup matters more.
    """
    rows: list[dict[str, Any]] = []

    for game in snap.red_zone_games:
        if not game.possession:
            continue
        owners = sorted({
            (snap.team(side.team_id).manager if snap.team(side.team_id) else "?")
            for matchup in snap.matchups for side in (matchup.home, matchup.away)
            for player in side.starters if player.pro_team_id == game.pro_team_id
        })
        if not owners:
            continue
        rows.append({
            "kind": "redzone", "flag": "RED ZONE",
            "text": f"{game.abbrev} inside the five \u00b7 {', '.join(owners[:3])}",
            "sort": 0,
        })

    probabilities = probabilities_for(snap)
    for matchup in snap.live_matchups or snap.matchups:
        probability = probabilities[matchup.id]
        if probability.settled:
            continue
        home = snap.team(matchup.home.team_id)
        away = snap.team(matchup.away.team_id)
        if not home or not away:
            continue
        margin = abs(matchup.home.total - matchup.away.total)
        # A coin flip is worth watching; a 90/10 is not, however close the score.
        if 0.25 < probability.home_win < 0.75:
            rows.append({
                "kind": "close", "flag": f"{margin:.1f} IN IT",
                "text": f"{away.manager} v {home.manager} \u00b7 "
                        f"{probability.home_win * 100:.0f}% either way",
                "sort": 1 + abs(0.5 - probability.home_win),
            })

    for matchup in snap.live_matchups or snap.matchups:
        for side, other in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
            team = snap.team(side.team_id)
            if not team or side.in_play == 0 or other.in_play > 0:
                continue
            # One side finished and the other still playing is the tensest state
            # in fantasy football and the score alone does not show it.
            rows.append({
                "kind": "alone", "flag": "LAST MAN",
                "text": f"{team.manager} has {side.in_play} left; "
                        f"{(snap.team(other.team_id).manager if snap.team(other.team_id) else '?')} has none",
                "sort": 2,
            })

    rows.sort(key=lambda r: r["sort"])
    return rows[:limit]


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
