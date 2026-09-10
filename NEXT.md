# PUNT — where the build is

Live state of the build. The plan is `docs/punt_build_spec_v1.md`; this file says
what is actually done and what the next person (or the next session) should pick up.

## Status: Phase 1 complete

**Phase 1 acceptance criterion — "a full recorded Sunday replays at 60x speed with
no network access and no cookies" — passes**, as `tests/test_phase1_acceptance.py`.
Watch it with:

```bash
python3 tools/replay_check.py --speed 1800
```

### What exists

| Area | State |
|---|---|
| `espn/models.py` | Total parsers for teams, players, matchups, settings, NFL game state. Never raise; degrade into `.problems`. |
| `espn/cache.py` | TTL cache with single-flight, so ten phones make one upstream call. Serves stale on a failed refresh. |
| `espn/feeds.py` | Every endpoint, view name and TTL in one table. |
| `espn/client.py` | Cookie auth, exponential backoff to 5 min, auth-expiry state, `LeagueRepository` → `LeagueSnapshot`. |
| `espn/replay.py` | Recorder (`RECORD=1`) and replay transport, gzipped payloads, seekable clock. |
| `data/recordings/demo-2025-11-16/` | Synthetic ten-team Sunday, 10.9 h, 416 payloads, 2 MB. Committed. |
| `views/`, `templates/`, `static/css/theme.css` | All seven routes render, htmx polling wired, TV mode, monogram fallback, empty states everywhere. |
| `tools/make_fixture.py` | Regenerates the fixture, seeded and byte-stable. |
| `tools/replay_check.py` | Watch a Sunday go past in the terminal. |
| `tests/` | 56 tests, no network, no cookies. |

### Decisions taken during Phase 1

- **The committed fixture is synthetic, not a capture of the real league.** A real
  capture is ~500 MB and carries ten real people's ESPN display names and account
  GUIDs into a public repository. The generator plants one of every event the
  engine must detect, so Phase 2's tests have something to assert against.
- **`views/` is a package**, split by response kind (pages, partials, JSON, media,
  admin) rather than by tab, because caching and degradation differ per kind.
- **Payloads are gzipped in recordings.** Fantasy JSON compresses about twentyfold.
- **The NFL scoreboard is fetched in Phase 1**, earlier than the plan implies. Without
  it, "scored nothing" and "has not kicked off" are indistinguishable, and a settled
  Sunday night tells every manager they still have four players to play.

## Next: Phase 2 — the engine

Acceptance: *replaying a recorded Sunday emits a plausible Moment timeline, and
bench regret figures reconcile by hand against the ESPN box score for two known weeks.*

- [ ] `engine/events.py` — poll diffing to `Moment`s, idempotent across restarts,
      magnitude-scaled. Note the fixture contains a `-2.0` turnover: deltas are signed.
- [ ] `engine/scoring.py` — optimal lineup under real slot eligibility, bench regret,
      all-play, luck index. Replaces the lower-bound placeholders in `views/viewmodels.py`
      (every one is marked `PHASE2`).
- [ ] `engine/simulate.py` — Monte Carlo win probability and playoff odds.
- [ ] Wire `Moment`s into the existing `/stream` SSE endpoint, which is currently a
      working heartbeat.
- [ ] Golden-file test: a recorded week produces a byte-identical Moment timeline.

### Blocked on Marc

- [ ] **`LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID`** for the real league. Everything through
      Phase 4 can be built and tested without them, but the "reconcile bench regret
      by hand against two known weeks" half of the Phase 2 gate needs real box scores.
- [ ] **Whether real recordings may ever be committed.** Currently `.gitignore` tracks
      only `demo-*`, on the assumption that ten managers' ESPN display names should not
      be in a public repo.

## Deployment

Not deployed yet. `punt.mdeller.com` resolves to the droplet and nginx answers on
:80, but there is no vhost, no certificate and no service — TLS currently serves
another app's certificate. Needs: port allocation, `deploy/` unit + nginx conf +
certbot, and an entry in `mdeller-landing/apps.json`.
