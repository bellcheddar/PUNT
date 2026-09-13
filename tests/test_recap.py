"""The weekly recap, and the validator that makes it safe to run unattended.

This is the only place in PUNT where prose is generated rather than slot-filled,
so it is the only place a number could be invented. The spec's reasoning is the
whole reason these tests exist: *a recap that invents a score is worse than no
recap, because the league will believe it.*
"""

from __future__ import annotations

import pytest

from config import DEMO_RECORDING
from engine.events import EventEngine
from engine.factpack import FactPack, build
from engine.recap import (
    ALLOWED_CAPITALS,
    Recap,
    generate,
    sentence_starts,
    templated,
    validate,
)
from espn.cache import TTLCache
from espn.client import EspnClient, LeagueRepository
from espn.replay import ReplayTransport


@pytest.fixture(scope="module")
def week() -> FactPack:
    transport = ReplayTransport.load(DEMO_RECORDING, speed=0.0)
    client = EspnClient(transport, 2025, "demo", TTLCache())
    repo = LeagueRepository(client)
    engine = EventEngine(simulate_draws=40)
    moments = []
    snapshot = None
    for position in range(0, int(transport.recording.duration) + 300, 300):
        transport.clock.seek(position)
        client.cache.invalidate()
        snapshot = repo.snapshot()
        moments.extend(engine.ingest(snapshot))
    return build(snapshot, moments)


# --------------------------------------------------------------------------
# the fact pack
# --------------------------------------------------------------------------

def test_the_fact_pack_describes_a_whole_week(week):
    assert week.week == 11
    assert len(week.teams) == 10
    assert len(week.matchups) == 5
    assert week.highlights["highest"]["score"] > week.highlights["lowest"]["score"]
    assert week.counts.get("touchdown", 0) > 0


def test_the_sayable_sets_are_collected_by_walking_not_by_listing(week):
    """A fact added later must become sayable automatically. A hand-kept list
    drifts, and it drifts towards rejecting true sentences, which is the failure
    that makes people turn a validator off."""
    score = week.highlights["highest"]["score"]
    numbers = week.numbers()
    assert f"{score:g}" in numbers and f"{score:.1f}" in numbers

    names = week.names()
    assert week.highlights["highest"]["team"] in names
    assert week.league in names
    # Surnames too: a recap writing "Braithwaite" where the pack says "Wilder
    # Braithwaite" is not inventing anybody.
    player = week.highlights["best_player"]["player"]
    assert all(part in names for part in player.split())


def test_rounded_figures_are_sayable(week):
    """"112 points" about a 112.43 score is correct and is the sentence a person
    would write. Rejecting it would make the validator an enemy."""
    score = week.teams[0]["score"]
    numbers = week.numbers()
    assert f"{score:.1f}" in numbers
    assert f"{score:.0f}" in numbers


# --------------------------------------------------------------------------
# the validator
# --------------------------------------------------------------------------

def test_the_templated_recap_passes_its_own_validator(week):
    """Correct by construction: assembled from the pack rather than written about
    it, which is what makes it a safe floor."""
    text = templated(week)
    assert validate(text, week) == []
    assert len(text) > 120


def test_an_invented_score_is_rejected(week):
    rejections = validate("Priya put up 999.9 and nobody could believe it.", week)
    assert any(r.kind == "number" and "999.9" in r.token for r in rejections)


def test_an_invented_player_is_rejected(week):
    rejections = validate("Kevin Bacon had a huge day for Priya.", week)
    assert any(r.kind == "name" for r in rejections)


def test_a_true_sentence_is_accepted(week):
    best = week.highlights["best_player"]
    text = f"{best['player']} finished on {best['points']:.1f} for {best['team']}."
    assert validate(text, week) == [], validate(text, week)


