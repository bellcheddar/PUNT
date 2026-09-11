# PUNT — where the build is

Live state of the build. The plan is `docs/punt_build_spec_v1.md`; this file says
what is actually done and what the next session should pick up.

## Status: live at https://punt.mdeller.com since 2026-09-11.

All six phases built. 216 tests, 1 skipped, all offline. Running on the demo
recording until the league credentials are handed over: see
[docs/credentials.md](credentials.md).

Nothing here needs credentials, a network or a browser profile. Watch any of it:

```bash
python3 -m pytest                             # 214 tests, no network, no cookies
PORT=8019 python3 -m app                      # then http://127.0.0.1:8019
python3 tools/replay_check.py --speed 1800    # the scores moving
python3 tools/timeline.py                     # every Moment of the day
python3 tools/transcript.py --stats           # the commentary, and how often it repeats
python3 tools/recap.py                        # the weekly write-up
python3 tools/deadcode.py                     # which lines never run during a whole Sunday
python3 tools/screenshot.py                   # needs a server; see above
python3 tools/screenshot.py --check-overflow  # no horizontal overflow at 390px
python3 tools/a11y.py                         # contrast
python3 tools/perf.py                         # the blocking path
```

`tools/deadcode.py` reports **zero** never-executed lines across `engine/`, `espn/`
and `views/viewmodels.py`. The seventy-nine it cannot reach each carry a `# cold:`
comment in the source saying why. Keep it that way: see `CLAUDE.md`.

## What is left, and all of it needs Marc

Every remaining item is blocked on something this machine does not have. Nothing is
blocked on a decision about the code.

| What | Needs | Why it is not done |
|---|---|---|
| **Phase 2's second gate** | `LEAGUE_ID`, `ESPN_S2`, `ESPN_SWID` | Reconciling bench regret by hand against two real ESPN box scores. Everything else in Phase 2 passes against the fixture. Hand them over with `bash deploy/set-credentials.sh`; see [docs/credentials.md](credentials.md). |
| **Phase 3's 60 fps gate** | A real iPhone | Simulators do not reproduce the frame cost of ten foil cards. |
| **Phase 4's mute-toggle gate** | A real iPhone | Nor the iOS audio-session behaviour, which is where this app is most fragile. |
| **Piper** | Installing it on the droplet | It is the shipping TTS backend; macOS `say` is the local stand-in and the no-backend path is supported. |
| **The recap's model path** | `ollama pull qwen2.5:1.5b-instruct` (~1 GB) | The validator and the retry loop are driven against stub backends on every `deadcode.py` run, so the logic is exercised; only a real model's output is not. |
| **Whether real recordings may ever be committed** | A decision | `.gitignore` tracks only `demo-*`, on the assumption that ten managers' ESPN display names should not be in a public repo. |
| **The wordmark** | A decision | The spec says to commission it from the vibe-icon skill once the card geometry is locked. It is locked. |

### The one open question is closed

**No superflex, and no flex that accepts a quarterback** (Marc, 2026-09-11). So the
eligibility bug was latent and never live: the optimal lineup used to be computed against a
*guess* at slot eligibility rather than the `eligibleSlots` ESPN sends, and on this league's
slots the guess and the truth agree exactly. No bench-regret figure anybody ever saw was
wrong.

It is still worth having fixed, and not only for tidiness. The answer is a property of the
league's settings, not of the code, and a commissioner can change it between seasons in
about four clicks -- at which point the guess would report 6 points of bench regret where
the truth is 20, on the headline number of the whole app, silently. The app now reads the
league's own rule, so that is no longer a thing anybody has to remember.

## What exists

