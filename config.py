"""Environment-driven configuration.

Every knob in the app is read here and nowhere else, which is what makes the
startup secret audit in `assert_no_secrets_in_static` meaningful: if a cookie
value can only enter the process through this module, there is exactly one
place it can leak from.

No secret is committed, logged or rendered into a template. `Config.redacted()`
is what any diagnostic surface prints.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
RECORDINGS_DIR = DATA_DIR / "recordings"
PHRASES_DIR = DATA_DIR / "phrases"
STATE_DIR = DATA_DIR / "state"

#: Which Moments have already fired, so a restart does not replay the afternoon.
#:
#: A deploy, a crash or a systemd restart on a Sunday used to hand every phone in
#: the bar the whole day again -- seventy-three touchdown horns in a row. The
#: engine could always persist this and nothing ever asked it to: the feature was
#: reachable only from its own unit test.
SEEN_MOMENTS = STATE_DIR / "seen-moments.json"
STATIC_DIR = REPO_ROOT / "static"

#: The fixture that ships with the repo. A fresh clone with no cookies at all
#: replays this, which is the Phase 1 acceptance criterion.
DEMO_RECORDING = "demo-2025-11-16"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class Config:
    """A snapshot of the environment, resolved once at app creation."""

    league_id: str = ""
    season: int = 2025
    espn_s2: str = field(default="", repr=False)
    espn_swid: str = field(default="", repr=False)

    roast_level: int = 1  # 0 safe, 1 teasing, 2 savage
    poll_seconds: int = 30

    record: bool = False
    replay: str = ""
    replay_speed: float = 1.0

    recap_model: str = "qwen2.5-1.5b-instruct"
    recap_backend: str = "mlx"  # mlx | llamacpp

    admin_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Config":
        # Poll floor is 30 s even in development. ESPN's fantasy endpoints are
        # undocumented and unmetered by courtesy only; the way that courtesy ends
        # is somebody's dev loop hammering them at 1 Hz for a whole afternoon.
        poll = max(30, _env_int("POLL_SECONDS", 30))
        return cls(
            league_id=os.environ.get("LEAGUE_ID", "").strip(),
            season=_env_int("SEASON", 2025),
            espn_s2=os.environ.get("ESPN_S2", "").strip(),
            espn_swid=os.environ.get("ESPN_SWID", "").strip(),
            roast_level=max(0, min(2, _env_int("ROAST_LEVEL", 1))),
            poll_seconds=poll,
            record=_env_bool("RECORD", False),
            replay=os.environ.get("REPLAY", "").strip(),
            replay_speed=max(0.0, _env_float("REPLAY_SPEED", 1.0)),
            recap_model=os.environ.get("RECAP_MODEL", "qwen2.5-1.5b-instruct"),
            recap_backend=os.environ.get("RECAP_BACKEND", "mlx"),
            admin_token=os.environ.get("ADMIN_TOKEN", "").strip(),
        )

    # -- derived -----------------------------------------------------------

    @property
    def has_cookies(self) -> bool:
        return bool(self.espn_s2 and self.espn_swid)

    @property
    def is_replaying(self) -> bool:
        return bool(self.replay)

    @property
    def can_reach_espn(self) -> bool:
        """Live mode needs a league id and both cookies. Private leagues are the
        only kind this app is built for, so a league id alone is not enough."""
        return bool(self.league_id) and self.has_cookies

    def redacted(self) -> dict:
        """What a diagnostic route or a log line is allowed to say."""
        return {
            "league_id": self.league_id or "(unset)",
            "season": self.season,
            "espn_s2": _fingerprint(self.espn_s2),
            "espn_swid": _fingerprint(self.espn_swid),
            "roast_level": self.roast_level,
            "poll_seconds": self.poll_seconds,
            "record": self.record,
            "replay": self.replay or "(off)",
            "replay_speed": self.replay_speed,
            "recap_model": self.recap_model,
            "recap_backend": self.recap_backend,
            "mode": "replay" if self.is_replaying else ("live" if self.can_reach_espn else "demo"),
        }


def _fingerprint(secret: str) -> str:
    """Enough to tell two cookies apart in a log, not enough to use one."""
    if not secret:
        return "(unset)"
    return f"set:{len(secret)}ch:…{secret[-4:]}"


class SecretLeak(RuntimeError):
    """Raised at startup when a secret is found somewhere the world can read."""


def assert_no_secrets_in_static(cfg: Config, static_dir: Path = STATIC_DIR) -> None:
    """Fail loudly if a cookie value has been written into the static tree.

    The realistic accident is not someone pasting a cookie into a template; it is
    a build step, a cache dump or a debug artefact writing one into `static/`,
    where nginx will happily serve it to anyone who guesses the filename. This
    runs on every boot because it costs milliseconds and the failure it catches
    is unrecoverable once a crawler has been past.
    """
    needles = [s for s in (cfg.espn_s2, cfg.espn_swid, cfg.admin_token) if len(s) >= 8]
    if not needles or not static_dir.is_dir():
        return

    offenders: list[str] = []
    for path in static_dir.rglob("*"):
        if not path.is_file():
            continue
        # Audio and images cannot plausibly carry a cookie and dominate the tree.
        if path.suffix.lower() in {".mp3", ".ogg", ".wav", ".png", ".jpg", ".jpeg", ".webp", ".woff", ".woff2"}:
            continue
        try:
            blob = path.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        if any(n in blob for n in needles):
            offenders.append(str(path.relative_to(static_dir)))

    if offenders:
        raise SecretLeak(
            "An ESPN cookie or the admin token appears in the static tree, which is "
            "served to the public: " + ", ".join(sorted(offenders))
        )
