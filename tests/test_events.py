"""Event detection.

The properties that matter are idempotence and restraint. An app whose whole
point is making noise has to be trusted not to make it twice for the same play,
or at the wrong moment.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine.events import (
    BENCH_DISASTER,
    CLINCH,
    DOOM,
    GOOSE_EGG,
    LEAD_CHANGE,
    MILESTONE,
    TOUCHDOWN,
    EventEngine,
    Moment,
    moment_id,
)
from espn.models import LeagueSettings, LeagueSnapshot, Matchup, Player, Side, Team

QB, RB, WR, TE, FLEX, BENCH = 0, 2, 4, 6, 23, 20
SLOTS = {"0": 1, "2": 1, "4": 1, "6": 1, "23": 1, "20": 5}


def make_player(pid, points, position="RB", slot=RB, game_over=False, injury="ACTIVE", projected=12.0):
    return Player(id=pid, name=f"Player {pid}", slot_id=slot, position=position, pro_team="KC",
                  pro_team_id=12, points=points, projected=projected, injury=injury, game_over=game_over)


def make_snapshot(home_players, away_players, home_total=None, away_total=None, week=11):
    home = Side(team_id=1, total=home_total if home_total is not None else
                round(sum(p.points for p in home_players if p.is_starter), 2), players=home_players)
    away = Side(team_id=2, total=away_total if away_total is not None else
                round(sum(p.points for p in away_players if p.is_starter), 2), players=away_players)
    settings = LeagueSettings(lineup_slot_counts={int(k): v for k, v in SLOTS.items()},
                              current_matchup_period=week, current_scoring_period=week)
    return LeagueSnapshot(
        season=2025, scoring_period=week, settings=settings,
        teams=[Team(id=1, name="Home", abbrev="HOM", owners=["Bex"]),
               Team(id=2, name="Away", abbrev="AWY", owners=["Chidi"])],
        matchups=[Matchup(id=1, matchup_period=week, home=home, away=away)],
    )


def kinds(moments):
    return [m.kind for m in moments]


def test_the_first_snapshot_emits_nothing():
    """Joining a Sunday in progress must not fire the whole afternoon at once.

    Every player already has points and every matchup already has a leader, so a
    diff against an empty baseline would emit hundreds of moments through a bar's
    PA at four o'clock."""
    engine = EventEngine(simulate_draws=50)
    snapshot = make_snapshot([make_player(1, 42.0)], [make_player(2, 8.0)])
    assert engine.ingest(snapshot) == []


def test_a_touchdown_fires_once_and_only_once():
    engine = EventEngine(simulate_draws=50)
    engine.ingest(make_snapshot([make_player(1, 4.0)], [make_player(2, 8.0)]))

    after = make_snapshot([make_player(1, 11.2)], [make_player(2, 8.0)])
    assert TOUCHDOWN in kinds(engine.ingest(after))

    # The same state observed again is not a new play.
    assert TOUCHDOWN not in kinds(engine.ingest(after))


def test_the_same_play_hashes_the_same_in_a_fresh_process():
    """Dedupe has to survive a restart, or a crash at 4pm replays the afternoon.

    Ids are hashes of the play's own facts, never of the poll or the process that
    saw it, which is what makes this true."""
    assert moment_id(TOUCHDOWN, 11, 1234, 11.2) == moment_id(TOUCHDOWN, 11, 1234, 11.2)
    assert moment_id(TOUCHDOWN, 11, 1234, 11.2) != moment_id(TOUCHDOWN, 11, 1234, 17.4)


def test_a_restart_replays_nothing(tmp_path):
    seen = tmp_path / "seen.json"
    before = make_snapshot([make_player(1, 4.0)], [make_player(2, 8.0)])
    after = make_snapshot([make_player(1, 11.2)], [make_player(2, 8.0)])

    engine = EventEngine(simulate_draws=50, seen_path=seen)
    engine.ingest(before)
    assert TOUCHDOWN in kinds(engine.ingest(after))
    engine.persist()

    restarted = EventEngine(simulate_draws=50, seen_path=seen)
    restarted.ingest(before)
    assert TOUCHDOWN not in kinds(restarted.ingest(after)), "a restart replayed the afternoon"


