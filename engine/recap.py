"""The weekly recap: generated prose, and the validator that makes it safe.

This is the only place in PUNT where a sentence is written rather than
slot-filled, and therefore the only place a number could be invented. The build
spec is unambiguous about why that matters: *a recap that invents a score is
worse than no recap, because the league will believe it.*

So the model is never trusted. Every numeral and every proper noun in the output
must appear in the fact pack, or the whole recap is thrown away and regenerated;
after three attempts it falls back to a fully templated version, which is
guaranteed correct because it is assembled from the fact pack rather than written
about it. Every rejection is logged so the prompt can be tuned against real
failures rather than imagined ones.

The templated fallback is not a degraded mode to be embarrassed about. It ships
today, reads perfectly well, and means the feature works with no model installed
at all -- which is the state of this machine, and of the droplet until Piper and
a small instruct model are put on it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from engine.factpack import FactPack

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3

#: Words a recap may capitalise without them being names. Deliberately short:
#: every addition is a hole in the validator, so the bar is "a person writing
#: about fantasy football would capitalise this and it is not a name".
ALLOWED_CAPITALS = frozenset({
    "A", "An", "And", "As", "At", "But", "For", "From", "He", "His", "I", "If",
    "In", "It", "Its", "No", "Not", "Of", "On", "Or", "She", "So", "That", "The",
    "Their", "Then", "There", "They", "This", "To", "We", "What", "When", "While",
    "Who", "With", "You", "Your",
    "Sunday", "Monday", "Saturday", "Week", "NFL", "TD", "QB", "RB", "WR", "TE",
    "FLEX", "IR", "OK", "MVP",
})

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORD_RE = re.compile(r"\b[A-Z][a-zA-Z'’\-]*\b")
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?]\s+|[\n])\s*")


@dataclass
class Rejection:
    """Why a candidate recap was thrown away."""

    kind: str       # "number" | "name"
    token: str
    context: str

    def __str__(self) -> str:
        return f"{self.kind} {self.token!r} is not in the fact pack ({self.context})"


@dataclass
class Recap:
    text: str
    source: str                                   # "model" | "template"
    attempts: int = 1
    rejections: list[Rejection] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source": self.source,
            "attempts": self.attempts,
            "rejections": [str(r) for r in self.rejections],
        }


class Backend(Protocol):
    """Anything that can turn a prompt into prose."""

    name: str

    def available(self) -> bool: ...
    def generate(self, prompt: str, temperature: float = 0.85, max_tokens: int = 200) -> str: ...


# --------------------------------------------------------------------------
# the validator
# --------------------------------------------------------------------------

def sentence_starts(text: str) -> set[int]:
    """Character offsets where a sentence begins.

    Needed because a capitalised word at the start of a sentence carries no
    information about whether it is a name, and treating "Bench regret was
    brutal" as a claim about somebody called Bench would reject correct prose.
    """
    return {match.end() for match in _SENTENCE_START_RE.finditer(text)}


def validate(text: str, pack: FactPack) -> list[Rejection]:
    """Every numeral and proper noun in `text`, checked against the pack."""
    rejections: list[Rejection] = []
    allowed_numbers = pack.numbers()
    allowed_names = pack.names()
    starts = sentence_starts(text)

    for match in _NUMBER_RE.finditer(text):
        raw = match.group()
        # "1,204" and "1204" are the same claim.
        candidates = {raw, raw.replace(",", "")}
        if raw.replace(",", "").rstrip("0").rstrip(".") != raw.replace(",", ""):
            candidates.add(raw.replace(",", "").rstrip("0").rstrip("."))
        if not candidates & allowed_numbers:
            rejections.append(Rejection("number", raw, _snippet(text, match.start())))

    words = list(_WORD_RE.finditer(text))
    for index, match in enumerate(words):
        word = match.group()
        if word in ALLOWED_CAPITALS:
            continue
        if match.start() in starts and not _looks_like_a_full_name(text, match, words, index):
            # A capitalised word at the start of a sentence carries no
            # information about whether it is a name: "Bench regret was brutal"
            # is not a claim about somebody called Bench. This is the validator's
            # one real blind spot -- a single invented forename opening a
            # sentence gets through -- narrowed by the check above, which catches
            # the far commoner case of an invented *full* name.
            continue
        if word.rstrip("'’s") in allowed_names or word in allowed_names:
            continue
        rejections.append(Rejection("name", word, _snippet(text, match.start())))

    return rejections


def _looks_like_a_full_name(text: str, match, words: list, index: int) -> bool:
    """Whether a sentence-initial capital is the first half of a full name.

    "Kevin Bacon had a huge day" opens with a capital that the sentence-start
    exemption would wave through, and the surname alone would be caught -- but
    only because there is one. Two capitalised words in a row at a sentence
    start is a name far more often than it is prose, so both halves get checked.
    """
    if index + 1 >= len(words):
        return False
    following = words[index + 1]
    between = text[match.end() : following.start()]
    return between.strip() == "" and following.group() not in ALLOWED_CAPITALS


def _snippet(text: str, position: int, width: int = 32) -> str:
    start = max(0, position - width // 2)
    return "…" + text[start : start + width].replace("\n", " ").strip() + "…"


# --------------------------------------------------------------------------
# the templated fallback
# --------------------------------------------------------------------------

def templated(pack: FactPack) -> str:
    """A recap assembled from the fact pack rather than written about it.

    Correct by construction: every number and name in it came out of the pack, so
    it cannot fail its own validator. That is what makes it a safe floor.
    """
    highlights = pack.highlights
    lines: list[str] = []

    high = highlights.get("highest")
    low = highlights.get("lowest")
    if high and low:
        lines.append(
            f"Week {pack.week}. {high['team']} put up {high['score']:.1f}, the most "
            f"anybody managed. {low['team']} managed {low['score']:.1f}, which was the least."
        )

    closest = highlights.get("closest")
    if closest:
        lines.append(
            f"The closest one went to {closest['winner']} by {closest['margin']:.1f} "
            f"over {closest['loser']}."
        )

    blowout = highlights.get("blowout")
    if blowout and closest and blowout["margin"] > closest["margin"]:
        lines.append(
            f"The least close went to {blowout['winner']} by {blowout['margin']:.1f}."
        )

    bench = highlights.get("bench_disaster")
    if bench and bench.get("benched"):
        lines.append(
            f"{bench['team']} left {bench['regret']:.1f} on the bench: "
            f"{bench['benched']} finished on {bench['benched_points']:.1f} while "
            f"{bench['started']} started in the {bench['slot']} and managed "
            f"{bench['started_points']:.1f}."
        )

    best = highlights.get("best_player")
    if best:
        lines.append(
            f"The best single performance was {best['player']} with "
            f"{best['points']:.1f} for {best['team']}."
        )

    geese = highlights.get("goose_eggs") or []
    if geese:
        names = ", ".join(f"{g['player']} for {g['team']}" for g in geese[:3])
        lines.append(f"Nobody at all from {names}.")

    doomed = highlights.get("doomed") or []
    if len(doomed) > 1:
        lines.append(f"At some point {', '.join(doomed[:-1])} and {doomed[-1]} were all mathematically finished.")

    touchdowns = pack.counts.get("touchdown")
    if touchdowns:
        lines.append(f"{touchdowns} touchdowns across the day.")

    return " ".join(lines) if lines else f"Week {pack.week} happened."


# --------------------------------------------------------------------------
# the prompt
# --------------------------------------------------------------------------

PROMPT = """You are writing a short, dry, funny recap of one week of a ten-team \
fantasy football league for the people in it. They are all in the same bar.

