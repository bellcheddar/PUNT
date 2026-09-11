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

import json
from typing import Any

from engine.scoring import all_play, luck_index, optimal_lineup, standings
from engine.simulate import playoff_odds, probabilities_for
from espn.models import LeagueSnapshot, Matchup, Side, Team


def _team_card(team: Team | None, side: Side | None) -> dict[str, Any]:
    if team is None:
        return {  # cold: no matchup in the fixture names a team mTeam never sent
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
        card.update(_pace(snap, side))
        cards.append(card)

    _rate(cards)
    # Form, not the raw score. Ordering ten cards by points at three in the
    # afternoon mostly ranks them by how many of their players happened to kick
    # off at one o'clock, which is not a thing anybody did. `total` stays as the
    # tie-break, because two identical ratings should still fall out in a stable
    # and explicable order rather than by dictionary insertion.
    cards.sort(key=lambda c: (c["form"], c["total"]), reverse=True)
    for rank, card in enumerate(cards):
        card["rank"] = rank + 1
        card["tier"] = _tier(
            rank, len(cards), card["bench_regret"],
            winning=card["winning"], week_low=card["week_low"],
            season_high=card["season_high"],
        )
    return cards


#: What the form rating is made of. Four parts, and they answer four different
#: questions that the single number on the front of the card used to conflate:
#:
#:   pace    are these players beating what was expected of them SO FAR -- the
#:           one component that does not reward a team simply for having kicked
#:           off earlier, because the expectation is prorated by how much of
#:           each real game has actually been played.
#:   lineup  did the manager start the right people. Points sitting on a bench
#:           were available and were not taken.
#:   win     is the head-to-head being won. A 60-point week is a bad week if the
#:           opponent has 90, and this is the only part that knows that.
#:   scale   the raw total, kept because a big score IS an achievement and a
#:           rating that ignored it would call a 40-point team with a perfect
#:           lineup the best in the league.
#:
#: They sum to 1.0 and a test enforces it: the rating is presented out of 100
#: and a set of weights summing to 0.9 would quietly make 100 unreachable.
FORM_WEIGHTS = {"pace": 0.35, "win": 0.25, "lineup": 0.20, "scale": 0.20}

#: Pace is capped here before normalising. Doubling the prorated projection is
#: already a remarkable afternoon; without a cap, one player returning a kickoff
#: in the first quarter -- when the denominator is tiny -- gives a pace of nine
#: and pins that team at the top of the album until teatime.
PACE_CAP = 2.0


def _rate(cards: list[dict[str, Any]]) -> None:
    """Give every card a 0-100 form rating, in place.

    One pass over all ten, like rarity, because two of the four parts are
    relative: `scale` is measured against the best score in the league this week
    and there is no such thing as a team's own scale in isolation.
    """
    best = max((c["total"] for c in cards), default=0.0)
    for card in cards:
        pace = card["pace"]
        optimal = card["optimal"]
        parts = {
            # No prorated expectation yet means no game has kicked off. Neutral
            # rather than zero: before the first snap every team is equally
            # unproven, and zeroing it would rank the album by the other three
            # parts while pretending it had measured something.
            "pace": 0.5 if pace is None else min(pace, PACE_CAP) / PACE_CAP,
            "win": 0.5 if card["win_prob"] is None else card["win_prob"],
            "lineup": (card["total"] / optimal) if optimal > 0 else 1.0,
            "scale": (card["total"] / best) if best > 0 else 0.0,
        }
        card["form_parts"] = {k: round(v, 3) for k, v in parts.items()}
        card["form"] = round(
            100 * sum(FORM_WEIGHTS[k] * v for k, v in parts.items()), 1
        )


def _pace(snap: LeagueSnapshot, side: Side | None) -> dict[str, Any]:
    """Points scored against points that should have been scored BY NOW.

    The naive version divides by the whole projection, which measures nothing on
    a Sunday afternoon: a team whose starters all kick off at one o'clock and a
    team whose starters all kick off at four have wildly different scores at
    three and identical prospects, and the album spent the afternoon ranking the
    first lot above the second for it. So each starter's projection is prorated
    by how much of his actual NFL game has been played, and the sum of those is
    what the team is measured against.

    A player with no game on the scoreboard -- a bye, a scratch, a pro team the
    feed did not send -- contributes to neither side of the ratio. Counting his
    projection as due would punish his manager for a fixture list, and counting
    it as delivered would reward him for nothing.
    """
    if side is None:
        return {"pace": None, "expected": 0.0, "beating": 0}

    expected = 0.0
    beating = 0
    for player in side.starters:
        game = snap.games.get(player.pro_team_id)
        if game is None:
            continue
        share = _elapsed(game)
        if share <= 0:
            continue
        due = player.projected * share
        expected += due
        if player.points > due:
            beating += 1

    return {
        "pace": round(side.total / expected, 3) if expected >= 1.0 else None,
        "expected": round(expected, 2),
        "beating": beating,
    }


def _elapsed(game) -> float:
    """How much of one NFL game has been played, from 0 to 1.

    Kept as a name here because half this module reads better for it. The
    arithmetic moved to `GameState.elapsed`: the ticker needs the same figure
    and lives in `engine/`, which cannot import a view.
    """
    return game.elapsed


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
    # Every game in the week, played, playing or still to come. It used to skip
    # anything finished or not yet kicked off, which meant the whole tab read
    # "No games in progress" on a Thursday evening and again on Monday morning,
    # and showed nothing at all on the two days a week people most want to look
    # at it. A game that has finished still answers the question -- it just
    # answers it in the past tense -- and one that has not started answers it in
    # the future. Ordered so the live ones come first.
    for pro_team_id, game in sorted(snap.games.items(), key=lambda kv: kv[1].abbrev):
        # One row per fixture, not per team: the same game appears twice in the
        # scoreboard, once from each side.
        fixture = "-".join(sorted([game.abbrev, game.opponent or ""]))
        if fixture in seen:
            continue
        seen.add(fixture)

        # Both halves of the fixture carry players, so collect from each.
        involved = list(stakes.get(pro_team_id, []))
        pro_ids = {pro_team_id}
        for other_id, other in snap.games.items():
            if other.abbrev == game.opponent:
                involved += stakes.get(other_id, [])
                pro_ids.add(other_id)

        mine = [p for tid, _, p in involved if tid == team_id]
        theirs = [p for tid, _, p in involved if opponent_id is not None and tid == opponent_id]
        others = sorted({m for tid, m, _ in involved if tid not in (team_id, opponent_id)})

        stake = _fixture_stake(snap, pro_ids)

        if team_id is None:
            # Not a verdict at all, because with no team chosen there is nothing
            # to be for or against. Every row used to read STAKE, which told a
            # room of ten people exactly nothing about which television to look
            # at. What they actually want is the size of the bet: how many
            # fantasy points are still on the field here, and whether this game
            # has both halves of somebody's head-to-head in it.
            verdict = ""
            who = (f"{stake['starters']} starter{'' if stake['starters'] == 1 else 's'}, "
                   f"{stake['teams']} team{'' if stake['teams'] == 1 else 's'}")
            if stake["swings"]:
                reason = (f"{who} \u00b7 decides "
                          f"{', '.join(s['label'] for s in stake['swings'][:2])}")
            elif stake["starters"]:
                reason = f"{who} \u00b7 {_stake_phrase(stake, game.finished)}"
            else:
                reason = "Nobody in the league has a starter in this one."
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
            # Which NFL team, so the row can open that game's detail.
            "pro_team_id": pro_team_id,
            "state": game.state,
            "finished": game.finished,
            "live": game.live,
            "when": ("final" if game.finished
                     else f"Q{game.period} {game.clock}" if game.live
                     else "not started"),
            "quarter": game.period,
            "clock": game.clock,
            "red_zone": game.red_zone,
            "verdict": verdict,
            "reason": reason,
            "mine": [p.name for p in mine],
            "theirs": [p.name for p in theirs],
            "others": others,
            # What the pill shows when nobody has chosen a team: points rather
            # than a word. A finished game has none left, so it shows what it
            # delivered instead -- the same quantity in the past tense, which is
            # what "final" means for a stake.
            "at_stake": stake["scored"] if game.finished else stake["live"],
            "stake_kind": "scored" if game.finished else "live",
            "heat": _stake_heat(stake, game.finished),
            "starters": stake["starters"],
            "teams": stake["teams"],
            "swings": [s["label"] for s in stake["swings"]],
        })

    # Live first, then still to come, then done: the question "do I want this to
    # happen" is only live for a game that has not finished, and a finished one
    # is a result rather than a stake.
    order = {"CONFLICTED": 0, "CHEER": 1, "BOO": 2, "NOTHING": 4}
    state_order = {True: 0, False: 1}
    rows.sort(key=lambda r: (r["finished"], state_order[bool(r["live"])],
                             order.get(r["verdict"], 3), -r["at_stake"], r["fixture"]))
    return rows


