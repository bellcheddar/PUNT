# PUNT — where the build is

Live state of the build. The plan is `docs/punt_build_spec_v1.md`; this file says
what is actually done and what the next person (or the next session) should pick up.

## Status: Phases 1-4 complete

Phases 1 to 4 are built. Watch any of them:

```bash
python3 tools/replay_check.py --speed 1800    # the scores moving
python3 tools/timeline.py                     # every Moment of the day
python3 tools/transcript.py                   # the commentary it would have said
python3 tools/phrase_lint.py                  # where the phrase bank is thin
python3 tools/screenshot.py --check-overflow  # no horizontal overflow at 390px
```

Three parts of the acceptance criteria are **not** met and are not met for the same
reason each time -- they need something this machine does not have:

| Gate | Needs |
|---|---|
| Phase 2: bench regret vs two real ESPN box scores | League credentials |
| Phase 3: 60 fps with ten cards on a real phone | A real phone |
| Phase 4: the mute toggle on iOS, and Piper | A real iPhone; Piper on the droplet |

Everything else passes, as `tests/test_phase1_acceptance.py` and
`tests/test_phase2_acceptance.py`. 147 tests, 1 skipped, all offline.

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
| `engine/scoring.py` | Exact optimal lineup (max-weight bipartite matching), bench regret, all-play, luck. |
| `engine/simulate.py` | Monte Carlo win probability with a lumpy per-player distribution. |
| `engine/events.py` | Nine Moment kinds, idempotent across restarts, magnitude-scaled. |
| `engine/live.py` | One background poller, event detection, SSE fan-out with backlog. |
| `tools/timeline.py` | Watch the day's Moments go past. |
| `tools/screenshot.py` | Phone-width captures, and `--check-overflow`. |
| `tests/` | 120 tests + 1 skipped, no network, no cookies, plus a golden Moment timeline. |

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

Neither blocks Phase 3, 4 or 5.

- [ ] **`LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID`** for the real league. Needed for the second
      half of the Phase 2 gate (reconcile bench regret against two known ESPN box scores)
      and for the real-device testing in Phase 6.
- [ ] **Whether real recordings may ever be committed.** Currently `.gitignore` tracks
      only `demo-*`, on the assumption that ten managers' ESPN display names should not
      be in a public repo.
- [ ] **The wordmark.** The spec says to commission it from the vibe-icon skill once the
      card geometry is locked, which is the end of Phase 3.

## Deployment

**The `deploy/` directory is written and tested; the droplet has not been touched.**

```bash
scp -r deploy root@45.55.102.228:/tmp/punt-deploy
ssh root@45.55.102.228 bash /tmp/punt-deploy/provision.sh   # creates user, venv, unit, vhost, cert
bash deploy/deploy.sh                                        # ships the code, verifies the new build
```

Port **8011**. 8000 to 8010 are taken (AlphaFraud, chem_sage-web, chatPDB-web,
BoltzMaker, FlexAppeal, PANTS, CODSWALLOP, ButtFold, ALPHABETTI, GOBSMACKED,
chatMCD). `provision.sh` refuses to install if anything is already listening,
and `tests/test_deploy_config.py` refuses if the number drifts apart across the
three files that mention it.

After it is live: add PUNT to the top of `mdeller-landing/apps.json` and
`./deploy.sh` there. Not done yet, because a launcher entry pointing at a host
with no service on it is a broken link on the front page.

Not deployed yet. `punt.mdeller.com` resolves to the droplet and nginx answers on
:80, but there is no vhost, no certificate and no service — TLS currently serves
another app's certificate. Needs: port allocation, `deploy/` unit + nginx conf +
certbot, and an entry in `mdeller-landing/apps.json`.

**One gunicorn worker with threads**, not several processes: the live feed's poller and
moment buffer are per-process, so a second worker doubles the upstream poll rate and gives
half the phones a different commentary feed. Moving the buffer to Redis is the prerequisite
for scaling out, and it is not needed for ten people.
