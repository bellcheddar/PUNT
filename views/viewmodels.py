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
import math
import statistics
import threading
from collections import OrderedDict
from dataclasses import replace
from typing import Any

from engine.scoring import all_play, luck_index, optimal_lineup, season_records, standings
from engine.simulate import playoff_odds, probabilities_for, win_probability
from espn.models import LeagueSnapshot, Matchup, Side, Team, defence_team


def _team_card(team: Team | None, side: Side | None) -> dict[str, Any]:
    if team is None:
        return {  # cold: no matchup in the fixture names a team mTeam never sent
            "id": 0, "name": "Unknown team", "abbrev": "?",
            "monogram": "?", "hue": 0, "logo": "", "record": "",
            "total": 0.0, "projected": 0.0, "starters": [], "bench": [],
            "missing": True,
        }
    return {
        "id": team.id,
        "name": team.name,
        # No manager name, here or anywhere below. These are ten real people's
        # ESPN display names -- their actual usernames -- and this app serves a
        # public page and a public JSON endpoint. `/api/state` was publishing
        # all ten of them.
        #
        # Stopped at the view model rather than at each template, for the same
        # reason the cookie audit lives in `config.py`: if the name cannot leave
        # here, there is no second place to check. The field stays on the model,
        # where it is needed to join ESPN's members block to a team.
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
        # From the game state when there is one. `remaining <= 0` alone marked
        # a player "final" the moment he passed his projection in the second
        # quarter, and a player projected for nothing as final before kickoff.
        "done": player.game_over if player.game_over is not None else player.remaining <= 0,
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
                "leader": leader["name"],
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
        card["win_tone"] = win_tone(probability)
        card.update(_pace(snap, side))
        card.update(_progress(side))
        other = matchup.side_for(matchup.away.team_id if side is matchup.home else matchup.home.team_id) \
            if matchup and side else None
        rival = snap.team(other.team_id) if other else None
        # The opponent by TEAM name. Not the abbreviation: ESPN abbreviations
        # are typed by the managers and in this league several are a first name.
        card["opponent"] = rival.name if rival else ""
        card["opponent_total"] = round(other.total, 2) if other else None
        card["margin"] = round(side.total - other.total, 2) if other else None
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


#: Where the score on a card turns from red to amber to green, by the chance of
#: winning this week's head-to-head. Symmetric around a coin flip: inside ten
#: points of fifty is a genuine contest and gets the in-between colour, and a
#: team the simulation gives six in ten is winning in a way worth showing.
WIN_TONES = ((0.60, "green"), (0.40, "amber"))


def win_tone(probability: float | None) -> str:
    """`green`, `amber`, `red`, or "" when there is no matchup to judge."""
    if probability is None:
        return ""  # cold: every fixture team has a matchup; a bye week has none
    for floor, tone in WIN_TONES:
        if probability >= floor:
            return tone
    return "red"


def _progress(side: Side | None) -> dict[str, int]:
    """How many starters have played, are playing, and are still to play.

    From the NFL game state rather than from points: a starter on zero has
    either not kicked off or had a bad day, and only the scoreboard knows which.
    A starter with no game state at all -- the NFL feed down, a bye -- counts
    as still to play, because nothing has shown he is done.
    """
    played = playing = to_play = 0
    for player in side.starters if side else []:
        if player.game_over:
            played += 1
        elif player.game_elapsed:
            playing += 1
        else:
            to_play += 1
    return {"played": played, "playing": playing, "to_play": to_play}


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
                    (side.team_id, team.name if team else "?", player)
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
        # Joined on the id the row already carries. It used to match on the
        # manager's name, which is a join on a string nothing guarantees is
        # unique and which no longer leaves the model.
        row["win_prob"] = by_team.get(row["id"])

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
            (snap.team(side.team_id).name if snap.team(side.team_id) else "?")
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
                "text": f"{away.name} v {home.name} \u00b7 "
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
                "text": f"{team.name} has {side.in_play} left; "
                        f"{(snap.team(other.team_id).name if snap.team(other.team_id) else '?')} has none",
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
        n = hottest["beating"]
        line("HOT", hottest,
             f"{hottest['name']} have {n} starter{'' if n == 1 else 's'} beating projection",
             str(n), True)

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
        "team": team.name, "hue": team.hue, "id": team.id,
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
        "team": team.name, "hue": team.hue, "id": team.id,
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
        "team": team.name, "hue": team.hue, "id": team.id,
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


# --------------------------------------------------------------------------
# the season panels
#
# Eight views that all answer a question the week's own numbers cannot. They
# share a shape: a list of rows, one per team, already sorted and already
# carrying the colour, because the templates that draw them are grids and
# charts rather than tables and a template is the wrong place to decide an
# ordering.
#
# All of them read `season_records`, which walks the whole fixture grid. That is
# arithmetic rather than simulation, so it is cheap, but it is not free and six
# panels asking for it on every poll is six walks: `_records` memoises it for
# the snapshot, the same way `engine/simulate` memoises the expensive ones.
# --------------------------------------------------------------------------

#: The drawing box for a sparkline, in user units. The SVG is scaled by CSS, so
#: these are a coordinate system rather than pixels.
SPARK = (100.0, 34.0)


def _plot(values, floor: float, ceiling: float, width: float, height: float,
          pad: float = 1.5) -> str:
    """`values` as an SVG points list, to one shared scale.

    The geometry is computed here and not in the template, for the reason the
    module docstring gives: a template that does arithmetic cannot be tested
    without a request context, and a sparkline is nothing but arithmetic. The
    scale is passed in rather than taken per row, so ten sparklines are actually
    comparable -- per-row scaling makes a team that never leaves 100 to 110 look
    exactly as dramatic as one swinging between 60 and 150.
    """
    if not values:
        return ""  # cold: every caller checks for scores before asking for a path
    span = max(1e-6, ceiling - floor)
    step = (width - pad * 2) / max(1, len(values) - 1)
    return " ".join(
        f"{pad + i * step:.2f},"
        f"{height - pad - min(1.0, max(0.0, (v - floor) / span)) * (height - pad * 2):.2f}"
        for i, v in enumerate(values)
    )


def _team_row(team) -> dict[str, Any]:
    """The identity every one of these rows starts with."""
    return {"id": team.id, "team": team.name, "abbrev": team.abbrev,
            "hue": team.hue}


_RECORDS: "OrderedDict[tuple, dict]" = OrderedDict()


def _records(snap: LeagueSnapshot) -> dict:
    """Season records, computed once per snapshot state.

    Keyed on the settled weeks and their scores rather than on the snapshot
    object, which is rebuilt on every request: keying on identity would miss
    every time while looking like it worked, which is the trap the simulation
    memo documents at length.
    """
    # Every settled SCORE, not the count of games per week. Counting them was
    # wrong in a way that hides: ESPN corrects stats retrospectively, which is
    # routine in fantasy and lands on a Tuesday, and a correction changes the
    # mean, the spread, the all-play record and the luck figure while leaving
    # the number of games in the week exactly as it was. Six panels read this,
    # and every one of them would have gone on showing the pre-correction
    # season for as long as the process lived -- quickly, silently, and with no
    # error anywhere. Caught by asking the memo to miss rather than by asking it
    # to hit.
    key = (snap.season, snap.scoring_period,
           tuple(sorted((week, side.team_id, round(side.total, 2))
                        for week, games in snap.settled_weeks.items()
                        for game in games
                        for side in (game.home, game.away))),
           tuple(sorted((side.team_id, round(side.total, 1))
                        for m in (snap.live_matchups or snap.matchups)
                        for side in (m.home, m.away))))
    if key in _RECORDS:
        _RECORDS.move_to_end(key)
        return _RECORDS[key]
    value = season_records(snap)
    _RECORDS[key] = value
    _RECORDS.move_to_end(key)
    while len(_RECORDS) > 4:
        _RECORDS.popitem(last=False)
    return value


def shape_view(snap: LeagueSnapshot, store=None) -> dict[str, Any]:
    """Every team's season as a line, with what they could have scored behind it.

    A table of results says who won. It cannot say that a team averaging 110 got
    there with a 53 and a 142 in it, which is the difference between a good team
    and a lucky one, and it is the first thing anybody wants to argue about.
    """
    records = _records(snap)
    optimal = _optimal_by_week(store, snap) if store is not None else {}
    rows = []
    for team in snap.teams:
        record = records.get(team.id)
        scores = [round(v, 1) for v in (record.weekly if record else [])]
        if not scores:
            continue
        row = _team_row(team)
        row.update({
            "scores": scores,
            "best": [optimal.get((week, team.id)) for week in
                     sorted(snap.settled_weeks)[:len(scores)]],
            "mean": round(record.mean, 1),
            "high": max(scores), "low": min(scores),
            # The last three against the three before them: a direction, not a
            # slope through ten weeks, because nobody cares how a team was
            # trending in September.
            "trend": round(sum(scores[-3:]) / min(3, len(scores))
                           - sum(scores[-6:-3]) / max(1, len(scores[-6:-3])), 1)
            if len(scores) >= 4 else 0.0,
        })
        rows.append(row)
    rows.sort(key=lambda r: -r["mean"])
    everything = sorted(v for row in rows for v in row["scores"])
    # The scale is the 5th to 95th percentile, not the extremes, and the plotted
    # values are clamped into it. One team's 53.5 in a field that otherwise
    # lives between 90 and 130 stretches the axis over a hundred points, and
    # every line in the panel comes out flat: the outlier is drawn perfectly and
    # the other ninety-nine readings say nothing. Clamping loses the depth of
    # one trough and gives back the shape of everything else, which is the
    # trade this panel exists to make.
    if everything:
        lo = everything[int(len(everything) * 0.05)]
        hi = everything[min(len(everything) - 1, int(len(everything) * 0.95))]
        floor, ceiling = (lo, hi) if hi - lo > 5 else (min(everything), max(everything))
    else:
        floor, ceiling = 0.0, 1.0
    width, height = SPARK
    for row in rows:
        row["path"] = _plot(row["scores"], floor, ceiling, width, height)
        # The area under the line, closed along the bottom edge.
        row["area"] = f"{row['path']} {width - 1.5:.2f},{height} 1.5,{height}" if row["path"] else ""
        row["mean_y"] = round(height - 1.5 - (row["mean"] - floor)
                              / max(1e-6, ceiling - floor) * (height - 3), 2)
        best = [v for v in row["best"] if v is not None]
        row["best_path"] = (_plot(row["best"], floor, ceiling, width, height)
                            if len(best) == len(row["best"]) and best else "")
    return {
        "rows": rows,
        "weeks": sorted(snap.settled_weeks)[:max((len(r["scores"]) for r in rows), default=0)],
        "width": width, "height": height,
        "floor": round(floor, 1), "ceiling": round(ceiling, 1),
        "has_optimal": any(any(v is not None for v in r["best"]) for r in rows),
    }


def _optimal_by_week(store, snap: LeagueSnapshot) -> dict[tuple[int, int], float]:
    """`(week, team_id) -> the best they could have scored`, from the history."""
    out: dict[tuple[int, int], float] = {}
    if store is None or not getattr(store, "available", False):
        return out
    for entry in store.weeks(snap.season):
        for team in store.week(snap.season, entry["week"]).get("teams", []):
            if team.get("optimal"):
                out[(entry["week"], team["team_id"])] = round(team["optimal"], 1)
    return out


def allplay_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """Who would have beaten whom this week, as a grid.

    PUNT already reduces this to one number per team. The number is the honest
    summary and the grid is the argument: it names the single fixture that went
    wrong instead of averaging it into a record.
    """
    scores: dict[int, float] = {}
    opponents: dict[int, int] = {}
    for matchup in snap.live_matchups or snap.matchups:
        scores[matchup.home.team_id] = round(matchup.home.total, 1)
        scores[matchup.away.team_id] = round(matchup.away.total, 1)
        opponents[matchup.home.team_id] = matchup.away.team_id
        opponents[matchup.away.team_id] = matchup.home.team_id

    teams = [t for t in snap.teams if t.id in scores]
    teams.sort(key=lambda t: -scores[t.id])
    rows = []
    for me in teams:
        cells = []
        for them in teams:
            beaten = None if me.id == them.id else scores[me.id] > scores[them.id]
            cells.append({
                # The team NAME as well as the code. ESPN abbreviations are
                # whatever the manager typed, and in the live league they are
                # mostly bits of their own names -- "Joe", "Mill", "ROBB" --
                # so a sheet listing them reads as a list of people.
                "id": them.id, "team": them.name, "abbrev": them.abbrev,
                "beaten": beaten,
                "margin": round(scores[me.id] - scores[them.id], 1),
                # The one cell in the row that actually counted.
                "real": opponents.get(me.id) == them.id,
            })
        row = _team_row(me)
        wins = sum(1 for c in cells if c["beaten"] is True)
        row.update({
            "score": scores[me.id], "cells": cells, "wins": wins,
            "losses": len(teams) - 1 - wins,
            # Whether the one fixture that counted went their way. The point of
            # the grid is the gap between this and `wins`.
            "won": (opponents.get(me.id) is not None
                    and scores[me.id] > scores.get(opponents[me.id], 0.0)),
        })
        rows.append(row)
    return {"rows": rows, "teams": [_team_row(t) for t in teams],
            "week": snap.scoring_period}


def seeds_view(snap: LeagueSnapshot, draws: int = 2500) -> dict[str, Any]:
    """Not whether they make the playoffs, but where they finish.

    The simulator has computed this since the first commit and nothing has ever
    displayed it: `PlayoffOdds.seed_odds` is a full distribution over finishing
    positions from the same seasons the headline percentage comes from. A single
    number cannot tell a team that is certainly third from one that is either
    first or fifth, and those are very different Sundays.
    """
    if not snap.season_schedule:
        return {"available": False, "rows": []}
    odds = playoff_odds(snap, draws=draws)
    if not odds:
        return {"available": False, "rows": []}  # cold: a league with no teams
    places = max(1, snap.settings.playoff_team_count or 6)
    size = len(snap.teams) or 10
    rows = []
    for team in snap.teams:
        chance = odds.get(team.id)
        if chance is None:
            continue  # cold: every team in mTeam is in the odds
        seeds = [{"seed": seed, "share": round(chance.seed_odds.get(seed, 0.0), 4),
                  "made_it": seed <= places}
                 for seed in range(1, size + 1)]
        row = _team_row(team)
        row.update({
            "odds": round(chance.odds, 3), "seeds": seeds,
            "likeliest": max(seeds, key=lambda s: s["share"])["seed"],
            # How settled the answer is. A team that lands on one seed in most
            # seasons is a different story from one spread across five, and the
            # spread is the part the headline percentage throws away.
            "spread": sum(1 for s in seeds if s["share"] >= 0.10),
            "clinched": chance.clinched, "eliminated": chance.eliminated,
        })
        rows.append(row)
    rows.sort(key=lambda r: (-r["odds"], r["likeliest"]))
    return {"available": bool(rows), "rows": rows, "places": places, "draws": draws}


def gauntlet_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """What everybody has left to play, and how frightening it is.

    Strength of schedule, but the version that means something in fantasy. The
    published figures are about NFL defences; the thing that decides your season
    is which of these ten people you still have to outscore, and how reliably
    they score. A wide opponent is one you might catch on a bad week. A narrow
    one, high up, is not.
    """
    records = _records(snap)
    weeks = snap.season_weeks()
    settled = set(snap.settled_weeks)
    current = snap.settings.current_matchup_period
    future = {week: games for week, games in weeks.items()
              if week not in settled and week >= current}
    if not future:
        return {"available": False, "rows": []}

    means = [records[tid].mean for tid in records if records[tid].weeks]
    league = round(sum(means) / len(means), 1) if means else 0.0

    rows = []
    for team in snap.teams:
        fixtures = []
        for week in sorted(future):
            for matchup in future[week]:
                sides = (matchup.home, matchup.away)
                if team.id not in (s.team_id for s in sides):
                    continue
                other = next(s for s in sides if s.team_id != team.id)
                opponent = snap.team(other.team_id)
                record = records.get(other.team_id)
                if opponent is None or record is None or not record.weeks:
                    continue  # cold: the grid never names a team mTeam did not send
                fixtures.append({
                    "week": week, "id": opponent.id, "abbrev": opponent.abbrev,
                    "team": opponent.name, "hue": opponent.hue,
                    "mean": round(record.mean, 1), "sigma": round(record.sigma, 1),
                    # Its own lane down the track. Four opponents drawn on one
                    # line overlap into a single rainbow smear where nothing can
                    # be told from anything, which is exactly how it first came
                    # out: a lane each keeps them legible and puts them in week
                    # order down the row, which is the order they arrive in.
                    "lane": len(fixtures),
                })
        if not fixtures:
            continue  # cold: a team on a bye in every remaining week
        average = round(sum(f["mean"] for f in fixtures) / len(fixtures), 1)
        for fixture in fixtures:
            fixture["lanes"] = len(fixtures)
        row = _team_row(team)
        row.update({
            "fixtures": fixtures, "average": average,
            "harder": average > league,
            "gap": round(average - league, 1),
            "toughest": max(fixtures, key=lambda f: f["mean"]),
        })
        rows.append(row)
    rows.sort(key=lambda r: -r["average"])
    floor = min([f["mean"] - f["sigma"] for r in rows for f in r["fixtures"]] + [140.0])
    ceiling = max([f["mean"] + f["sigma"] for r in rows for f in r["fixtures"]] + [floor + 1.0])
    return {"available": bool(rows), "rows": rows, "league": league,
            "floor": round(floor, 1), "ceiling": round(ceiling, 1),
            "weeks_left": len(future)}


#: The slots of an NFL week, in the order anybody would say them.
WINDOW_ORDER = ["THU", "EARLY", "LATE", "SNF", "MNF", "SAT"]
WINDOW_LABELS = {"THU": "Thu", "EARLY": "1pm", "LATE": "4pm",
                 "SNF": "Sun night", "MNF": "Mon night", "SAT": "Sat"}


def clock_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """When each team's points arrive, and how much is still to come.

    Two managers on the same score are in completely different moods if one has
    banked it and the other has a running back left, and nothing anywhere shows
    that. It is the most useful thing on the page at six o'clock on a Sunday.
    """
    used = [w for w in WINDOW_ORDER
            if any(game.window == w for game in snap.games.values())]
    if not used:
        return {"available": False, "rows": [], "windows": []}

    rows = []
    for matchup in snap.live_matchups or snap.matchups:
        for side in (matchup.home, matchup.away):
            team = snap.team(side.team_id)
            if team is None:
                continue  # cold: a fixture naming a team mTeam never sent
            parts = {w: {"window": w, "label": WINDOW_LABELS[w], "points": 0.0,
                         "to_come": 0.0, "players": 0} for w in used}
            for player in side.starters:
                game = snap.games.get(player.pro_team_id)
                if game is None or game.window not in parts:
                    continue  # cold: a starter on a bye, which the fixture has none of
                part = parts[game.window]
                part["points"] += player.points
                part["to_come"] += player.remaining
                part["players"] += 1
            ordered = [parts[w] for w in used]
            for part in ordered:
                part["points"] = round(part["points"], 1)
                part["to_come"] = round(part["to_come"], 1)
            row = _team_row(team)
            banked = sum(p["points"] for p in ordered)
            left = sum(p["to_come"] for p in ordered)
            row.update({
                "parts": ordered, "banked": round(banked, 1), "to_come": round(left, 1),
                # The number the panel exists for: how much of this team's day is
                # already decided.
                "settled": round(banked / (banked + left) * 100) if banked + left else 100,
            })
            rows.append(row)
    rows.sort(key=lambda r: -r["settled"])
    ceiling = max([p["points"] + p["to_come"] for r in rows for p in r["parts"]] + [1.0])
    return {"available": bool(rows), "rows": rows,
            "windows": [{"window": w, "label": WINDOW_LABELS[w]} for w in used],
            "ceiling": round(ceiling, 1)}


def ledger_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """Points by starting slot, against the league median for that slot.

    Two teams on the same total can be built completely differently, and the
    total cannot say which. This can, and it is the one view here that suggests
    what to do about it rather than just describing the damage.
    """
    per_team: dict[int, dict[str, float]] = {}
    for matchup in snap.live_matchups or snap.matchups:
        for side in (matchup.home, matchup.away):
            slots: dict[str, float] = {}
            for player in side.starters:
                slots[player.slot] = round(slots.get(player.slot, 0.0) + player.points, 2)
            per_team[side.team_id] = slots
    if not per_team:
        return {"available": False, "rows": [], "slots": [], "started": 0}

    # How much of the league has actually played. Before the early games kick
    # off this panel compares zero against zero: every median is 0.0, every
    # difference is 0.0, and ten rows of "+0.0" is a wall that reads as a broken
    # panel rather than as an early one. It said nothing useful and it said it
    # at great length.
    started = sum(1 for slots in per_team.values() if sum(slots.values()) > 0)
    if started * 2 < len(per_team):
        return {"available": False, "rows": [], "slots": [], "started": started,
                "teams": len(per_team)}

    order = [s for s in ("QB", "RB", "WR", "TE", "FLEX", "D/ST", "K")
             if any(s in slots for slots in per_team.values())]
    order += sorted({s for slots in per_team.values() for s in slots} - set(order))

    median: dict[str, float] = {}
    for slot in order:
        values = sorted(slots.get(slot, 0.0) for slots in per_team.values())
        middle = len(values) // 2
        median[slot] = round(
            values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2, 1)

    rows = []
    for team in snap.teams:
        slots = per_team.get(team.id)
        if slots is None:
            continue  # cold: a team with no fixture this week
        cells = [{"slot": slot, "points": round(slots.get(slot, 0.0), 1),
                  "diff": round(slots.get(slot, 0.0) - median[slot], 1),
                  "median": median[slot]}
                 for slot in order]
        row = _team_row(team)
        row.update({
            "cells": cells,
            "total": round(sum(c["points"] for c in cells), 1),
            "best": max(cells, key=lambda c: c["diff"]),
            "worst": min(cells, key=lambda c: c["diff"]),
        })
        rows.append(row)
    rows.sort(key=lambda r: -r["total"])
    spread = max([abs(c["diff"]) for r in rows for c in r["cells"]] + [1.0])
    return {"available": bool(rows), "rows": rows, "slots": order,
            "median": median, "spread": round(spread, 1)}


def volatility_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """Average score against how wildly it swings.

    Four quadrants and each is a different kind of season. High and steady is
    the team nobody wants to draw; high and wild is the one that beats you by
    forty once and loses to everybody else; low and steady is honest; low and
    wild is the worst place to be, because the good weeks are wasted and the bad
    ones are unsurvivable.
    """
    records = _records(snap)
    rows = []
    for team in snap.teams:
        record = records.get(team.id)
        if record is None or record.weeks < 2:
            continue
        row = _team_row(team)
        row.update({"mean": round(record.mean, 1), "sigma": round(record.sigma, 1),
                    "weeks": record.weeks,
                    "high": round(max(record.weekly), 1),
                    "low": round(min(record.weekly), 1)})
        rows.append(row)
    if not rows:
        return {"available": False, "rows": []}
    means = [r["mean"] for r in rows]
    sigmas = [r["sigma"] for r in rows]
    mid_mean = round(sum(means) / len(means), 1)
    mid_sigma = round(sum(sigmas) / len(sigmas), 1)
    pad = 4
    mean_floor, mean_ceiling = min(means) - pad, max(means) + pad
    sigma_floor, sigma_ceiling = max(0.0, min(sigmas) - 2), max(sigmas) + 2
    for row in rows:
        # Percentages of the plot box, so the template places a dot with `left`
        # and `top` and never does arithmetic.
        row["x"] = round((row["sigma"] - sigma_floor)
                         / max(1e-6, sigma_ceiling - sigma_floor) * 100, 2)
        row["y"] = round(100 - (row["mean"] - mean_floor)
                         / max(1e-6, mean_ceiling - mean_floor) * 100, 2)
        high, wild = row["mean"] >= mid_mean, row["sigma"] >= mid_sigma
        row["quadrant"] = ("the one nobody wants to draw" if high and not wild
                           else "boom or bust" if high and wild
                           else "honest" if not wild
                           else "the worst place to be")
        row["good"] = high
    rows.sort(key=lambda r: -r["mean"])
    return {
        "available": True, "rows": rows,
        "mid_mean": mid_mean, "mid_sigma": mid_sigma,
        "mid_x": round((mid_sigma - sigma_floor)
                       / max(1e-6, sigma_ceiling - sigma_floor) * 100, 2),
        "mid_y": round(100 - (mid_mean - mean_floor)
                       / max(1e-6, mean_ceiling - mean_floor) * 100, 2),
        "mean_floor": round(mean_floor, 1), "mean_ceiling": round(mean_ceiling, 1),
        "sigma_floor": round(sigma_floor, 1), "sigma_ceiling": round(sigma_ceiling, 1),
    }


def swap_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """Every team's own scores, replayed against everybody else's fixture list.

    This is the end of the argument. "I have had no luck" is either true or it
    is not, and the honest test is to leave a manager's scores exactly as they
    were and give them somebody else's opponents week by week. PUNT already
    reports luck as a single number; this says who, specifically, has had the
    schedule everyone else wanted.
    """
    records = _records(snap)
    weeks = sorted(snap.settled_weeks)
    if len(weeks) < 2:
        return {"available": False, "rows": [], "weeks": len(weeks)}

    # week -> {team_id: opponent_id}
    grid: dict[int, dict[int, int]] = {}
    for week in weeks:
        pairs: dict[int, int] = {}
        for matchup in snap.settled_weeks.get(week, []):
            pairs[matchup.home.team_id] = matchup.away.team_id
            pairs[matchup.away.team_id] = matchup.home.team_id
        if pairs:
            grid[week] = pairs
    scores = {tid: record.weekly for tid, record in records.items()}
    order = [t for t in snap.teams if scores.get(t.id)]
    if not grid or len(order) < 2:
        return {"available": False, "rows": [], "weeks": len(weeks)}

    def record_under(me: int, schedule_of: int) -> tuple[int, int]:
        """My weekly scores, against whoever `schedule_of` actually faced."""
        wins = losses = 0
        for index, week in enumerate(sorted(grid)):
            opponent = grid[week].get(schedule_of)
            if opponent is None or opponent == me:
                continue
            if index >= len(scores[me]) or index >= len(scores.get(opponent, [])):
                continue  # cold: a team that joined the league mid-season
            if scores[me][index] > scores[opponent][index]:
                wins += 1
            elif scores[me][index] < scores[opponent][index]:
                losses += 1
        return wins, losses

    rows = []
    for me in order:
        own = record_under(me.id, me.id)
        cells = []
        for them in order:
            wins, losses = record_under(me.id, them.id)
            cells.append({"id": them.id, "abbrev": them.abbrev, "team": them.name,
                          "record": f"{wins}-{losses}", "wins": wins,
                          "diff": wins - own[0], "own": them.id == me.id})
        row = _team_row(me)
        kindest = max(cells, key=lambda c: c["diff"])
        cruellest = min(cells, key=lambda c: c["diff"])
        row.update({
            "own": f"{own[0]}-{own[1]}", "cells": cells,
            "kindest": kindest, "cruellest": cruellest,
            # How much the fixture list, rather than the scoring, has decided
            # this team's season.
            "swing": kindest["diff"] - cruellest["diff"],
        })
        rows.append(row)
    rows.sort(key=lambda r: -r["swing"])
    return {"available": bool(rows), "rows": rows, "weeks": len(grid),
            "order": [_team_row(t) for t in order]}


# --------------------------------------------------------------------------
# the season detail sheets
#
# Each is the panel's own view model plus the depth a full sheet has room for.
# Built on the panel rather than beside it on purpose: a sheet that recomputed
# its figures would be a second opinion of the number that was tapped, and two
# numbers for the same thing on one screen is worse than one number nobody can
# see. The extra depth is the part a row cannot hold.
# --------------------------------------------------------------------------

def _row_for(view: dict[str, Any], team_id: int) -> dict[str, Any] | None:
    return next((r for r in view.get("rows", []) if r["id"] == team_id), None)


def shape_detail(snap: LeagueSnapshot, team_id: int, store=None) -> dict[str, Any]:
    """One team's season, week by week, with who they played and what happened."""
    row = _row_for(shape_view(snap, store), team_id)
    if row is None:
        return {}
    weeks = sorted(snap.settled_weeks)
    optimal = _optimal_by_week(store, snap) if store is not None else {}
    played = []
    for index, score in enumerate(row["scores"]):
        week = weeks[index] if index < len(weeks) else None
        opponent = opponent_score = None
        for matchup in snap.settled_weeks.get(week, []) if week else []:
            for side, other in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
                if side.team_id == team_id:
                    team = snap.team(other.team_id)
                    opponent = team.name if team else "?"
                    opponent_score = round(other.total, 1)
        best = optimal.get((week, team_id))
        played.append({
            "week": week, "score": score, "opponent": opponent,
            "against": opponent_score,
            "won": None if opponent_score is None else score > opponent_score,
            "best": best,
            "left": round(best - score, 1) if best is not None else None,
        })
    return {
        "row": row, "weeks": played,
        "high": row["high"], "low": row["low"], "mean": row["mean"],
        "swing": round(row["high"] - row["low"], 1),
        "wins": sum(1 for w in played if w["won"]),
        "losses": sum(1 for w in played if w["won"] is False),
    }


def grid_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """This week against everybody, and the one fixture that counted."""
    view = allplay_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    beaten = [c for c in row["cells"] if c["beaten"] is True]
    lost = [c for c in row["cells"] if c["beaten"] is False]
    real = next((c for c in row["cells"] if c["real"]), None)
    return {
        "row": row, "week": view["week"], "real": real,
        "beaten": sorted(beaten, key=lambda c: -c["margin"]),
        "lost": sorted(lost, key=lambda c: c["margin"]),
        # The gap between the week they had and the week the fixture list gave
        # them, which is the whole argument this panel exists to settle.
        "unlucky": bool(real and not row["won"] and len(beaten) > len(lost)),
        "fortunate": bool(real and row["won"] and len(lost) > len(beaten)),
    }


def seeds_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """Where one team finishes, across every simulated season."""
    view = seeds_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    odds = playoff_odds(snap).get(team_id)
    live = [s for s in row["seeds"] if s["share"] > 0]
    return {
        "row": row, "places": view["places"], "draws": view["draws"],
        "seeds": live,
        "magic": getattr(odds, "magic_number", None),
        "mean_wins": round(getattr(odds, "mean_wins", 0.0), 1),
        "remaining": getattr(odds, "remaining", 0),
        "ceiling": min((s["seed"] for s in live), default=0),
        "floor": max((s["seed"] for s in live), default=0),
    }


def gauntlet_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """Every fixture a team has left, in the order it arrives."""
    view = gauntlet_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    fixtures = row["fixtures"]
    return {
        "row": row, "league": view["league"], "fixtures": fixtures,
        "toughest": max(fixtures, key=lambda f: f["mean"]),
        "kindest": min(fixtures, key=lambda f: f["mean"]),
        # The one you can least afford to catch on its good day.
        "wildest": max(fixtures, key=lambda f: f["sigma"]),
    }


def clock_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """Every starter, grouped by the window his game kicks off in."""
    view = clock_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    side = None
    for matchup in snap.live_matchups or snap.matchups:
        for candidate in (matchup.home, matchup.away):
            if candidate.team_id == team_id:
                side = candidate
    groups: dict[str, list[dict[str, Any]]] = {p["window"]: [] for p in row["parts"]}
    for player in (side.starters if side else []):
        game = snap.games.get(player.pro_team_id)
        if game is None or game.window not in groups:
            continue  # cold: a starter on a bye, which the fixture has none of
        groups[game.window].append({
            "name": player.name, "slot": player.slot, "pro_team": player.pro_team,
            "points": round(player.points, 2), "remaining": round(player.remaining, 2),
            "done": player.game_over, "when": ("final" if game.finished
                                               else f"Q{game.period} {game.clock}" if game.live
                                               else "not started"),
        })
    parts = []
    for part in row["parts"]:
        parts.append({**part, "players": sorted(
            groups.get(part["window"], []), key=lambda p: -p["points"])})
    return {"row": row, "parts": [p for p in parts if p["players"]],
            "banked": row["banked"], "to_come": row["to_come"],
            "settled": row["settled"]}


def ledger_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """Each slot, the players in it, and the league's median for the same slot."""
    view = ledger_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    side = None
    for matchup in snap.live_matchups or snap.matchups:
        for candidate in (matchup.home, matchup.away):
            if candidate.team_id == team_id:
                side = candidate
    by_slot: dict[str, list[dict[str, Any]]] = {}
    for player in (side.starters if side else []):
        by_slot.setdefault(player.slot, []).append({
            "name": player.name, "pro_team": player.pro_team,
            "points": round(player.points, 2), "projected": round(player.projected, 2),
            "done": player.game_over,
        })
    cells = [{**cell, "players": by_slot.get(cell["slot"], [])} for cell in row["cells"]]
    return {"row": row, "cells": cells, "median": view["median"],
            "best": row["best"], "worst": row["worst"], "total": row["total"]}


def volatility_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """One team's weekly spread, and what kind of season that makes it."""
    view = volatility_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    record = _records(snap).get(team_id)
    scores = [round(v, 1) for v in (record.weekly if record else [])]
    weeks = sorted(snap.settled_weeks)[:len(scores)]
    ordered = sorted(
        ({"week": weeks[i] if i < len(weeks) else None, "score": s,
          "against_mean": round(s - row["mean"], 1)} for i, s in enumerate(scores)),
        key=lambda w: -w["score"])
    return {
        "row": row, "weeks": ordered,
        "mid_mean": view["mid_mean"], "mid_sigma": view["mid_sigma"],
        "swing": round(row["high"] - row["low"], 1),
        # How many weeks land within one standard deviation: the plain-English
        # version of a sigma nobody outside a lab reads as a quantity.
        "typical": sum(1 for w in ordered if abs(w["against_mean"]) <= row["sigma"]),
    }


def swap_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """This team's record under every other schedule in the league."""
    view = swap_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    others = [c for c in row["cells"] if not c["own"]]
    return {
        "row": row, "weeks": view["weeks"],
        "cells": sorted(others, key=lambda c: -c["wins"]),
        "kindest": row["kindest"], "cruellest": row["cruellest"],
        "own": row["own"],
        # How many of the other nine schedules would have been better than the
        # one they got. That is the number the argument is actually about.
        "better": sum(1 for c in others if c["diff"] > 0),
        "worse": sum(1 for c in others if c["diff"] < 0),
    }


#: What each kind of ticker line means, in one sentence, because the strip shows
#: the line and never has room to say why that line exists.
CHANGE_MEANING = {
    "SCORE": "A team's total moved by enough to be worth looking up for.",
    "WIN": "The live chance of winning this week's head-to-head moved by five points or more.",
    "PLAYOFF": "The chance of making the playoffs moved, across 2,500 simulated seasons.",
    "SEED": "A team clinched a playoff place or was eliminated from the race.",
    "FORM": "A team moved at least two places in the album, which is ranked on form rather than points.",
    "HOT": "A starter passed 1.6 times what he was due by now, prorated by how much of his game has been played.",
    "COLD": "A starter fell below 45% of what he was due by now.",
    "BENCH": "Somebody on the bench scored, so the points left there went up.",
    "FINAL": "Every starter's game has finished. That score cannot move again.",
    "SAID": "The commentary engine detected a play and had something to say about it.",
    # Commentary lines carry their Moment's kind, so the word beside a line that
    # just sounded a horn says which horn it was.
    "TOUCHDOWN": "A starter scored a touchdown. Every phone played the horn for it.",
    "BIG PLAY": "A starter gained enough in one play to move his team's evening. The whoosh.",
    "LEAD": "The team that was behind in this head-to-head is now ahead.",
    "GOOSE EGG": "A starter's game finished and he scored nothing at all.",
    "DOOM": "This matchup's chance of winning dropped low enough that it is slipping away.",
    "CLINCH": "The chance of winning this head-to-head went past the point of no return.",
    "BENCHED": "A benched player outscored the starter who played instead of him.",
    "MILESTONE": "A team or a player passed a round number worth a chime.",
    "INJURY": "A starter's injury status changed during his game.",
    "RED ZONE": "A player somebody in the league is starting is on a drive inside the five. The rising tone.",
    "NO GOOD": "That drive inside the five ended without a touchdown. The record scratch.",
}


def change_detail(live, change_id: str, snap: LeagueSnapshot | None = None) -> dict[str, Any]:
    """One line of the ticker, in full.

    The strip is one row of thirteen words and it rotates every four seconds, so
    everything about why the line is there has to live here instead: what moved,
    by how much, what that kind of line means, and what else has happened to the
    same team this afternoon.
    """
    if live is None:
        return {}
    change = next((c for c in live.ticker.recent(200) if c.id == change_id), None)
    if change is None:
        return {}
    team = snap.team(change.team_id) if snap is not None else None
    same_team = [c.to_json() for c in live.ticker.recent(200)
                 if c.team_id == change.team_id and c.id != change.id][:8]

    context: dict[str, Any] = {}
    if snap is not None and team is not None:
        for matchup in snap.live_matchups or snap.matchups:
            for side, other in ((matchup.home, matchup.away), (matchup.away, matchup.home)):
                if side.team_id != team.id:
                    continue
                opponent = snap.team(other.team_id)
                context = {
                    "score": round(side.total, 1),
                    "projected": round(side.live_projection, 1),
                    "in_play": side.in_play,
                    "opponent": opponent.name if opponent else "?",
                    "opponent_score": round(other.total, 1),
                    "margin": round(side.total - other.total, 1),
                }
    return {
        "change": change.to_json(),
        "meaning": CHANGE_MEANING.get(change.kind, ""),
        "team": team.name if team else change.team,
        "hue": team.hue if team else change.hue,
        "logo": f"/img/team/{team.id}" if team and team.logo else "",
        "monogram": team.monogram if team else "?",
        "record": team.record if team else "",
        "context": context,
        "also": same_team,
    }


# -- the front office ---------------------------------------------------------
#
# Four panels about decisions rather than games: next week's lineups, whether
# ESPN's projections can be trusted, the draft, and the moves since. Everything
# above is about what happened to the lineups the managers set; these ask
# whether they set the right ones.

#: Positions in the order a lineup is read.
FRONT_POSITIONS = ("QB", "RB", "WR", "TE", "K", "D/ST")


def _season_points(snap: LeagueSnapshot) -> dict[int, dict[int, float]]:
    """Player id -> week -> points, for every player the front office tracks.

    Settled weeks come from the player feed, which follows a player wherever he
    goes, waivers included. The live week comes from the box score, which moves
    every poll, so the draft and the move ledger are live on a Sunday rather
    than a day behind it. A player nobody rosters this week keeps whatever the
    player feed says for it.
    """
    current = snap.scoring_period
    points = {pid: {week: value for week, value in history.points.items() if week <= current}
              for pid, history in snap.player_history.items()}
    for matchup in snap.live_matchups or snap.matchups:
        for side in (matchup.home, matchup.away):
            for player in side.players:
                points.setdefault(player.id, {})[current] = player.points
    return points


def _people(snap: LeagueSnapshot) -> dict[int, dict[str, str]]:
    """Player id -> name and position, from whichever feed has the player.
    Team defences are only in the box scores: ESPN's player feed skips them."""
    people = {pid: {"name": h.name, "position": h.position}
              for pid, h in snap.player_history.items()}
    players = [p for roster in snap.next_rosters.values() for p in roster]
    players += [p for m in (snap.live_matchups or snap.matchups)
                for side in (m.home, m.away) for p in side.players]
    for player in players:
        people[player.id] = {"name": player.name, "position": player.position}
    return people


def _person(people: dict[int, dict[str, str]], player_id: int) -> dict[str, str]:
    """A player's name and position, or a defence's from its id, or "Unknown"."""
    if player_id in people:
        return people[player_id]
    team = defence_team(player_id)
    if team:
        return {"name": f"{team} D/ST", "position": "D/ST"}
    return {"name": "Unknown player", "position": ""}  # cold: every drafted or moved player is in a feed


def _rostered_by(snap: LeagueSnapshot) -> dict[int, int]:
    """Player id -> the team that has him now. Missing means nobody does."""
    where = {pid: h.on_team_id for pid, h in snap.player_history.items() if h.on_team_id > 0}
    for team_id, roster in snap.next_rosters.items():
        for player in roster:
            where[player.id] = team_id
    for matchup in snap.live_matchups or snap.matchups:
        for side in (matchup.home, matchup.away):
            for player in side.players:
                where[player.id] = side.team_id
    return where


def _diverging(rows: list[dict[str, Any]], key: str) -> None:
    """Bar geometry for a signed figure, in place: length as a share of the
    biggest in the league, and which side of zero."""
    span = max((abs(r[key]) for r in rows), default=0.0) or 1.0
    for row in rows:
        row["bar"] = round(min(100.0, abs(row[key]) / span * 100), 1)
        row["side"] = 1 if row[key] >= 0 else -1


# -- the look-ahead -----------------------------------------------------------

#: Designations that mean a starter will not play, and the label each gets.
HOLE_OUT = {"OUT": "OUT", "INJURY_RESERVE": "IR", "SUSPENSION": "SUSP"}
#: Designations that mean he might not.
HOLE_DOUBT = {"DOUBTFUL": "D", "QUESTIONABLE": "Q", "DAY_TO_DAY": "DTD"}
#: Draws per pre-game matchup. Fewer than the live figure's 2,000: nothing has
#: happened yet, the answer only moves when a lineup does, and ten simulations
#: a request at full size cost a third of a second.
LOOKAHEAD_DRAWS = 800
_AHEAD: "OrderedDict[tuple, float]" = OrderedDict()
_AHEAD_LOCK = threading.Lock()


def _hole(player, week: int, byes: dict[int, int]) -> tuple[str, str] | None:
    """What is wrong with a player next week, as (label, severity), or None.

    A bye beats an injury: a player on bye is out whatever his status says. A
    projection of zero with no designation is a hole too, because it is ESPN
    saying he will not play without saying why.
    """
    if byes.get(player.pro_team_id) == week:
        return "BYE", "out"
    if player.injury in HOLE_OUT:
        return HOLE_OUT[player.injury], "out"
    if player.projected <= 0:
        return "ZERO", "out"
    if player.injury in HOLE_DOUBT:
        return HOLE_DOUBT[player.injury], "doubt"
    return None


def _ahead_win(week: int, home_id: int, away_id: int, home_players, away_players) -> float:
    """Pre-game chance the home side wins, from the app's own simulator.

    Keyed on every starter's id, slot and projection, so a lineup change or a
    projection update misses and nothing else does.
    """
    key = (week, home_id, away_id,
           tuple((p.id, p.slot_id, round(p.projected, 2)) for p in home_players),
           tuple((p.id, p.slot_id, round(p.projected, 2)) for p in away_players))
    with _AHEAD_LOCK:
        if key in _AHEAD:
            _AHEAD.move_to_end(key)
            return _AHEAD[key]
    matchup = Matchup(id=0, matchup_period=week,
                      home=Side(team_id=home_id, players=list(home_players)),
                      away=Side(team_id=away_id, players=list(away_players)))
    value = win_probability(matchup, draws=LOOKAHEAD_DRAWS).home_win
    with _AHEAD_LOCK:
        _AHEAD[key] = value
        while len(_AHEAD) > 40:
            _AHEAD.popitem(last=False)
    return value


def _ahead_side(snap: LeagueSnapshot, team, roster, week: int):
    """One team's next week: its holes, the best fix for each, and the lineup
    as it would play with and without the fixes."""
    starters = [p for p in roster if p.is_starter]
    bench = [p for p in roster if not p.is_starter]
    marked = [(p, _hole(p, week, snap.byes)) for p in starters]

    # Fixes are handed out worst hole first, so a player ruled out gets the one
    # healthy backup before a questionable starter does.
    fixes: dict[int, Any] = {}
    used: set[int] = set()
    for severity in ("out", "doubt"):
        for player, hole in marked:
            if not hole or hole[1] != severity:
                continue
            now = 0.0 if severity == "out" else player.projected
            options = [c for c in bench
                       if c.id not in used and player.slot_id in c.eligible_slots
                       and _hole(c, week, snap.byes) is None and c.projected > now]
            best = max(options, key=lambda c: c.projected, default=None)
            if best is not None:
                used.add(best.id)
                fixes[player.id] = best

    as_is, fixed, holes, lineup = [], [], [], []
    for player, hole in marked:
        effective = 0.0 if hole and hole[1] == "out" else player.projected
        playing = replace(player, projected=effective) if effective != player.projected else player
        as_is.append(playing)
        fix = fixes.get(player.id)
        fixed.append(replace(fix, slot_id=player.slot_id) if fix else playing)
        lineup.append({"slot": player.slot, "player": player.name, "position": player.position,
                       "projected": round(effective, 1), "status": hole[0] if hole else ""})
        if hole:
            holes.append({
                "slot": player.slot, "player": player.name, "position": player.position,
                "label": hole[0], "severity": hole[1], "projected": round(effective, 1),
                "fix": ({"player": fix.name, "projected": round(fix.projected, 1),
                         "gain": round(fix.projected - effective, 1)} if fix else None),
            })

    projected = round(sum(p.projected for p in as_is), 1)
    fixed_total = round(sum(p.projected for p in fixed), 1)
    side = _team_row(team)
    side.update({
        "projected": projected, "fixed": fixed_total, "gain": round(fixed_total - projected, 1),
        "holes": holes, "lineup": lineup,
        "bench": [{"player": c.name, "position": c.position, "projected": round(c.projected, 1),
                   "status": (_hole(c, week, snap.byes) or ("", ""))[0], "fix": c.id in used}
                  for c in sorted(bench, key=lambda c: -c.projected)],
    })
    return side, as_is, fixed


def lookahead_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """Next week, before the lineups lock.

    Every other panel wakes up at kickoff. The mistakes that lose weeks are made
    on a Wednesday: a player ruled out left in a starting slot, a bye nobody
    noticed, a questionable starter with a healthy backup sitting on the bench.
    """
    week = snap.scoring_period + 1
    if snap.scoring_period != snap.settings.current_scoring_period:
        return {"available": False, "reason": "archive", "games": [], "rows": [], "week": week}
    pairings = [m for m in snap.season_schedule if m.matchup_period == week]
    if not snap.next_rosters or not pairings:
        return {"available": False, "reason": "none", "games": [], "rows": [], "week": week}

    games = []
    for pairing in pairings:
        home_team, away_team = snap.team(pairing.home.team_id), snap.team(pairing.away.team_id)
        home_roster = snap.next_rosters.get(pairing.home.team_id)
        away_roster = snap.next_rosters.get(pairing.away.team_id)
        if not (home_team and away_team and home_roster and away_roster):
            continue  # cold: every team in the schedule has a roster and a name
        home, home_now, home_fixed = _ahead_side(snap, home_team, home_roster, week)
        away, away_now, away_fixed = _ahead_side(snap, away_team, away_roster, week)
        win = _ahead_win(week, home_team.id, away_team.id, home_now, away_now)
        home.update(win=round(win * 100), tone=win_tone(win),
                    win_fixed=round(_ahead_win(week, home_team.id, away_team.id, home_fixed, away_now) * 100))
        away.update(win=100 - round(win * 100), tone=win_tone(1 - win),
                    win_fixed=round((1 - _ahead_win(week, home_team.id, away_team.id, home_now, away_fixed)) * 100))
        games.append({"home": home, "away": away})
    if not games:
        return {"available": False, "reason": "none", "games": [], "rows": [], "week": week}  # cold: see above

    sides = [s for g in games for s in (g["home"], g["away"])]
    return {
        "available": True, "reason": "", "week": week, "games": games, "rows": sides,
        "holes": sum(len(s["holes"]) for s in sides),
        "clean": sum(1 for s in sides if not s["holes"]),
        "fixable": round(sum(s["gain"] for s in sides), 1),
    }


def lookahead_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """One team's whole lineup for next week, and who they are playing."""
    view = lookahead_view(snap)
    for game in view["games"]:
        for mine, theirs in ((game["home"], game["away"]), (game["away"], game["home"])):
            if mine["id"] == team_id:
                return {"row": mine, "opponent": theirs, "week": view["week"]}
    return {}


# -- promise vs delivery ------------------------------------------------------

def trust_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """How much of ESPN's projection each team's starters actually delivered.

    Win probability, playoff odds and the form rating all start from ESPN's
    projection and nothing had ever checked it. Finished weeks only: a week in
    progress has a projection and half a score, and comparing them measures the
    clock rather than the projection.
    """
    tallies: dict[int, dict[str, Any]] = {}
    for week in sorted(snap.archive):
        for matchup in snap.archive[week]:
            for side in (matchup.home, matchup.away):
                starters = side.starters
                if not starters:
                    continue  # cold: a finished week's box score always has its lineups
                tally = tallies.setdefault(side.team_id, {"weeks": [], "positions": {}, "players": {}})
                projected = sum(p.projected for p in starters)
                actual = sum(p.points for p in starters)
                tally["weeks"].append({"week": week, "projected": round(projected, 1),
                                       "actual": round(actual, 1), "diff": round(actual - projected, 1)})
                for player in starters:
                    position = tally["positions"].setdefault(player.position, [0.0, 0.0])
                    position[0] += player.projected
                    position[1] += player.points
                    line = tally["players"].setdefault(player.id, {
                        "player": player.name, "position": player.position,
                        "projected": 0.0, "actual": 0.0, "starts": 0})
                    line["projected"] += player.projected
                    line["actual"] += player.points
                    line["starts"] += 1
    diffs = [w["diff"] for t in tallies.values() for w in t["weeks"]]
    if not diffs:
        return {"available": False, "rows": [], "positions": [], "weeks": 0}

    # How far apart two teams finish relative to their projections, measured
    # from this league rather than assumed: the spread of one team's miss,
    # times root two for two teams missing independently.
    spread = math.sqrt(2) * statistics.pstdev(diffs) if len(diffs) > 1 else 0.0
    positions = [p for p in FRONT_POSITIONS if any(p in t["positions"] for t in tallies.values())]
    rows = []
    for team in snap.teams:
        tally = tallies.get(team.id)
        if not tally:
            continue  # cold: every team played every settled week
        weeks = tally["weeks"]
        projected = sum(w["projected"] for w in weeks)
        actual = sum(w["actual"] for w in weeks)
        bias = (actual - projected) / len(weeks)
        # The win chance a bias of this size is worth in an otherwise even game.
        win_error = ((0.5 * (1 + math.erf(bias / (spread * math.sqrt(2)))) - 0.5) * 100
                     if spread > 0 else 0.0)
        cells = []
        for position in positions:
            pair = tally["positions"].get(position)
            pct = pair[1] / pair[0] * 100 if pair and pair[0] > 0 else None
            cells.append({"position": position,
                          "pct": None if pct is None else round(pct),
                          "diff": 0 if pct is None else round(pct - 100),
                          "weight": 0 if pct is None else min(100, round(abs(pct - 100) / 25 * 100))})
        players = [{**line, "projected": round(line["projected"], 1), "actual": round(line["actual"], 1),
                    "diff": round(line["actual"] - line["projected"], 1)}
                   for line in tally["players"].values()]
        row = _team_row(team)
        row.update({
            "delivered": round(actual / projected * 100, 1) if projected > 0 else 100.0,
            "beaten": sum(1 for w in weeks if w["diff"] > 0), "weeks": len(weeks),
            "bias": round(bias, 1), "miss": round(sum(abs(w["diff"]) for w in weeks) / len(weeks), 1),
            "win_error": round(win_error, 1), "cells": cells,
            "per_week": weeks, "players": players,
            "projected": round(projected / len(weeks), 1), "actual": round(actual / len(weeks), 1),
        })
        rows.append(row)
    rows.sort(key=lambda r: -r["delivered"])
    total_projected = sum(w["projected"] for t in tallies.values() for w in t["weeks"])
    total_actual = sum(w["actual"] for t in tallies.values() for w in t["weeks"])
    return {
        "available": True, "rows": rows, "positions": positions,
        "weeks": len(snap.archive), "spread": round(spread, 1),
        "league": round(total_actual / total_projected * 100, 1) if total_projected > 0 else 100.0,
    }


def trust_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """One team against its projections: week by week, position by position,
    and the starters ESPN has been most wrong about."""
    view = trust_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    players = sorted(row["players"], key=lambda p: p["diff"])
    return {
        "row": row, "positions": view["positions"], "league": view["league"],
        "spread": view["spread"],
        "over": [p for p in reversed(players) if p["diff"] > 0][:3],
        "under": [p for p in players if p["diff"] < 0][:3],
    }


# -- draft receipts -----------------------------------------------------------

#: Picks either side that a pick is measured against: about a round. The
#: league's own draft is the baseline, not a rankings site's idea of what a
#: fourth-round pick is worth, which would be a number nobody in the league
#: agreed to.
DRAFT_WINDOW = 10


def draft_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """Every pick against what the picks around it have scored."""
    if not snap.draft:
        return {"available": False, "reason": "draft", "rows": [], "picks": []}
    points = _season_points(snap)
    totals = [round(sum(points.get(p.player_id, {}).values()), 1) for p in snap.draft]
    if not any(totals):
        return {"available": False, "reason": "points", "rows": [], "picks": []}
    people, where = _people(snap), _rostered_by(snap)
    hues = {t.id: t.hue for t in snap.teams}

    picks = []
    for i, (pick, total) in enumerate(zip(snap.draft, totals)):
        neighbours = totals[max(0, i - DRAFT_WINDOW):i] + totals[i + 1:i + 1 + DRAFT_WINDOW]
        expected = sum(neighbours) / len(neighbours) if neighbours else total
        person = _person(people, pick.player_id)
        now = where.get(pick.player_id, 0)
        picks.append({
            "overall": pick.overall, "round": pick.round, "pick": pick.round_pick,
            "team_id": pick.team_id, "player": person.get("name", "Unknown player"),
            "position": person.get("position", ""), "keeper": pick.keeper,
            "points": total, "expected": round(expected, 1), "value": round(total - expected, 1),
            "status": "kept" if now == pick.team_id else "elsewhere" if now else "released",
        })

    rows = []
    for team in snap.teams:
        mine = [p for p in picks if p["team_id"] == team.id]
        if not mine:
            continue  # cold: every team drafts
        row = _team_row(team)
        row.update({
            "value": round(sum(p["value"] for p in mine), 1),
            "points": round(sum(p["points"] for p in mine), 1),
            "picks": len(mine), "kept": sum(1 for p in mine if p["status"] == "kept"),
            "released": sum(1 for p in mine if p["status"] == "released"),
            "steal": max(mine, key=lambda p: p["value"]), "bust": min(mine, key=lambda p: p["value"]),
        })
        rows.append(row)
    rows.sort(key=lambda r: -r["value"])
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    _diverging(rows, "value")

    # The plot, in percentages of its box so the template only places things.
    ceiling = max(totals) or 1.0
    last = max(p["overall"] for p in picks)
    x = lambda overall: round((overall - 1) / max(1, last - 1) * 100, 2)  # noqa: E731
    y = lambda value: round(100 - min(value, ceiling) / ceiling * 100, 2)  # noqa: E731
    dots = [{"x": x(p["overall"]), "y": y(p["points"]), "hue": hues.get(p["team_id"], 0),
             "label": f"{p['round']}.{p['pick']:02d} {p['player']}: {p['points']} pts, "
                      f"the picks around it {p['expected']}"} for p in picks]
    curve = " ".join(f"{x(p['overall'])},{y(p['expected'])}" for p in picks)
    weeks = sorted({w for p in snap.draft for w in points.get(p.player_id, {})})
    return {
        "available": True, "reason": "", "rows": rows, "picks": picks, "dots": dots,
        "curve": curve, "ceiling": round(ceiling, 1), "last": last, "weeks": len(weeks),
        "window": DRAFT_WINDOW,
        "best": max(picks, key=lambda p: p["value"]), "worst": min(picks, key=lambda p: p["value"]),
    }


def draft_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """One team's seventeen picks, in the order they were made."""
    view = draft_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    return {"row": row, "picks": [p for p in view["picks"] if p["team_id"] == team_id],
            "window": view["window"], "teams": len(view["rows"])}


# -- the move ledger ----------------------------------------------------------

def moves_view(snap: LeagueSnapshot) -> dict[str, Any]:
    """Every pickup, drop and trade, and whether it paid.

    Net is what the players a team brought in have scored since the move,
    minus what the players it let go have scored since, wherever they went and
    whoever started them. Counted from the week of the move, because a claim
    processed on a Wednesday plays that week.
    """
    if not snap.moves:
        return {"available": False, "rows": [], "ledger": []}
    points, people = _season_points(snap), _people(snap)
    names = {t.id: t.name for t in snap.teams}

    def line(player_id: int, week: int) -> dict[str, Any]:
        person = _person(people, player_id)
        since = sum(v for w, v in points.get(player_id, {}).items() if w >= week)
        return {"player": person.get("name", "Unknown player"),
                "position": person.get("position", ""), "points": round(since, 1)}

    ledger = []
    for move in snap.moves:
        added = [line(p, move.week) for p in move.added]
        dropped = [line(p, move.week) for p in move.dropped]
        ledger.append({
            "id": move.id, "week": move.week, "kind": move.kind, "team_id": move.team_id,
            "team": names.get(move.team_id, f"team {move.team_id}"),
            "added": added, "dropped": dropped,
            "net": round(sum(a["points"] for a in added) - sum(d["points"] for d in dropped), 1),
        })

    rows = []
    for team in snap.teams:
        mine = [m for m in ledger if m["team_id"] == team.id]
        adds = [a for m in mine for a in m["added"]]
        drops = [d for m in mine for d in m["dropped"]]
        row = _team_row(team)
        row.update({
            "moves": len(mine), "net": round(sum(m["net"] for m in mine), 1),
            "best_add": max(adds, key=lambda a: a["points"], default=None),
            "worst_drop": max(drops, key=lambda d: d["points"], default=None),
        })
        rows.append(row)
    rows.sort(key=lambda r: (-r["net"], -r["moves"]))
    _diverging(rows, "net")
    return {
        "available": True, "rows": rows, "ledger": ledger, "moves": len(ledger),
        "best": max(ledger, key=lambda m: m["net"]), "worst": min(ledger, key=lambda m: m["net"]),
    }


def moves_detail(snap: LeagueSnapshot, team_id: int) -> dict[str, Any]:
    """One team's moves, newest first."""
    view = moves_view(snap)
    row = _row_for(view, team_id)
    if row is None:
        return {}
    mine = [m for m in view["ledger"] if m["team_id"] == team_id]
    return {
        "row": row, "moves": list(reversed(mine)),
        "added": round(sum(a["points"] for m in mine for a in m["added"]), 1),
        "dropped": round(sum(d["points"] for m in mine for d in m["dropped"]), 1),
    }

