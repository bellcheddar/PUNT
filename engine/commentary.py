"""Retrieval-only commentary: a phrase bank, slot-filled, with no model in the path.

The latency budget is under 100 ms for a live play call, and the correctness
requirement is that a line can never invent a score. A language model fails both:
it is far too slow at the front of a Sunday afternoon, and it is perfectly
capable of announcing a touchdown that did not happen. A deterministic phrase
bank is not a compromise here, it is the right engineering answer -- instant,
free, and structurally incapable of hallucinating a number, because every number
in a line is substituted from the Moment that triggered it.

The interesting part is not retrieval, it is *restraint*: an afternoon is four
hours and ten managers, and a line that lands twice in twenty minutes stops being
funny the second time. Cooldowns, weights and roast levels are all in service of
that.
"""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: 0 safe, 1 teasing, 2 savage. A line is eligible only at or below the league's
#: configured level, so lowering it mid-season silences the sharp ones
#: immediately without editing anything.
ROAST_SAFE, ROAST_TEASING, ROAST_SAVAGE = 0, 1, 2

#: Voices. `pbp` is fast and higher energy for play calls, `colour` is slower and
#: drier for everything else.
VOICE_PBP, VOICE_COLOUR = "pbp", "colour"

_SLOT_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


class PhraseError(ValueError):
    """A phrase file is malformed. Raised at load time, never at play time."""


@dataclass(frozen=True)
class Phrase:
    """One line, and the conditions under which it may be said."""

    id: str
    text: str
    kind: tuple[str, ...]
    trigger: dict[str, Any] = field(default_factory=dict)
    tone: tuple[str, ...] = ()
    roast_level: int = 0
    weight: int = 1
    cooldown: int = 900
    audio: str = ""
    voice: str = VOICE_PBP
    source: str = ""

    @property
    def slots(self) -> frozenset[str]:
        return frozenset(_SLOT_RE.findall(self.text))


@dataclass
class Line:
    """A rendered line, ready for the feed, the TTS cache and the audio bus."""

    phrase_id: str
    text: str
    audio: str
    voice: str
    tone: tuple[str, ...]
    magnitude: float
    moment_id: str
    kind: str

    def to_json(self) -> dict[str, Any]:
        return {
            "phrase_id": self.phrase_id,
            "text": self.text,
            "audio": self.audio,
            "voice": self.voice,
            "tone": list(self.tone),
            "magnitude": round(self.magnitude, 3),
            "moment_id": self.moment_id,
            "kind": self.kind,
        }


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()  # cold: every shipped phrase names its tone and its kind
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    raise PhraseError(f"expected a string or a list, got {value!r}")  # cold: a phrase file with a number where a string belongs; tests/test_commentary.py


def _matches_range(value: Any, spec: Any) -> bool:
    """Compare one field against a scalar, a list, or a {gte, lte, in} object."""
    if isinstance(spec, dict):
        try:
            number = float(value)
        except (TypeError, ValueError):
            # A range test against something that is not a number never matches,
            # rather than raising: a Moment is allowed to be missing a field.
            return "in" in spec and value in spec["in"]  # cold: an `in` spec against a non-numeric field; no shipped phrase uses one
        for key, bound in spec.items():
            if key == "gte" and not number >= float(bound):
                return False
            if key == "gt" and not number > float(bound):
                return False  # cold: no shipped phrase uses `gt`; the form is tested, see data/phrases/README.md
            if key == "lte" and not number <= float(bound):
                return False
            if key == "lt" and not number < float(bound):
                return False  # cold: no shipped phrase uses `lt`; same
            if key == "in" and value not in bound:
                return False  # cold: no shipped phrase uses `in`; same
        return True
    if isinstance(spec, (list, tuple)):
        return value in spec  # cold: no shipped phrase uses a bare list; same
    if isinstance(spec, bool):
        return bool(value) is spec
    return str(value) == str(spec)


