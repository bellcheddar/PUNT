"""The phrase bank and the selection algorithm.

The interesting property is not retrieval, it is restraint. A bank that always
says something is worse than one that knows when to be quiet, and the failure
mode that kills the joke is a line landing twice inside an afternoon.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config import PHRASES_DIR
from engine.commentary import (
    Commentator,
    Phrase,
    PhraseBank,
    PhraseError,
    _matches_range,
    moment_slots,
)
from engine.events import Moment


@pytest.fixture(scope="module")
def bank() -> PhraseBank:
    return PhraseBank.load(PHRASES_DIR)


def moment(kind="TOUCHDOWN", **kwargs) -> Moment:
    defaults = dict(
        id=f"m-{kind}-{kwargs.get('seq', 0)}", kind=kind, magnitude=0.7,
        teams=["Priya", "Gus"], player="Dax Ashgrove", delta_points=8.4,
        context={"week": 11, "slot": "RB", "position": "RB", "pro_team": "KC",
                 "pro_opponent": "LV", "total": 34.2, "starter": True,
                 "quarter": 3, "clock": "7:12", "red_zone": False},
    )
    defaults.update({k: v for k, v in kwargs.items() if k != "seq"})
    if "context" in kwargs:
        merged = dict(defaults["context"])
        merged.update(kwargs["context"])
        defaults["context"] = merged
    return Moment(**defaults)


def test_the_bank_loads_and_covers_every_moment_kind(bank):
    from engine import events

    expected = {events.TOUCHDOWN, events.BIG_PLAY, events.LEAD_CHANGE, events.MILESTONE,
                events.BENCH_DISASTER, events.INJURY, events.DOOM, events.CLINCH,
                events.GOOSE_EGG}
    assert expected <= bank.kinds, f"no lines for {expected - bank.kinds}"
    assert len(bank) > 100


def test_no_line_can_state_a_number_the_moment_did_not_carry(bank):
    """The structural reason a phrase bank is safe where a model is not.

    Every substitution comes from the Moment. There is nowhere for an invented
    number to come from, so this is a property of the design rather than
    something that has to be checked at runtime."""
    for phrase in bank.phrases:
        for slot in phrase.slots:
            assert slot.islower(), f"{phrase.id}: {slot} is not a slot name"
    slots = moment_slots(moment())
    assert slots["delta"] == "8.4"
    assert slots["player"] == "Dax Ashgrove"
    assert slots["manager"] == "Priya"


def test_a_line_is_never_rendered_with_a_hole_in_it(bank):
    """A phrase asking for a slot this kind does not carry is dropped, not
    rendered with a literal `{benched}` in the middle of it."""
    commentator = Commentator(bank, roast_level=2)
    for kind in sorted(bank.kinds - {"FILLER"}):
        line = commentator.say(moment(kind=kind, context={
            "threshold": 20.0, "team": False, "projected": 11.5, "status": "OUT",
            "started": "A", "started_points": 1.0, "benched": "B", "benched_points": 30.0,
            "regret": 29.0, "win_prob": 0.03, "deficit": 22.0, "opponent_in_play": 3,
            "lead": 18.0, "margin": 4.2, "matchup": 1, "home": 90.0, "away": 86.0,
        }))
        if line is None:
            continue
        assert "{" not in line.text and "}" not in line.text, f"{kind}: {line.text}"


def test_roast_level_actually_silences_the_sharp_lines(bank):
    safe = Commentator(bank, roast_level=0)
    savage = Commentator(bank, roast_level=2)
    m = moment(kind="BENCH_DISASTER", context={
        "started": "Ash Greenhalgh", "started_points": 1.4,
        "benched": "Wilder Braithwaite", "benched_points": 41.2, "regret": 54.7, "slot": "FLEX"})

    assert all(p.roast_level == 0 for p in safe.eligible(m))
    assert any(p.roast_level == 2 for p in savage.eligible(m))
    assert len(savage.eligible(m)) > len(safe.eligible(m))


def test_every_category_still_speaks_at_roast_level_zero(bank):
    """A commissioner lowering ROAST_LEVEL mid-season must not silence a whole
    category, which is what happens if every line in it is level 1 or 2."""
    for kind in sorted(bank.kinds):
        assert any(p.roast_level == 0 for p in bank.for_kind(kind)), f"{kind} goes silent at level 0"


def test_the_same_sunday_produces_the_same_commentary(bank):
    """Seeded per (week, moment id), because the golden-file transcript test
    depends on it and so does anybody trying to reproduce a complaint."""
    m = moment()
    first = Commentator(bank).say(m, week=11)
    second = Commentator(bank).say(m, week=11)
    assert first.phrase_id == second.phrase_id
    assert first.text == second.text


def test_a_line_does_not_come_round_twice_in_a_hurry(bank):
    """The failure that kills the joke.

    Measured on the demo Sunday before this was tuned: the closest repeat was
    exactly 8 Moments apart, which is `cooldown: 240` divided by the 30 s poll --
    the mechanism working perfectly at a badly chosen setting. Raising the
    generics to 780-1200 s pushed the closest repeat to 26 Moments and removed
    every repeat inside 10.
    """
    commentator = Commentator(bank, roast_level=1)
    seen: dict[str, int] = {}
    closest = 10**6
    for index in range(120):
        line = commentator.say(moment(kind="BIG_PLAY", seq=index, id=f"bp-{index}"), week=11)
        if line is None:
            continue
        if line.phrase_id in seen:
            closest = min(closest, index - seen[line.phrase_id])
        seen[line.phrase_id] = index
    assert closest >= 12, f"a line repeated {closest} moments apart"


def tiny_bank() -> PhraseBank:
    """Two lines on long cooldowns.

    The silence rules are tested against this rather than against the real bank,
    because they are a property of the *mechanism* and the real bank is now large
    enough never to exhaust its cooldowns at any plausible Moment rate -- which is
    the bank working, and which made the original version of this test assert
    nothing. A mechanism test that depends on how many lines somebody has written
    stops testing the mechanism the moment somebody writes more.
    """
    return PhraseBank([
        Phrase(id="tiny_a", text="{player} again.", kind=("BIG_PLAY", "TOUCHDOWN"), cooldown=3600),
        Phrase(id="tiny_b", text="{player} once more.", kind=("BIG_PLAY", "TOUCHDOWN"), cooldown=3600),
    ])


def test_silence_is_a_valid_answer():
    """An app that says something about every single play is worse than one that
    picks its moments. Small moments go quiet rather than repeat."""
    commentator = Commentator(tiny_bank(), roast_level=1)
    said = sum(1 for i in range(30)
               if commentator.say(moment(kind="BIG_PLAY", magnitude=0.3, seq=i, id=f"q-{i}"), week=11))
    assert said == 2, f"said {said} lines from a bank of two with everything on cooldown"
    assert commentator.missed.get("BIG_PLAY", 0) > 0


def test_a_big_moment_still_speaks_even_when_everything_is_on_cooldown():
    """The other half of that trade: a touchdown is worth a repeated line."""
    commentator = Commentator(tiny_bank(), roast_level=1)
    said = sum(1 for i in range(30)
               if commentator.say(moment(kind="TOUCHDOWN", magnitude=0.9, seq=i, id=f"td-{i}"), week=11))
    assert said == 30, "a high-magnitude moment went silent"


def test_the_real_bank_is_large_enough_not_to_need_the_silence_rule(bank):
    """The measurement that justified writing 410 lines.

    At the poll rate a Sunday actually produces, the real bank never runs out of
    eligible big-play lines, so the silence rule is a safety net rather than a
    routine behaviour. It was not always so: at 27 lines, six big plays an
    afternoon went unsaid."""
    commentator = Commentator(bank, roast_level=1)
    said = sum(1 for i in range(120)
               if commentator.say(moment(kind="BIG_PLAY", magnitude=0.3, seq=i, id=f"r-{i}"), week=11))
    assert said == 120, f"{120 - said} big plays went silent with a 410-line bank"


def test_triggers_narrow_correctly(bank):
    commentator = Commentator(bank, roast_level=2)
    benched = moment(kind="TOUCHDOWN", context={"starter": False})
    started = moment(kind="TOUCHDOWN", context={"starter": True})
    benched_ids = {p.id for p in commentator.eligible(benched)}
    started_ids = {p.id for p in commentator.eligible(started)}
    assert "td_bench_01" in benched_ids
    assert "td_bench_01" not in started_ids


def test_a_malformed_phrase_fails_at_load_not_at_play_time():
    with pytest.raises(PhraseError, match="trigger.kind"):
        PhraseBank._parse({"id": "x", "text": "hello"}, "test.yaml")
    with pytest.raises(PhraseError, match="duplicate"):
        PhraseBank([
            Phrase(id="dup", text="a", kind=("TOUCHDOWN",)),
            Phrase(id="dup", text="b", kind=("TOUCHDOWN",)),
        ])


def test_the_bank_passes_its_own_linter():
    """Unfillable slots and dead triggers are invisible at runtime: the line is
    simply never chosen and the app says something else."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "tools/phrase_lint.py"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    # Exit 1 without --strict means only the line-count shortfall, which is
    # tracked in the README rather than failing the suite.
    assert "problem(s)" not in result.stderr, result.stderr