| Area | State |
|---|---|
| `espn/models.py` | Total parsers for teams, players, matchups, settings, NFL game state. Never raise; degrade into `.problems`. Every branch is driven. |
| `espn/cache.py` | TTL cache with single-flight, so ten phones make one upstream call. Serves stale on a failed refresh. |
| `espn/feeds.py` | Every endpoint, view name and TTL in one table. |
| `espn/client.py` | Cookie auth, exponential backoff with separate local and remote ceilings, auth-expiry state, `LeagueRepository` → `LeagueSnapshot`. |
| `espn/replay.py` | Recorder (`RECORD=1`) and replay transport, gzipped payloads, seekable clock. |
| `data/recordings/demo-2025-11-16/` | Synthetic ten-team Sunday: 10.9 h, 414 payloads, 3.2 MB on disk. Committed, seeded, byte-stable. |
| `engine/scoring.py` | Exact optimal lineup (max-weight bipartite matching), bench regret, all-play, luck, standings with ties. |
| `engine/simulate.py` | Monte Carlo win probability with a lumpy per-player distribution; playoff odds, magic numbers, clinch and elimination. |
| `engine/events.py` | Nine Moment kinds, magnitude-scaled, idempotent across restarts via a persisted dedupe set. |
| `engine/live.py` | One background poller, event detection, commentary, speech, SSE fan-out with backlog. |
| `engine/commentary.py` | Retrieval-only phrase bank: 410 lines, cooldowns, roast levels, weighted sampling, seeded per `(week, moment.id)`. |
| `engine/recap.py` | Grounded weekly write-up. Every numeral and proper noun must appear in the fact pack; three rejected samples fall back to the template. |
| `engine/speech.py` | Phrase audio cached by the hash of what is spoken, rendered on a pool as soon as the server picks the line. |
| `views/`, `templates/`, `static/` | Seven tabs plus TV mode, htmx polling, SSE, PWA shell, synthesised audio, generated icons and splash screens. |
| `data/phrases/` | Ten YAML files and a README documenting the trigger DSL and the slot vocabulary. |
| `tests/` | 214 tests, 1 skipped, no network, no cookies, plus a golden Moment timeline. |

## The fixture's planted storylines

The generator plants one of every event the engine has to detect, because a seeded
four-hour simulation left to chance might contain none of them and the test for the
funniest event in the app would pass by asserting nothing.

| Storyline | Who |
|---|---|
| A 41.2-point bench disaster | Priya |
| A goose egg from a starter | Gus |
| Mathematically finished by mid-afternoon | Wren |
| A Sunday-night lead change, and his season high | Sam |
| Two injured starters and one injured bench player | Chidi, Theo, Noor |
| Three starters in one NFL game | Bex |
| A week that ended level | Priya and Bex, week 4 |

## Deployment

**Provisioned and deployed 2026-09-11.** Certificate, http2 and the launcher
entry are all in place. To ship a change:

```bash
bash deploy/deploy.sh
```

It runs the whole test suite first and refuses to ship if anything fails, then
verifies three separate things about the running service afterwards, because
any one of them alone can pass over a failed deploy.

The first-time setup, kept here because it is idempotent and safe to re-run:

```bash
scp -r deploy root@45.55.102.228:/tmp/punt-deploy
ssh root@45.55.102.228 bash /tmp/punt-deploy/provision.sh   # user, venv, unit, vhost, cert
bash deploy/deploy.sh                                        # ships the code, verifies the new build
```

Port **8011**. 8000 to 8010 are taken (AlphaFraud, chem_sage-web, chatPDB-web,
BoltzMaker, FlexAppeal, PANTS, CODSWALLOP, ButtFold, ALPHABETTI, GOBSMACKED,
chatMCD). `provision.sh` refuses to install if anything is already listening, and
`tests/test_deploy_config.py` refuses if the number drifts apart across the three
files that mention it.

The vhost splits `/static/`. CSS, JS and fonts are only ever requested with a `?v=`
stamp, so they get the droplet's shared long-cache snippet (immutable, a year) and
gzip. Icons, splash screens, the manifest and the audio sprites are fetched
*without* a stamp, by the manifest and by howler, so those stay at thirty days: a
year-old icon pinned on every returning phone is not a cache, it is a bug.

The HTML `no-cache` header is set by the app, not by nginx. `add_header` appends
rather than replaces, so the vhost version arrived alongside the team-logo routes'
own `public, max-age=86400`, and a browser joins repeated Cache-Control field lines
into one value where `no-cache` wins. Every logo was revalidated on every page load.

**One gunicorn worker with threads**, not several processes: the live feed's poller and
Moment buffer are per-process, so a second worker doubles the upstream poll rate and gives
half the phones a different commentary feed. Moving the buffer to Redis is the prerequisite
for scaling out, and it is not needed for ten people.