def test_magnitude_scales_with_the_play():
    """A two-point reception and a sixty-yard touchdown must not get the same horn."""
    engine = EventEngine(simulate_draws=50)
    engine.ingest(make_snapshot([make_player(1, 0.0)], [make_player(2, 0.0)]))
    small = engine.ingest(make_snapshot([make_player(1, 3.0)], [make_player(2, 0.0)]))

    engine2 = EventEngine(simulate_draws=50)
    engine2.ingest(make_snapshot([make_player(1, 0.0)], [make_player(2, 0.0)]))
    big = engine2.ingest(make_snapshot([make_player(1, 12.4)], [make_player(2, 0.0)]))

    assert small[0].magnitude < big[0].magnitude
    assert 0.0 <= small[0].magnitude <= 1.0 and 0.0 <= big[0].magnitude <= 1.0


def test_a_goose_egg_waits_for_the_final_whistle():
    """Firing this on a scoreless first quarter would be wrong and, given what it
    sounds like, unkind."""
    engine = EventEngine(simulate_draws=50)
    scoreless = make_player(1, 0.0, slot=WR, position="WR", game_over=False)
    engine.ingest(make_snapshot([scoreless], [make_player(2, 8.0)]))

    still_playing = make_snapshot([make_player(1, 0.0, slot=WR, position="WR", game_over=False)],
                                  [make_player(2, 8.0)])
    assert GOOSE_EGG not in kinds(engine.ingest(still_playing))

    finished = make_snapshot([make_player(1, 0.0, slot=WR, position="WR", game_over=True)],
                             [make_player(2, 8.0)])
    assert GOOSE_EGG in kinds(engine.ingest(finished))


def test_bench_disaster_needs_a_real_margin():
    engine = EventEngine(simulate_draws=50, bench_margin=15.0)
    lineup = [make_player(1, 2.0, slot=FLEX), make_player(9, 10.0, slot=BENCH)]
    engine.ingest(make_snapshot(lineup, [make_player(2, 8.0)]))

    # Eight points behind: annoying, not announceable.
    modest = [make_player(1, 2.0, slot=FLEX), make_player(9, 10.0, slot=BENCH)]
    assert BENCH_DISASTER not in kinds(engine.ingest(make_snapshot(modest, [make_player(2, 8.0)])))

    disaster = [make_player(1, 2.0, slot=FLEX), make_player(9, 41.2, slot=BENCH)]
    moments = engine.ingest(make_snapshot(disaster, [make_player(2, 8.0)]))
    bench = [m for m in moments if m.kind == BENCH_DISASTER]
    assert bench and bench[0].context["benched_points"] == 41.2
    assert bench[0].context["started"] == "Player 1"


def test_doom_is_silent_once_the_games_are_over():
    """Being told you are doomed after the final whistle is not doom, it is the
    score, and the app already showed you that."""
    engine = EventEngine(simulate_draws=200)
    hopeless = [make_player(1, 10.0, game_over=True, projected=10.0)]
    winner_still_playing = [make_player(2, 120.0, game_over=False, projected=140.0)]
    engine.ingest(make_snapshot(hopeless, winner_still_playing))
    assert DOOM in kinds(engine.ingest(make_snapshot(hopeless, winner_still_playing)))

    engine2 = EventEngine(simulate_draws=200)
    winner_done = [make_player(2, 120.0, game_over=True, projected=140.0)]
    engine2.ingest(make_snapshot(hopeless, winner_done))
    assert DOOM not in kinds(engine2.ingest(make_snapshot(hopeless, winner_done)))


def test_a_lead_change_between_two_empty_scores_is_not_a_lead_change():
    """Six of these fired in the first eleven minutes of the demo Sunday, when
    nobody had scored twenty points between them."""
    engine = EventEngine(simulate_draws=50)
    engine.ingest(make_snapshot([make_player(1, 0.0)], [make_player(2, 1.8)]))
    early = engine.ingest(make_snapshot([make_player(1, 2.1)], [make_player(2, 1.8)]))
    assert LEAD_CHANGE not in kinds(early)

    engine2 = EventEngine(simulate_draws=50)
    engine2.ingest(make_snapshot([make_player(1, 60.0)], [make_player(2, 62.0)]))
    late = engine2.ingest(make_snapshot([make_player(1, 64.0)], [make_player(2, 62.0)]))
    assert LEAD_CHANGE in kinds(late)


