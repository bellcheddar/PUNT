# PUNT — where the build is

Live state of the build. The plan is `docs/punt_build_spec_v1.md`; this file says
what is actually done and what the next session should pick up.

## Status: live at https://punt.mdeller.com since 2026-09-11.

All six phases built. 372 tests, 1 skipped, all offline. Running on the demo
recording until the league credentials are handed over: see
[docs/credentials.md](credentials.md).

Nothing here needs credentials, a network or a browser profile. Watch any of it:

```bash
python3 -m pytest                             # 372 tests, no network, no cookies
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
and `views/viewmodels.py`. The eighty it cannot reach each carry a `# cold:`
comment in the source saying why. Keep it that way: see `CLAUDE.md`.

## Eight season panels, and what they open

Under their own rule below the scores. Each row opens its own sheet -- eight of
them plus one for a ticker line -- built on the panel's own view model rather
than beside it, so a sheet cannot quietly disagree with the figure that was
tapped to open it.

Every panel now carries a one-line note collapsed behind a `Details` expander,
with `hx-preserve` and a stable id on each: the note lives inside the fragment
its panel re-renders every thirty seconds, and without that an opened note snaps
shut mid-sentence on the next poll.

`static/js/pulse.js` marks what changed on each swap -- a wash for a row whose
numbers moved, a lift for a card that scored, a slide for a card that changed
position, a fade for a row that is new. Four treatments because four different
things change, and a single flash for all of them says less than none.

The music bed is the orchestral theme everywhere now, on the wall and in the
hand. It used to be the watch-party loop on a phone, which was the right call
for a LOOP and this does not loop: it plays once.

## Eight season panels

Under their own rule below the scores, because they answer a slower question than the rest of
the page. Season shape, the all-play grid, seed roulette, the gauntlet, the scoring clock, the
position ledger, boom or metronome and schedule swap. Seven needed no new data at all; the
scoring clock needed one field, `date` on each NFL scoreboard event, now parsed into
`GameState.kickoff` and bucketed by `GameState.window` -- and emitted by the fixture generator
too, or the panel would have been dead in the demo and `deadcode.py` would have said so.

Three things that were wrong and only visible in a capture: the gauntlet drew four opponents on
one line and they overlapped into a rainbow smear (a lane each now); the sparklines were flat
because one shared scale spanned a hundred points only one team ever used (5th to 95th
percentile, clamped); and the season-shape rows truncated team names to "Statisticall...",
which CLAUDE.md already lists as a defect found this way once before.

And one found only on the live site, which neither the demo nor a test would
have shown: the gauntlet reported itself available because there were fourteen
fixtures still to come, then drew nothing, because with no settled weeks there
was no record to measure any of those opponents by. An empty box with a caption
under it. `available` now means "there is something to draw" on every one of
these, never "the feed answered", and `tests/test_liveness.py` checks that no
panel renders an empty container.

One class of bug the tests caught rather than a screenshot: four of these divide by a range
computed from the data, and `default=` only fires on an EMPTY sequence. A full one of zeroes
returns zero, which is every panel before kickoff, so the live league's week one would have
been a 500 on the whole page -- on panels that had been looked at all afternoon at mid-Sunday.

## The LATEST strip

A thin reel above the album, stepping one line at a time through what has moved: scores, win
probability, playoff odds, clinching, album positions, bench regret, players hot and cold
against their prorated projection, and the commentary. `engine/ticker.py` is a differ rather
than a detector, and that is the point -- nobody's win probability falls twelve points in one
play, it falls twelve points over twenty minutes of the other bloke's running back grinding
out first downs, and there is no Moment anywhere in that.

Two traps, both now tested. The reel is stepped by a finite CSS `transition` driven from
JavaScript, never an infinite `@keyframes` loop, because anything that repeats forever stops
`--virtual-time-budget` from settling and would hang every headless capture in the repo. A
repeating `setInterval` does the same, so the wheel also refuses to start under `?punt=steady`,
and a test asserts that every tool driving Chrome with virtual time asks for a still page. That
was found by hitting it: `--dump-dom --virtual-time-budget` against `/` never returned.

## Everything on the page is live

Three panels shipped static: bench regret, playoff odds and who is in trouble were rendered
once at page load and never again. They were right on arrival, so nothing caught it -- a
screenshot of a freshly loaded page looks identical either way. Every panel on every route
polls its own fragment now, they are shared templates rather than one copy per tab, and
`tests/test_liveness.py` refuses a panel that arrives without a trigger. That test had to be
made strict before it worked: the first version skipped panels with no digits in their markup,
which meant it silently passed whenever a panel happened to be rendering its empty state.

The simulations are memoised, keyed on the player state that produced them rather than on the
snapshot object (which is rebuilt per request and would have missed every time while looking
like it worked). `probabilities_for` had been running five times per page render, and the
module docstring had claimed it was cached since the first commit. One poll window now costs
one simulation: the first phone pays 335 ms and every other phone pays 20.

## The season, not just the Sunday

Three things landed together, because they are the same feature seen from three
sides.

**The week rolls over on its own.** It always read `scoringPeriodId` from ESPN,
and mSettings was cached for a day -- so the period changed on a Tuesday morning
and PUNT carried on serving the Sunday that had already finished until some time
on Wednesday, with nothing looking broken. The TTL is ten minutes now. The live
feed empties everything that is per-week when the period moves: the Moment
buffer, the chosen lines, the red-zone overlays, the notable list, the counts and
the win-probability lows. Three of those six used to be cleared and three did
not, which looks fine on the Tuesday and puts last Sunday's commentary under this
Sunday's scores the following weekend. The dedupe set is deliberately kept: every
Moment id is hashed with its week.

**Every week is written down**, to `data/state/history.sqlite3` (gitignored, and
`engine/history.py` is the whole of it). Not a cache of ESPN, which will serve a
past box score for as long as the league exists. It is the half ESPN cannot give
back: the Moments the engine detected as they happened, the commentary it chose,
and what the optimal lineup *was* before anybody edited a roster. Written on
every poll, so there is no final whistle to miss, and once more at the rollover
before the buffer is emptied.

**The week is selectable** from a menu on the league line in the header. The live
week is the empty value in that menu, not its own number, so a URL with no week
in it follows the season and a bookmark of week 11 is still week 11 in December.
An archived page says so in a banner and does not open the SSE stream: everything
else on the page looks identical whether it is moving or finished.

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
| `engine/history.py` | Every week as PUNT saw it happen, in one SQLite file. Not a cache of ESPN: the Moments, the lines and the optimal lineup at the time are what ESPN cannot give back. |
| `views/`, `templates/`, `static/` | One page plus TV mode, htmx polling, SSE, PWA shell, synthesised audio, generated icons and splash screens. |
| `data/phrases/` | Ten YAML files and a README documenting the trigger DSL and the slot vocabulary. |
| `tests/` | 372 tests, 1 skipped, no network, no cookies, plus a golden Moment timeline. |

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