Rules, which matter more than the writing:
- Use ONLY the numbers and names in the facts below. Do not calculate anything.
- Do not invent a score, a name, a player or a statistic. If it is not in the \
facts, it did not happen.
- Tease the TEAMS for their decisions, by team name. Never name a person, and
  never mock the real athletes.
- Three or four sentences. No headings, no lists, no preamble.

Facts:
{facts}

Recap:"""


def build_prompt(pack: FactPack) -> str:
    return PROMPT.format(facts=pack.dumps(indent=2))


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------

def generate(pack: FactPack, backend: Backend | None = None,
             attempts: int = MAX_ATTEMPTS, temperature: float = 0.85) -> Recap:
    """A validated recap, or the templated one.

    Regeneration is not a retry loop around a flaky API: each attempt is a fresh
    sample from a model that produced something untrue, and after three the
    honest conclusion is that this week's facts are not ones it can write about
    safely.
    """
    if backend is None or not backend.available():
        return Recap(text=templated(pack), source="template", attempts=0)

    all_rejections: list[Rejection] = []
    for attempt in range(1, attempts + 1):
        try:
            candidate = backend.generate(build_prompt(pack), temperature=temperature).strip()
        except Exception as exc:  # noqa: BLE001 - a model failing is not an app failing
            log.warning("recap backend %s failed: %s", backend.name, exc)
            break

        rejections = validate(candidate, pack)
        if not rejections:
            return Recap(text=candidate, source="model", attempts=attempt,
                         rejections=all_rejections)

        all_rejections.extend(rejections)
        # Logged individually and loudly. The point of recording these is that the
        # prompt gets tuned against real failures rather than imagined ones.
        for rejection in rejections:
            log.info("recap attempt %d rejected: %s", attempt, rejection)

    return Recap(text=templated(pack), source="template",
                 attempts=attempts, rejections=all_rejections)


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------

class OllamaBackend:
    """Ollama's HTTP API. Present on this machine; no model pulled yet."""

    name = "ollama"

    def __init__(self, model: str = "qwen2.5:1.5b-instruct", host: str = "http://127.0.0.1:11434") -> None:
        self.model = model
        self.host = host

    def available(self) -> bool:
        try:
            import urllib.request  # noqa: PLC0415

            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=1.5) as response:
                import json  # noqa: PLC0415

                names = {m.get("name", "") for m in json.load(response).get("models", [])}
            return any(name.startswith(self.model.split(":")[0]) for name in names)
        except Exception:  # noqa: BLE001
            return False  # cold: ollama is running on this machine, so the connection does not fail; it just has no matching model

    def generate(self, prompt: str, temperature: float = 0.85, max_tokens: int = 200) -> str:
        import json  # noqa: PLC0415  # cold: OllamaBackend.generate: needs the model pulled (ollama pull qwen2.5:1.5b-instruct)
        import urllib.request  # noqa: PLC0415  # cold: same

        body = json.dumps({  # cold: same
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }).encode()
        request = urllib.request.Request(  # cold: same
            f"{self.host}/api/generate", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=45) as response:  # cold: same
            return json.load(response).get("response", "")  # cold: same


class MLXBackend:
    """mlx-lm on Apple silicon. The spec's first suggestion."""

    name = "mlx"

    def __init__(self, model: str = "mlx-community/Qwen2.5-1.5B-Instruct-4bit") -> None:
        self.model = model
        self._loaded = None

    def available(self) -> bool:
        try:
            import mlx_lm  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True  # cold: mlx_lm is not installed here

    def generate(self, prompt: str, temperature: float = 0.85, max_tokens: int = 200) -> str:
        from mlx_lm import generate as mlx_generate, load  # noqa: PLC0415  # cold: MLXBackend.generate: needs mlx_lm and the weights

        if self._loaded is None:  # cold: same
            self._loaded = load(self.model)  # cold: same
        model, tokenizer = self._loaded  # cold: same
        return mlx_generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)  # cold: same


def pick_backend(cfg) -> Backend | None:
    """Whichever backend the configuration asks for, if it is actually there."""
    candidates = {"mlx": MLXBackend, "llamacpp": OllamaBackend, "ollama": OllamaBackend}
    factory = candidates.get(getattr(cfg, "recap_backend", "mlx"))
    if factory is None:
        return None
    backend = factory()
    return backend if backend.available() else None