def _names(players: list) -> str:
    """A readable list of player names, truncated before it becomes a paragraph."""
    names = [p.name for p in players]
    if len(names) <= 2:
        return " and ".join(names)
    return f"{', '.join(names[:2])} and {len(names) - 2} more"


def _fixture_stake(snap: LeagueSnapshot, pro_ids: set[int]) -> dict[str, Any]:
    """How much one NFL game matters to the fantasy league.

    "Matters" has two parts and they are not the same. The first is size: the
    fantasy points still to come out of this fixture, which is what makes a game
    worth looking up at. The second is consequence: whether both halves of a
    head-to-head have starters in it, which is what makes a game worth *arguing*
    about. A fixture with eight starters all belonging to one manager is a big
    stake and decides nothing; a fixture with one starter each side of the
    league's closest matchup is a small stake that decides the week. Both are
    returned, and nothing here collapses them into a single number, because the
    collapse is exactly what made every row read the same.
    """
    per_team: dict[int, dict[str, Any]] = {}
    swings: list[dict[str, Any]] = []

    for matchup in snap.live_matchups or snap.matchups:
        pair = []
        for side in (matchup.home, matchup.away):
            team = snap.team(side.team_id)
            inside = [p for p in side.starters if p.pro_team_id in pro_ids]
            entry = {
                "team_id": side.team_id,
                "team": team.name if team else f"team {side.team_id}",
                "manager": team.manager if team else "",
                "hue": team.hue if team else 0,
                "players": [p.name for p in inside],
                "count": len(inside),
                "scored": round(sum(p.points for p in inside), 2),
                "live": round(sum(p.remaining for p in inside), 2),
            }
            pair.append(entry)
            if inside:
                per_team[side.team_id] = entry
        if pair[0]["count"] and pair[1]["count"]:
            # Both managers are invested, so this game moves the margin between
            # them rather than just both their totals. `net` is signed towards
            # the first side: positive means the fixture favours them.
            swings.append({
                "label": f"{pair[0]['team']} v {pair[1]['team']}",
                "sides": pair,
                "net": round(pair[0]["live"] - pair[1]["live"], 2),
            })

    swings.sort(key=lambda s: -(s["sides"][0]["live"] + s["sides"][1]["live"]))
    exposure = sorted(per_team.values(), key=lambda e: (-e["live"], -e["scored"]))
    return {
        "starters": sum(e["count"] for e in exposure),
        "teams": len(exposure),
        "scored": round(sum(e["scored"] for e in exposure), 2),
        "live": round(sum(e["live"] for e in exposure), 2),
        "swings": swings,
        "exposure": exposure,
    }