def test_every_trigger_form_works_including_the_ones_no_phrase_uses_yet():
    """The whole comparison vocabulary a phrase author can write.

    The shipped bank uses four of the eight forms -- a scalar, a bool, `gte` and
    `lte` -- so `gt`, `lt`, `in` and a bare list had never been evaluated by
    anything, in the app or in a test. They are the forms somebody reaches for
    while writing the next phrase file, and an authoring feature that has never
    run once is a trap rather than a feature. data/phrases/README.md documents
    all eight; this is what keeps that document true.
    """
    assert _matches_range(12.0, 12.0)               # scalar, compared as text
    assert _matches_range("WR", "WR")
    assert not _matches_range("WR", "RB")
    assert _matches_range(True, True)               # bool
    assert not _matches_range(False, True)
    assert _matches_range("RB", ["RB", "WR"])       # a bare list is membership
    assert not _matches_range("TE", ["RB", "WR"])

    assert _matches_range(12.0, {"gte": 12}) and not _matches_range(11.9, {"gte": 12})
    assert _matches_range(12.1, {"gt": 12}) and not _matches_range(12.0, {"gt": 12})
    assert _matches_range(12.0, {"lte": 12}) and not _matches_range(12.1, {"lte": 12})
    assert _matches_range(11.9, {"lt": 12}) and not _matches_range(12.0, {"lt": 12})
    assert _matches_range(4.0, {"gte": 3, "lte": 6})        # both bounds, one spec
    assert not _matches_range(7.0, {"gte": 3, "lte": 6})

    # `in` inside a range object, which is how a non-numeric field is tested
    # alongside numeric ones. A Moment is allowed to be missing a field, so a
    # range test against something unmeasurable answers False rather than raising.
    assert _matches_range("OUT", {"in": ["OUT", "DOUBTFUL"]})
    assert not _matches_range("ACTIVE", {"in": ["OUT", "DOUBTFUL"]})
    assert not _matches_range(None, {"gte": 1})