def test_the_validator_actually_reads_the_pack(week):
    """The spec's grounding test: corrupt the fact pack and the same prose must
    stop validating.

    Without this, a validator that rubber-stamped everything would pass every
    other test in this file.
    """
    text = templated(week)
    assert validate(text, week) == []

    corrupted = FactPack(
        season=week.season, week=week.week, league=week.league,
        teams=[{**t, "score": t["score"] + 1000} for t in week.teams],
        matchups=[], highlights={}, counts={},
    )
    rejections = validate(text, corrupted)
    assert rejections, "the same prose validated against a pack that contradicts it"
    assert any(r.kind == "number" for r in rejections)


def test_a_sentence_opening_capital_is_not_treated_as_a_name(week):
    """"Bench regret was brutal" is not a claim about somebody called Bench."""
    assert validate("Bench regret was brutal this week.", week) == []
    assert validate("Nobody scored much.", week) == []


def test_two_capitals_in_a_row_at_a_sentence_start_are_checked(week):
    """The narrowing of the validator's one blind spot. A single invented
    forename opening a sentence still gets through; an invented full name does
    not, and a model inventing a person almost always gives it a surname."""
    assert validate("Kevin Bacon scored.", week)
    assert validate("Kevin scored.", week) == []   # the documented hole


def test_sentence_starts_are_found_after_every_terminator():
    text = "One. Two! Three? Four"
    starts = sentence_starts(text)
    assert len(starts) == 4


def test_allowed_capitals_stays_short():
    """Every entry is a hole in the validator, so the list is a liability that
    has to be argued for rather than a convenience to be grown."""
    assert len(ALLOWED_CAPITALS) < 60
    assert "Priya" not in ALLOWED_CAPITALS


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------

def test_with_no_backend_the_recap_is_the_templated_one(week):
    recap = generate(week, backend=None)
    assert recap.source == "template"
    assert recap.attempts == 0
    assert validate(recap.text, week) == []


def test_a_lying_model_never_reaches_the_user(week):
    """Three attempts, then the templated fallback. The user sees a correct
    recap either way and never sees the invented one."""

    class Liar:
        name = "liar"
        calls = 0

        def available(self):
            return True

        def generate(self, prompt, temperature=0.85, max_tokens=200):
            Liar.calls += 1
            return "Priya scored 9999.9 points, a league record."

    recap = generate(week, backend=Liar())
    assert recap.source == "template"
    assert Liar.calls == 3, "gave up before three attempts"
    assert recap.rejections, "rejections were not recorded for prompt tuning"
    assert "9999.9" not in recap.text
    assert validate(recap.text, week) == []


def test_a_truthful_model_is_used(week):
    best = week.highlights["best_player"]

    class Honest:
        name = "honest"

        def available(self):
            return True

        def generate(self, prompt, temperature=0.85, max_tokens=200):
            return (f"{best['player']} finished on {best['points']:.1f} for "
                    f"{best['team']}. It was that kind of week.")

    recap = generate(week, backend=Honest())
    assert recap.source == "model"
    assert recap.attempts == 1
    assert recap.rejections == []


def test_a_broken_backend_falls_back_rather_than_raising(week):
    class Broken:
        name = "broken"

        def available(self):
            return True

        def generate(self, prompt, temperature=0.85, max_tokens=200):
            raise RuntimeError("connection refused")

    recap = generate(week, backend=Broken())
    assert recap.source == "template"
    assert validate(recap.text, week) == []


def test_the_prompt_carries_the_facts(week):
    from engine.recap import build_prompt

    prompt = build_prompt(week)
    assert week.highlights["highest"]["team"] in prompt
    assert "Do not invent" in prompt
    # The prompt has to forbid two different things: naming a real person, and
    # mocking a real athlete. The first is a privacy rule and the second is a
    # taste one, and a model given only the second will happily name people.
    assert "Never name a person" in prompt
    assert "never mock the real athletes" in prompt.lower()


def test_a_recap_serialises(week):
    payload = generate(week, backend=None).to_json()
    import json

    json.dumps(payload)
    assert payload["source"] == "template"