def _stake_phrase(stake: dict[str, Any], finished: bool) -> str:
    """The half-sentence that goes after the starter count."""
    if finished:
        return f"{stake['scored']:.1f} delivered"
    if stake["live"] <= 0:
        return "nothing left to come"
    return f"{stake['live']:.1f} still to come"


def _stake_heat(stake: dict[str, Any], finished: bool) -> str:
    """Which of four bands the pill is painted in.

    Banded on consequence first and size second, in that order, because a room
    deciding which screen to watch cares more that a game is deciding somebody's
    week than that it is worth a lot of points to one manager who is already
    forty ahead.
    """
    if finished or not stake["starters"]:
        return "done" if finished else "none"
    if len(stake["swings"]) >= 2:
        return "hot"
    if stake["swings"] or stake["live"] >= 20:
        return "warm"
    return "cool"


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
                continue  # cold: same: every side in the fixture has a team behind it
            rows.append({
                "id": team.id,
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
                "teams": moment.teams,
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
                # The team id, so a row can open something. Without it the
                # template rendered `data-team=""` and the row was decorated as
                # tappable and did nothing -- which is the exact failure the
                # comment on `.tappable` warns about, shipped the wrong way
                # round. `url_for` with an int converter is what finally raised
                # it; an empty attribute never will.
                "id": team.id,
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
        return ""  # cold: every team in the standings is also in the odds
    if chance.clinched:
        return "IN"
    if chance.eliminated:
        return "OUT"  # cold: week 11 of 14 with six of ten qualifying: nobody is out yet
    if chance.magic_number == 0:
        return "WIN NOTHING"  # cold: magic zero without a clinch is a band a tenth of a percent wide
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
            continue  # cold: two pro teams are unowned and neither reached the red zone
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
            continue  # cold: same missing-team guard as above
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


def ticker_view(live, snap: LeagueSnapshot, limit: int = 30) -> list[dict[str, Any]]:
    """The strip along the top: what has moved, newest first.

    Falls back to a summary of the week when nothing has moved. That is not a
    nicety: the differ needs two polls before it can report anything, so a fresh
    process, a deploy, or anybody opening the page on a Tuesday would otherwise
    get an empty strip where the liveliest thing on the page is supposed to be.
    A settled week has plenty to say, it just does not change.
    """
    changes = [c.to_json() for c in live.ticker.recent(limit)] if live is not None else []
    if changes:
        return changes
    return _week_in_summary(snap)


def _week_in_summary(snap: LeagueSnapshot) -> list[dict[str, Any]]:
    """The week as a handful of ticker lines, for when nothing is moving."""
    cards = album_view(snap)
    if not cards:
        return []  # cold: a snapshot with no teams renders its own empty state first
    out: list[dict[str, Any]] = []

    def line(kind, card, text, value, good):
        out.append({
            "id": f"summary-{kind}-{card['id']}", "kind": kind,
            "team_id": card["id"], "team": card["name"], "hue": card["hue"],
            "text": text, "value": value, "good": good, "magnitude": 0.5,
            "ts": snap.captured_at, "summary": True,
        })

    best = cards[0]
    line("FORM", best, f"{best['name']} lead the week on form", f"{best['form']:.0f}", True)

    top = max(cards, key=lambda c: c["total"])
    line("SCORE", top, f"{top['name']} top the scoring", f"{top['total']:.1f}", True)

    worst = max(cards, key=lambda c: c["bench_regret"])
    if worst["bench_regret"] > 0:
        line("BENCH", worst,
             f"{worst['name']} left the most on the bench", f"{worst['bench_regret']:.1f}", False)

    hottest = max(cards, key=lambda c: c["beating"])
    if hottest["beating"]:
        line("HOT", hottest,
             f"{hottest['name']} have {hottest['beating']} starters beating projection",
             str(hottest["beating"]), True)

    multiverse = multiverse_view(snap)
    if multiverse.get("available") and multiverse["rows"]:
        leader = multiverse["rows"][0]
        out.append({
            "id": f"summary-playoff-{leader['id']}", "kind": "PLAYOFF",
            "team_id": leader["id"], "team": leader["team"], "hue": leader["hue"],
            "text": f"{leader['team']} lead the playoff race",
            "value": f"{leader['odds'] * 100:.0f}%", "good": True,
            "magnitude": 0.5, "ts": snap.captured_at, "summary": True,
        })
    return out


def moments_view(live, limit: int = 25, snap: LeagueSnapshot | None = None) -> list[dict[str, Any]]:
    """The commentary feed.

    Every line carries the team it is about, whether or not the phrase happens
    to name one. A hundred and three of the four hundred and ten lines name no
    team -- fifty of those are filler and never should, and the rest are about a
    specific team and simply do not say so: "Nothing at all from the WR, and
    16.9 sitting on the bench" is a complete sentence about somebody, and
    reading it in a feed you cannot tell whose bench. Attributing the item
    rather than auditing the bank also means the next phrase written cannot
    reintroduce the problem.
    """
    if live is None:
        return []
    hues = {t.id: t.hue for t in snap.teams} if snap is not None else {}
    out = []
    for moment in live.recent(limit=limit):
        line = live.line_for(moment)
        out.append({
            # The Moment's own id, so a feed line can open the play behind it.
            "id": moment.id,
            "kind": moment.kind,
            "magnitude": round(moment.magnitude, 2),
            "teams": moment.teams,
            "team_id": moment.team_ids[0] if moment.team_ids else None,
            "hue": hues.get(moment.team_ids[0]) if moment.team_ids else None,
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


def stored_moments(store, snap: LeagueSnapshot, limit: int = 200) -> list[dict[str, Any]]:
    """The commentary feed for a week that has already finished.

    Shaped exactly like `moments_view`, because the same template renders both
    and a fragment that quietly lacked a field would show an empty line rather
    than an error. The fields that only exist while a play is live -- the
    magnitude the engine measured it at, the audio sting it chose -- come back
    empty, which is honest: they were properties of the moment it happened.

    Newest first, matching the live feed, so the two do not read in opposite
    directions depending on which week is selected.
    """
    hues = {t.id: t.hue for t in snap.teams}
    week = store.week(snap.season, snap.scoring_period)
    out = []
    for row in reversed(week.get("moments", [])[-limit:]):
        team_id = row.get("team_id")
        out.append({
            "id": row["id"],
            "kind": row["kind"],
            "magnitude": 0.0,
            "teams": [row["team"]] if row["team"] else [],
            "team_id": team_id,
            "hue": hues.get(team_id),
            "player": row["player"],
            "delta": row["delta"],
            "context": json.loads(row["payload"] or "{}"),
            "ts": row["at"],
            "text": row["said"],
            "audio": "",
            "tone": [],
        })
    return out


# --------------------------------------------------------------------------
# the detail sheets
#
# Each panel on the page opens its own kind of detail, because "more about this
# row" means something different in each: a bench-regret row is about a lineup,
# a cheer row is about an NFL game, a commentary line is about one play. They
# are separate view models rather than one wide one so that a sheet cannot
# quietly start showing a number the panel it came from does not have.
# --------------------------------------------------------------------------

def regret_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """One manager's whole lineup decision, not just the worst swap.

    The panel shows the single most expensive mistake. This shows the working:
    every seat, who is in it, who was available for it, and what each swap was
    worth. The optimal lineup is a maximum-weight matching, so the seats do not
    come out in roster order and "swap these two" is often not what the solver
    actually did -- which is exactly why it is worth showing.
    """
    team = snap.team(team_id)
    matchup = snap.matchup_for(team_id)
    side = matchup.side_for(team_id) if matchup else None
    if team is None or side is None:
        return {}

    slots = snap.settings.starting_slots
    lineup = optimal_lineup(side.players, slots)
    seated = {seat.player.id for seat in lineup.seats if seat.player} if lineup else set()

    bench = sorted((p for p in side.bench), key=lambda p: -p.points)
    starters = sorted(side.starters, key=lambda p: -p.points)

    # What each bench player would have been worth in the seat its owner
    # actually filled worst. Not the solver's answer, the readable version of it.
    might_have = []
    for benched in bench[:8]:
        beaten = [s for s in starters if s.points < benched.points
                  and _slot_allows(snap, s, benched)]
        if not beaten:
            continue
        worst = min(beaten, key=lambda s: s.points)
        might_have.append({
            "benched": benched.name,
            "benched_points": round(benched.points, 2),
            "started": worst.name,
            "started_points": round(worst.points, 2),
            "slot": worst.slot,
            "gain": round(benched.points - worst.points, 2),
        })
    might_have.sort(key=lambda r: -r["gain"])

    return {
        "team": team.name, "manager": team.manager, "hue": team.hue, "id": team.id,
        "logo": team.logo, "monogram": team.monogram, "record": team.record,
        "actual": round(side.total, 2),
        "optimal": round(lineup.total, 2) if lineup else round(side.total, 2),
        "regret": round(lineup.regret, 2) if lineup else 0.0,
        "exact": lineup.exact if lineup else True,
        "seats": [
            {"slot": seat.slot,
             "name": seat.player.name if seat.player else "empty",
             "points": round(seat.player.points, 2) if seat.player else 0.0,
             "started": bool(seat.player and seat.player.is_starter)}
            for seat in (lineup.seats if lineup else [])
        ],
        "left_out": [
            {"name": p.name, "points": round(p.points, 2), "slot": p.slot,
             "position": p.position}
            for p in bench if p.id not in seated and p.points > 0
        ][:10],
        "might_have": might_have[:6],
    }


def _slot_allows(snap: LeagueSnapshot, started, benched) -> bool:
    """Whether the benched player could legally have taken that seat."""
    from engine.scoring import eligible_slots  # noqa: PLC0415 - avoids a cycle

    return started.slot_id in eligible_slots(benched)


def trouble_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """Why this team is losing, and what is left that could change it."""
    team = snap.team(team_id)
    matchup = snap.matchup_for(team_id)
    if team is None or matchup is None:
        return {}
    side = matchup.side_for(team_id)
    other = matchup.opponent_of(team_id)
    opponent = snap.team(other.team_id) if other else None
    probability = probabilities_for(snap).get(matchup.id)

    def remaining(a_side) -> list[dict[str, Any]]:
        return sorted(
            ({"name": p.name, "slot": p.slot, "pro_team": p.pro_team,
              "points": round(p.points, 2), "projected": round(p.projected, 2),
              "remaining": round(p.remaining, 2)}
             for p in a_side.starters if p.remaining > 0),
            key=lambda p: -p["remaining"],
        )

    return {
        "team": team.name, "manager": team.manager, "hue": team.hue, "id": team.id,
        "logo": team.logo, "monogram": team.monogram,
        "opponent": opponent.name if opponent else "?",
        "opponent_id": other.team_id if other else None,
        "opponent_hue": opponent.hue if opponent else 0,
        "score": round(side.total, 2), "opponent_score": round(other.total, 2) if other else 0.0,
        "deficit": round(side.total - (other.total if other else 0), 2),
        "win_prob": probability.for_team(team_id) if probability else None,
        "projected": round(probability.mean_for(team_id), 2) if probability else None,
        "opponent_projected": (round(probability.mean_for(other.team_id), 2)
                               if probability and other else None),
        "in_play": side.in_play, "opponent_in_play": other.in_play if other else 0,
        "mine": remaining(side)[:8],
        "theirs": remaining(other)[:8] if other else [],
    }


def moment_detail(live, moment_id: str, snap: LeagueSnapshot | None = None) -> dict[str, Any]:
    """One play, in full.

    The feed is one line because a feed has to be. Everything the engine knew
    when it fired is here instead: what it measured, how loud it decided that
    was, what it moved, and the line it chose to say about it.
    """
    if live is None:
        return {}
    moment = next((m for m in live.recent(limit=200) if m.id == moment_id), None)
    if moment is None:
        return {}
    line = live.line_for(moment)
    team_id = moment.team_ids[0] if moment.team_ids else None
    team = snap.team(team_id) if snap is not None and team_id else None

    # The context dict is whatever that detector recorded, and it differs by
    # kind. Rendered as-is rather than mapped onto fixed fields, because a
    # mapping would have to be updated every time a detector learns something
    # and would quietly drop whatever it had not heard of.
    facts = []
    for key, value in sorted((moment.context or {}).items()):
        if isinstance(value, float):
            value = f"{value:.2f}".rstrip("0").rstrip(".")
        facts.append({"label": key.replace("_", " "), "value": value})

    return {
        "kind": moment.kind.replace("_", " "),
        "team": moment.teams[0] if moment.teams else None,
        "team_id": team_id,
        "hue": team.hue if team else None,
        "logo": team.logo if team else "",
        "monogram": team.monogram if team else "",
        "player": moment.player,
        "delta": round(moment.delta_points, 2),
        "magnitude": round(moment.magnitude, 2),
        "win_prob_delta": (round(moment.win_prob_delta * 100, 1)
                           if moment.win_prob_delta else None),
        "at": moment.ts.strftime("%H:%M:%S"),
        "said": line.text if line else "",
        "voice": line.voice if line else "",
        "audio": line.audio if line else "",
        "tone": ", ".join(line.tone) if line and line.tone else "",
        "facts": facts,
    }


def game_detail(snap: LeagueSnapshot, pro_team_id: int) -> dict[str, Any]:
    """One NFL game, and every player in the league who is in it.

    The Cheer panel says whether you want this to happen. This says exactly who
    decides that: both rosters' players in this fixture, whose they are, and
    what each has scored -- which is the thing people actually shout about.
    """
    game = snap.games.get(pro_team_id)
    if game is None:
        return {}
    sides = {game.abbrev, game.opponent or ""}
    pro_ids = {tid for tid, g in snap.games.items() if g.abbrev in sides}

    owned = []
    for matchup in snap.live_matchups or snap.matchups:
        for side in (matchup.home, matchup.away):
            team = snap.team(side.team_id)
            for player in side.players:
                if player.pro_team_id not in pro_ids:
                    continue
                owned.append({
                    "name": player.name, "slot": player.slot, "position": player.position,
                    "pro_team": player.pro_team, "points": round(player.points, 2),
                    "projected": round(player.projected, 2),
                    "starter": player.is_starter, "done": player.game_over,
                    "team": team.name if team else "?", "team_id": side.team_id,
                    "hue": team.hue if team else 0,
                })
    owned.sort(key=lambda p: (not p["starter"], -p["points"]))

    stake = _fixture_stake(snap, pro_ids)
    return {
        "fixture": f"{game.opponent or '?'} at {game.abbrev}",
        "state": game.state, "finished": game.finished, "live": game.live,
        "when": ("final" if game.finished
                 else f"Q{game.period} {game.clock}" if game.live else "not started"),
        "red_zone": game.red_zone, "possession": game.possession,
        "score": game.score,
        "players": owned,
        "starters": sum(1 for p in owned if p["starter"]),
        "managers": len({p["team_id"] for p in owned}),
        # The same analysis the Cheer panel's figure comes from, so the sheet
        # explains the number that was tapped rather than a second opinion of it.
        "at_stake": stake["scored"] if game.finished else stake["live"],
        "stake_kind": "scored" if game.finished else "live",
        "scored": stake["scored"], "live_points": stake["live"],
        "swings": stake["swings"], "exposure": stake["exposure"],
        "heat": _stake_heat(stake, game.finished),
    }


def odds_detail(snap: LeagueSnapshot, team_id: int, draws: int = 2500) -> dict[str, Any]:
    """Where one team finishes, across every simulated season.

    The panel is a single percentage. A percentage is the answer to "will I make
    it" and no help at all with "what do I need", so this is the seed
    distribution, the rest of the fixture list, and the record the simulator
    thinks it is heading for.
    """
    if not snap.season_schedule:
        return {}
    team = snap.team(team_id)
    if team is None:
        return {}
    odds = playoff_odds(snap, draws=draws).get(team_id)
    table = {r.team_id: r for r in standings(snap)}
    record = table.get(team_id)
    places = snap.settings.playoff_team_count or 6
    current = snap.settings.current_matchup_period

    fixtures = []
    for week, games in sorted(snap.season_weeks().items()):
        if week < current:
            continue
        for matchup in games:
            if team_id not in matchup.team_ids:
                continue
            other = matchup.opponent_of(team_id)
            rival = snap.team(other.team_id) if other else None
            rival_record = table.get(other.team_id) if other else None
            fixtures.append({
                "week": week,
                "opponent": rival.name if rival else "?",
                "opponent_id": other.team_id if other else None,
                "hue": rival.hue if rival else 0,
                "record": rival_record.record if rival_record else "",
                "this_week": week == current,
            })

    return {
        "team": team.name, "manager": team.manager, "hue": team.hue, "id": team.id,
        "logo": team.logo, "monogram": team.monogram,
        "record": record.record if record else "",
        "points_for": round(record.points_for, 1) if record else 0.0,
        "all_play": record.all_play.record if record else "",
        "luck": round(record.luck, 1) if record else 0.0,
        "odds": round(odds.odds * 100, 1) if odds else None,
        "mean_wins": round(odds.mean_wins, 1) if odds else None,
        "magic": odds.magic_number if odds else None,
        "clinched": odds.clinched if odds else False,
        "eliminated": odds.eliminated if odds else False,
        "remaining": odds.remaining if odds else 0,
        "places": places,
        "seeds": [{"seed": seed, "pct": round(share * 100, 1)}
                  for seed, share in sorted((odds.seed_odds if odds else {}).items())
                  if share > 0.004],
        "fixtures": fixtures,
        "draws": draws,
    }