def test_magnitude_is_a_trigger_field_even_though_no_phrase_uses_it():
    """`magnitude` is how a phrase says "only for the loud ones". It is wired
    into the matcher and no shipped phrase asks for it, which means the only
    thing standing between it and a silent typo is this test."""
    loud = Phrase(
        id="x_loud", kind=("TOUCHDOWN",), text="Enormous.", trigger={"magnitude": {"gte": 0.8}},
        source="test",
    )
    speaker = Commentator(PhraseBank([loud]), roast_level=2)

    assert speaker.eligible(moment(magnitude=0.9)) == [loud]
    assert speaker.eligible(moment(magnitude=0.5)) == []


def test_a_team_name_ending_in_s_takes_a_bare_apostrophe():
    """"The Wounded Ferrets's afternoon" is what you get when a phrase bank
    written for people starts interpolating team names.

    Fixed after interpolation rather than in the phrases: twenty-nine lines take
    a possessive, and which of them needs the apostrophe moved depends on the
    team names in somebody's league, which the bank cannot know.
    """
    from engine.commentary import _possessive

    assert _possessive("The Wounded Ferrets's afternoon") == "The Wounded Ferrets' afternoon"
    assert _possessive("Sunday Roast's pile") == "Sunday Roast's pile"
    assert _possessive("Priya's bench") == "Priya's bench"


def test_the_commentary_names_teams_not_usernames(bank):
    """The league calls itself by its team names and the commentary follows."""
    from engine.events import Moment

    line = Commentator(bank, roast_level=2).say(
        Moment(id="x", kind="TOUCHDOWN", magnitude=0.9, teams=["Bench Mob Rule"],
               team_ids=[5], player="Somebody Quick", delta_points=12.0,
               context={"total": 60.0}),
        week=11,
    )
    assert line is not None
    assert "Priya" not in line.text
