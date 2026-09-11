"""Phase 2 acceptance.

The build plan's gate is two things:

1. *Replaying a recorded Sunday emits a plausible Moment timeline.* Plausibility
   is a judgement, so it is pinned as a golden file: the timeline is regenerated
   on every run and compared byte for byte, and any change to detection has to be
   acknowledged deliberately by regenerating it.
2. *Bench regret reconciles by hand against the ESPN box score for two known
   weeks.* The half of this that can be done without a real league's credentials
   is done here, against an independent exact algorithm and against the figures
   planted in the fixture. The other half is noted at the bottom and in NEXT.md.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from config import DEMO_RECORDING
from engine.events import EventEngine
from engine.scoring import eligible_slots, optimal_lineup
from espn.cache import TTLCache
from espn.client import EspnClient, LeagueRepository
from espn.replay import ReplayTransport

GOLDEN = Path(__file__).parent / "golden" / "moments_demo-2025-11-16.json"

#: The cadence and draw count the golden file was generated at. Both change the
#: output, so both are pinned rather than defaulted.
POLL_SECONDS = 60
DRAWS = 800


def replay_timeline(poll: int = POLL_SECONDS, draws: int = DRAWS) -> list[dict]:
    """Every Moment of the demo Sunday, in order."""
    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    repo = LeagueRepository(client)
    engine = EventEngine(simulate_draws=draws)

    timeline: list[dict] = []
    for position in range(0, int(transport.recording.duration) + poll, poll):
        transport.clock.seek(position)
        client.cache.invalidate()
        for moment in engine.ingest(repo.snapshot()):
            payload = moment.to_json()
            # The wall-clock timestamp is when the test ran, not when the play
            # happened, so it is replaced by the replay position. Leaving it in
            # would make the golden file fail once a second, for ever.
            payload["ts"] = position
            timeline.append(payload)
    return timeline


def exact_best_by_dp(players, slots) -> float:
    """An independent exact solver, for checking the production one.

    Deliberately a different algorithm: the production code is a greedy matching
    justified by a matroid argument, and checking it against itself proves
    nothing. This walks the slots one at a time over bitmasks of used players,
    which is exact by exhaustion. It stays fast because only masks with exactly
    `i` bits set can reach slot `i`, so it visits about 26,000 states for a
    sixteen-player roster rather than the full 2^16 per slot.
    """
    ranked = [p for p in players if p.points > 0]
    allowed = [eligible_slots(p) for p in ranked]
    best: dict[int, float] = {0: 0.0}

    for slot in slots:
        nxt: dict[int, float] = {}
        for mask, score in best.items():
            # Leaving the seat empty is always legal and sometimes optimal.
            if nxt.get(mask, -1.0) < score:
                nxt[mask] = score
            for index, player in enumerate(ranked):
                bit = 1 << index
                if mask & bit or slot not in allowed[index]:
                    continue
                candidate = score + player.points
                if nxt.get(mask | bit, -1.0) < candidate:
                    nxt[mask | bit] = candidate
        best = nxt
    return round(max(best.values()), 2)


def snapshot_at(position: float):
    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    transport.clock.seek(position)
    client = EspnClient(transport=transport, season=2025, league_id="demo", cache=TTLCache())
    return LeagueRepository(client).snapshot()


# --------------------------------------------------------------------------
# 1. The Moment timeline
# --------------------------------------------------------------------------


def test_the_timeline_is_plausible(no_network):
    """Shape checks, before the byte-for-byte one.

    A golden file locks in whatever the code did, including nonsense, so these
    assert the properties that make a timeline plausible in the first place.
    """
    timeline = replay_timeline()
    kinds = [m["kind"] for m in timeline]

    assert 120 < len(timeline) < 500, f"{len(timeline)} moments in a day"
    assert len({m["id"] for m in timeline}) == len(timeline), "a moment fired twice"
    assert all(0.0 <= m["magnitude"] <= 1.0 for m in timeline)

    # Every kind the day should contain. A Sunday with no touchdowns, or with
    # nobody ever taking the lead, is not a plausible Sunday.
    for kind in ("TOUCHDOWN", "BIG_PLAY", "LEAD_CHANGE", "MILESTONE",
                 "BENCH_DISASTER", "DOOM", "CLINCH", "GOOSE_EGG"):
        assert kind in kinds, f"no {kind} in a whole Sunday"

    # Touchdowns are the common loud event and big plays are commoner still;
    # if DOOM outnumbers them something is firing on every poll.
    assert kinds.count("BIG_PLAY") > kinds.count("TOUCHDOWN") > kinds.count("DOOM")
    assert kinds.count("DOOM") <= 10, "DOOM is once per doomed manager, not once per poll"

    # Nothing may fire before the first snapshot has a baseline to diff against.
    assert min(m["ts"] for m in timeline) > 0

    # The planted storylines, by name. Moments carry TEAM names now, not the
    # managers': the league calls itself by its team names and the commentary
    # follows. Priya's team is Bench Mob Rule, which is the joke.
    bench = [m for m in timeline if m["kind"] == "BENCH_DISASTER" and m["teams"] == ["Bench Mob Rule"]]
    assert bench, "the planted bench disaster never fired"
    assert max(m["context"]["benched_points"] for m in bench) == 41.2
    assert any(m["kind"] == "DOOM" and m["teams"] == ["Certified Bottlers"] for m in timeline)
    assert any(m["kind"] == "GOOSE_EGG" and m["teams"] == ["Sunday Roast"] for m in timeline)


def test_the_timeline_is_byte_for_byte_what_it_was(no_network):
    """The golden file.

    Regenerate deliberately with:

        python3 -m tests.golden_regen

    and read the diff before committing it. A detection change that alters the
    afternoon is exactly the kind of thing that should require somebody to look.
    """
    if not GOLDEN.is_file():
        pytest.skip(f"{GOLDEN.name} has not been generated yet")

    expected = json.loads(GOLDEN.read_text("utf-8"))
    actual = replay_timeline()

    assert len(actual) == len(expected), (
        f"{len(actual)} moments now, {len(expected)} in the golden file"
    )
    for index, (got, want) in enumerate(zip(actual, expected)):
        assert got == want, f"moment {index} changed:\n  now:  {got}\n  was:  {want}"


# --------------------------------------------------------------------------
# 2. Bench regret
# --------------------------------------------------------------------------


def test_bench_regret_matches_an_independent_exact_solver(no_network):
    """Every team, every hour of the day, checked against a different algorithm."""
    for position in (3 * 3600, 6 * 3600, 39000):
        snapshot = snapshot_at(position)
        slots = snapshot.settings.starting_slots
        assert slots, "the league's lineup slots did not parse"

        for matchup in snapshot.matchups:
            for side in (matchup.home, matchup.away):
                produced = optimal_lineup(side.players, slots).total
                expected = exact_best_by_dp(side.players, slots)
                assert produced == expected, (
                    f"team {side.team_id} at {position}s: matching says {produced}, "
                    f"exhaustive DP says {expected}"
                )


def test_bench_regret_reconciles_with_the_planted_figures(no_network):
    """Hand reconciliation, against numbers chosen before the code existed.

    `tools/make_fixture.py` plants Priya (team 5) benching a receiver who
    finishes on 41.2 while she starts one who finishes on 1.4. Her optimal lineup
    works out, by hand, as three changes:

        Bram Marchbank   14.58  in for  Bram Ziegler      8.11  at RB    +6.47
        Wilder Braithwaite 41.20 in for Yusuf Marchbank   9.88  at WR   +31.32
        Sol Huddleston   18.33  in for  Ash Greenhalgh    1.40  at FLEX +16.93
                                                                        ------
                                                             105.89 -> 160.61

    Note that the 41.2 receiver does *not* go into the FLEX seat the 1.4 player
    vacated, which is what the first version of this test assumed. He is a
    receiver, the solver seats him at WR, and a running back takes the FLEX --
    which is worth more. The assumption was wrong and the solver was right.
    """
    snapshot = snapshot_at(39000)
    slots = snapshot.settings.starting_slots
    priya = next(t for t in snapshot.teams if t.manager == "Priya")
    side = snapshot.matchup_for(priya.id).side_for(priya.id)

    lineup = optimal_lineup(side.players, slots)
    assert lineup.actual == 105.89
    assert lineup.total == 160.61
    assert lineup.regret == 54.72

    started, benched = lineup.worst_swap
    assert benched.name == "Wilder Braithwaite" and benched.points == 41.2
    assert started.name == "Yusuf Marchbank" and started.slot == "WR"

    dropped = {p.name: p.points for p, _ in lineup.swaps}
    assert dropped["Ash Greenhalgh"] == 1.4, "the planted starter was not identified"

    # The regret is exactly the sum of the gains from the named swaps: the
    # headline number and the story behind it cannot disagree.
    gains = sum(b.points - s.points for s, b in lineup.swaps)
    assert round(gains, 2) == lineup.regret


def test_the_optimal_lineup_is_always_a_legal_lineup(no_network):
    """An optimal lineup that could not have been fielded is not a lineup."""
    snapshot = snapshot_at(39000)
    slots = snapshot.settings.starting_slots

    for matchup in snapshot.matchups:
        for side in (matchup.home, matchup.away):
            lineup = optimal_lineup(side.players, slots)
            seated = [seat.player.id for seat in lineup.seats if seat.player]
            assert len(seated) == len(set(seated)), "a player was seated twice"
            assert [seat.slot_id for seat in lineup.seats] == list(slots)
            for seat in lineup.seats:
                if seat.player:
                    assert seat.slot_id in eligible_slots(seat.player), (
                        f"{seat.player.name} ({seat.player.position}) cannot play {seat.slot}"
                    )


@pytest.mark.skip(
    reason="Needs LEAGUE_ID/ESPN_S2/ESPN_SWID for the real league. The other half of "
           "the Phase 2 gate: reconcile these figures against two known ESPN box "
           "scores by hand. Tracked in NEXT.md."
)
def test_bench_regret_reconciles_against_two_real_espn_weeks():
    raise NotImplementedError