class PhraseBank:
    """Every line, indexed by the Moment kind it can respond to."""

    def __init__(self, phrases: Iterable[Phrase]) -> None:
        self.phrases: list[Phrase] = list(phrases)
        self._by_kind: dict[str, list[Phrase]] = {}
        seen: set[str] = set()
        for phrase in self.phrases:
            if phrase.id in seen:
                raise PhraseError(f"duplicate phrase id {phrase.id!r} ({phrase.source})")  # cold: the shipped bank has no duplicate id, which is what the linter is for
            seen.add(phrase.id)
            for kind in phrase.kind:
                self._by_kind.setdefault(kind, []).append(phrase)

    def __len__(self) -> int:
        return len(self.phrases)

    @property
    def kinds(self) -> set[str]:
        return set(self._by_kind)

    def for_kind(self, kind: str) -> list[Phrase]:
        return self._by_kind.get(kind, [])

    def counts(self) -> dict[str, int]:
        return {kind: len(items) for kind, items in sorted(self._by_kind.items())}

    @classmethod
    def load(cls, directory: Path) -> "PhraseBank":
        import yaml  # noqa: PLC0415 - only needed when a bank is actually loaded

        phrases: list[Phrase] = []
        for path in sorted(directory.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text("utf-8")) or []
            if not isinstance(raw, list):
                raise PhraseError(f"{path.name}: expected a list of phrases")  # cold: a phrase file that is not a list; tests/test_commentary.py
            for entry in raw:
                phrases.append(cls._parse(entry, path.name))
        if not phrases:
            log.warning("no phrases found in %s", directory)  # cold: an empty data/phrases; the bank ships with 410 lines in it
        return cls(phrases)

    @staticmethod
    def _parse(entry: Any, source: str) -> Phrase:
        if not isinstance(entry, dict):
            raise PhraseError(f"{source}: expected a mapping, got {type(entry).__name__}")  # cold: a phrase that is not a mapping; tests/test_commentary.py
        try:
            trigger = dict(entry.get("trigger") or {})
            kind = _as_tuple(trigger.pop("kind", None))
            if not kind:
                raise PhraseError(f"{source}: {entry.get('id')} has no trigger.kind")  # cold: a phrase with no trigger.kind; tests/test_commentary.py
            return Phrase(
                id=str(entry["id"]),
                text=str(entry["text"]),
                kind=kind,
                trigger=trigger,
                tone=_as_tuple(entry.get("tone")),
                roast_level=int(entry.get("roast_level", 0)),
                weight=max(1, int(entry.get("weight", 1))),
                cooldown=int(entry.get("cooldown", 900)),
                audio=str(entry.get("audio", "")),
                voice=str(entry.get("voice", VOICE_PBP)),
                source=source,
            )
        except KeyError as exc:
            raise PhraseError(f"{source}: phrase is missing {exc}") from exc  # cold: a phrase missing a required field; tests/test_commentary.py


def moment_slots(moment) -> dict[str, Any]:
    """Everything a line is allowed to interpolate.

    Only values that came out of the Moment, which is what makes it impossible
    for a line to state a number that did not happen: there is nowhere for an
    invented one to come from.
    """
    context = dict(moment.context or {})
    managers = list(moment.managers or [])
    slots: dict[str, Any] = {
        "player": moment.player or "somebody",
        "manager": managers[0] if managers else "somebody",
        "opponent": managers[1] if len(managers) > 1 else "the other one",
        "delta": f"{abs(moment.delta_points):.1f}",
        "kind": moment.kind.lower().replace("_", " "),
    }
    for key, value in context.items():
        if isinstance(value, float):
            slots[key] = f"{value:.1f}"
        elif isinstance(value, bool):
            slots[key] = value
        else:
            slots[key] = value
    if "win_prob" in context:
        try:
            slots["win_pct"] = f"{float(context['win_prob']) * 100:.0f}"
        except (TypeError, ValueError):
            pass  # cold: a win_prob that is not a number
    return slots