def test_moments_arrive_loudest_first():
    """The audio bus takes them in order, so ordering is the priority queue."""
    engine = EventEngine(simulate_draws=50)
    engine.ingest(make_snapshot(
        [make_player(1, 0.0), make_player(2, 0.0, slot=WR, position="WR")],
        [make_player(3, 0.0)],
    ))
    moments = engine.ingest(make_snapshot(
        [make_player(1, 14.0), make_player(2, 3.0, slot=WR, position="WR")],
        [make_player(3, 0.0)],
    ))
    magnitudes = [m.magnitude for m in moments]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_a_moment_serialises_to_json_the_stream_can_send():
    moment = Moment(id="abc", kind=TOUCHDOWN, magnitude=0.8, managers=["Bex"],
                    team_ids=[1], player="Dax Ashgrove", delta_points=9.4,
                    ts=datetime(2025, 11, 16, 18, 30, tzinfo=timezone.utc))
    payload = moment.to_json()
    assert payload["kind"] == TOUCHDOWN
    assert payload["ts"].startswith("2025-11-16T18:30")
    import json

    json.dumps(payload)  # must not raise


def test_every_kind_of_play_can_be_the_biggest_swing_of_the_day():
    """`win_prob_delta` was set by three detectors out of nine and left at zero
    by the other six.

    Measured on the demo Sunday: 0 of 73 touchdowns carried one, so the Swing
    tab's "biggest swing of the day" could only ever be a lead change, a doom or
    a clinch -- 7 Moments out of 236. The biggest swing of a Sunday is almost
    always a touchdown.
    """
    import json
    from pathlib import Path

    golden = Path(__file__).parent / "golden" / "moments_demo-2025-11-16.json"
    timeline = json.loads(golden.read_text("utf-8"))

    with_delta = {m["kind"] for m in timeline if abs(m["win_prob_delta"]) > 0.0001}
    assert "TOUCHDOWN" in with_delta
    assert "BIG_PLAY" in with_delta

    swings = [m for m in timeline if abs(m["win_prob_delta"]) > 0.05]
    assert len(swings) > 15, f"only {len(swings)} Moments could ever appear on the Swing tab"
    biggest = max(timeline, key=lambda m: abs(m["win_prob_delta"]))
    assert biggest["kind"] in ("TOUCHDOWN", "BIG_PLAY", "LEAD_CHANGE")


def test_a_swing_is_credited_to_one_play_not_to_every_moment_in_the_poll():
    """One poll produces one net change per team, and several Moments land inside
    it. Giving all of them the same number puts three identical percentages at
    the top of the Swing tab and says nothing about which one did it.

    Observations are excluded entirely: a bench disaster and a goose egg describe
    a state rather than cause a change in it, and the scoring that moved the
    number is a separate Moment in the same poll.
    """
    import json
    from pathlib import Path

    golden = Path(__file__).parent / "golden" / "moments_demo-2025-11-16.json"
    timeline = json.loads(golden.read_text("utf-8"))

    for kind in ("BENCH_DISASTER", "GOOSE_EGG", "MILESTONE", "INJURY"):
        credited = [m for m in timeline if m["kind"] == kind and abs(m["win_prob_delta"]) > 0.0001]
        assert not credited, f"{kind} was credited with causing a swing"


def test_a_touchdown_never_lowers_its_own_team_s_chances():
    """It can, arithmetically: the opponent may have scored more in the same
    poll, leaving the team's probability down for the window.

    Crediting the touchdown with that produced a Swing tab reading "touchdown,
    -43%", which is true and obvious nonsense. The cause of a fall is something
    on the other side, and that side's own play picks it up as a positive in the
    same poll, so nothing is lost by refusing to attribute it here.
    """
    import json
    from pathlib import Path

    golden = Path(__file__).parent / "golden" / "moments_demo-2025-11-16.json"
    timeline = json.loads(golden.read_text("utf-8"))

    backwards = [
        f"{m['kind']} {m['player']} {m['win_prob_delta']:+.3f}"
        for m in timeline
        if m["kind"] in ("TOUCHDOWN", "BIG_PLAY") and m["win_prob_delta"] < 0
    ]
    assert not backwards, f"plays credited with hurting their own team: {backwards[:3]}"