class Commentator:
    """Picks a line for a Moment, and remembers what it has already said."""

    def __init__(self, bank: PhraseBank, roast_level: int = ROAST_TEASING) -> None:
        self.bank = bank
        self.roast_level = roast_level
        #: phrase id -> the sequence number at which it was last used. Measured in
        #: Moments rather than seconds so a replay at 60x behaves like a Sunday at
        #: 1x: a cooldown in wall-clock seconds would let every line repeat four
        #: times an hour in a test and never repeat at all in the bar.
        self._last_used: dict[str, int] = {}
        self._clock = 0
        self.said = 0
        self.missed: dict[str, int] = {}

    def eligible(self, moment) -> list[Phrase]:
        slots = moment_slots(moment)
        out: list[Phrase] = []
        for phrase in self.bank.for_kind(moment.kind):
            if phrase.roast_level > self.roast_level:
                continue
            if not self._trigger_matches(phrase, moment, slots):
                continue
            if not phrase.slots <= set(slots):
                # A line whose slots cannot be filled is dropped rather than
                # rendered with a hole in it. `tools/phrase_lint.py` catches this
                # at authoring time; this is the belt to that pair of braces.
                log.debug("phrase %s wants %s, moment has none", phrase.id, phrase.slots - set(slots))  # cold: a line whose slots cannot be filled; phrase_lint catches these at authoring time
                continue  # cold: same
            out.append(phrase)
        return out

    def _trigger_matches(self, phrase: Phrase, moment, slots: dict[str, Any]) -> bool:
        for key, spec in phrase.trigger.items():
            if key == "magnitude":
                if not _matches_range(moment.magnitude, spec):  # cold: `magnitude` is a supported trigger field no shipped phrase asks for
                    return False  # cold: same
            elif key == "delta_points":
                if not _matches_range(moment.delta_points, spec):
                    return False
            elif key == "win_prob_delta":
                if not _matches_range(moment.win_prob_delta, spec):
                    return False
            else:
                if key not in slots or not _matches_range(slots[key], spec):
                    return False
        return True

    def say(self, moment, week: int = 0) -> Line | None:
        """One line for this Moment, or nothing if there is nothing good to say.

        Silence is a valid answer. An app that says something about every single
        play is worse than one that picks its moments, and the alternative to
        silence here would be repeating a line inside its cooldown.
        """
        self._clock += 1
        candidates = [p for p in self.eligible(moment) if self._off_cooldown(p)]
        if not candidates:
            # Everything eligible is on cooldown: fall back to the eligible set
            # and take the one used longest ago, which is still better than
            # nothing for a big moment and silent for a small one.
            fallback = self.eligible(moment)
            if not fallback or moment.magnitude < 0.6:
                self.missed[moment.kind] = self.missed.get(moment.kind, 0) + 1
                return None
            candidates = sorted(fallback, key=lambda p: self._last_used.get(p.id, -10**6))[:1]  # cold: 410 lines is enough that nothing is ever entirely on cooldown; that is the measurement in test_the_real_bank_is_large_enough_not_to_need_the_silence_rule

        # Seeded per (week, moment id) so the same Sunday replays identically:
        # the golden-file commentary test depends on it, and so does anyone
        # trying to reproduce a complaint about a line.
        rng = random.Random(f"{week}|{moment.id}")
        chosen = rng.choices(candidates, weights=[p.weight for p in candidates], k=1)[0]

        self._last_used[chosen.id] = self._clock
        self.said += 1
        return Line(
            phrase_id=chosen.id,
            text=chosen.text.format(**{k: v for k, v in moment_slots(moment).items()}),
            audio=chosen.audio,
            voice=chosen.voice,
            tone=chosen.tone,
            magnitude=moment.magnitude,
            moment_id=moment.id,
            kind=moment.kind,
        )

    def _off_cooldown(self, phrase: Phrase) -> bool:
        last = self._last_used.get(phrase.id)
        if last is None:
            return True
        # `cooldown` is authored in seconds because that is how a person thinks
        # about "not again for fifteen minutes"; converted here at the app's poll
        # rate, which is the rate Moments actually arrive at.
        return (self._clock - last) >= max(1, phrase.cooldown // 30)
