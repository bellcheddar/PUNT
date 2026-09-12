# 🏈 PUNT

> **Play-by-play, uproar, numbers and trash-talk: a fantasy football companion built for a bar, not a spreadsheet.**

[![live](https://img.shields.io/badge/live-punt.mdeller.com-00d084?logo=icloud&logoColor=white)](https://punt.mdeller.com) ![python](https://img.shields.io/badge/python-3.12.3-3776AB?logo=python&logoColor=white) ![flask](https://img.shields.io/badge/flask-3.1.3-000000?logo=flask&logoColor=white) ![gunicorn](https://img.shields.io/badge/gunicorn-26.2.0-499848?logo=gunicorn&logoColor=white) ![nginx](https://img.shields.io/badge/nginx-1.24.0-009639?logo=nginx&logoColor=white) ![sqlite](https://img.shields.io/badge/sqlite-3.45.1-003B57?logo=sqlite&logoColor=white) ![htmx](https://img.shields.io/badge/htmx-2.0.4-3366CC?logo=htmx&logoColor=white) ![howler](https://img.shields.io/badge/howler.js-2.2.4-9b51e0) ![requests](https://img.shields.io/badge/requests-2.34.2-467FF7) ![pyyaml](https://img.shields.io/badge/PyYAML-6.0.3-467FF7) ![tests](https://img.shields.io/badge/pytest-346%20passing-00897B?logo=pytest&logoColor=white) ![data](https://img.shields.io/badge/data-ESPN%20Fantasy%20%C2%B7%20ESPN%20Scoreboard-9b51e0) ![audio](https://img.shields.io/badge/audio-CC0%20%C2%B7%20CC--BY%204.0-00897B) ![licence](https://img.shields.io/badge/licence-MIT-00d084) ![author](https://img.shields.io/badge/author-Marc%20C.%20Deller%2C%20D.Phil.-1C244B)

<table>
<tr>
<td>🌐 <b>App</b></td><td><a href="https://punt.mdeller.com" target="_blank" rel="noopener noreferrer">punt.mdeller.com</a></td>
<td>✉️ <b>Contact</b></td><td><a href="mailto:marc@marcdeller.com">marc@marcdeller.com</a></td>
<td>🐙 <b>GitHub</b></td><td><a href="https://github.com/bellcheddar/PUNT" target="_blank" rel="noopener noreferrer">bellcheddar/PUNT</a></td>
</tr>
</table>

---

![PUNT on a phone: ten manager cards tiered by form, the week's matchups with live scores, bench regret, who is in trouble, and the commentary feed, all on one page](docs/screenshots/home.png)

The ESPN app is a spreadsheet with a logo. PUNT is the opposite: a mobile-first companion for a
ten-team bar league that exists to make a Sunday afternoon louder. It reads the same undocumented
ESPN fantasy API the app does, turns polling into discrete **Moments**, and renders them as a
sticker album of collectible manager cards with horns, commentary and a bar-screen mode. Lineups are
still set in ESPN; PUNT is read-only.

**Why it matters:** the numbers that make fantasy football funny (points left on the bench, a
one-in-twenty comeback, a starter who finished on zero) are all computable and none of them are
shown by the official app, which is optimised for a manager alone on a sofa rather than for ten
people in a room. Every design decision here resolves in favour of the room: audio on unless muted,
no settings screen, no accounts, one URL, one page. It is useful for anyone running a private ESPN
league who wants a shared screen worth looking at: a commissioner setting up a weekly ritual, a
league that already has a group chat full of receipts, or anyone who wants a worked example of
consuming ESPN's undocumented fantasy endpoints without a browser extension.

## 📸 What it looks like

Everything is on one page. It used to be five tabs, and they were plain links, so every tab change
was a new document: audio needs a user gesture per document, which meant the tap that changed tabs
was also the tap that unlocked the sound, and the browser tore the document down mid fade-in. One
document fixes that by construction, and the panels turn out to fit beside each other anyway.

![The same page on a desktop: five cards to a row, the week's matchups two to a row, and the panels paired left and right](docs/screenshots/desktop.png)

![The season panels: a sparkline of every team's weekly scores, the ten-by-ten all-play grid, the finishing-seed distribution, and what everybody has left to play](docs/screenshots/season.png)

| The album | The receipts | The bar screen |
|---|---|---|
| ![Ten manager cards, two to a row, each tinted in that team's own colour and tiered epic, rare, common or cursed, with the form rating printed under the score](docs/screenshots/album.png) | ![Every manager ranked by points left on the bench, with the exact swap that cost them](docs/screenshots/receipts.png) | ![The Big Board in TV mode: the whole slate on the bar screen](docs/screenshots/big-board.png) |

Every panel is also its own route (`/album`, `/cheer`, `/swing`, `/receipts`, `/multiverse`), which
is what the screenshots use and what `?tv=1` narrows to. Everything above is the synthetic Sunday
that ships with the repository: no real athlete, franchise or person appears in it.

## ⚡ The engine

Polls produce state. Nobody cheers at "Dax Ashgrove now has 18.4 points". `engine/`
turns consecutive polls into **Moments**, which is what the audio, the commentary and
the cards all consume.

```bash
python3 tools/timeline.py                       # the whole Sunday, moment by moment
python3 tools/timeline.py --kinds DOOM,BENCH_DISASTER
python3 tools/timeline.py --summary
```

The demo Sunday produces 242 Moments across 10.9 hours:

```
16:10  DOOM            0.85  Wren       2.1% with 59.1 to find
16:30  CLINCH          0.75  Sam        61.9 up, 98% safe
17:05  BENCH_DISASTER  0.76  Priya      Delroy Marchbank (21.4) benched, Ash Greenhalgh (0.0) in the FLEX
18:32  LEAD_CHANGE     0.78  Noor       in front by 1.2
19:02  BENCH_DISASTER  0.92  Priya      Wilder Braithwaite (41.2) benched, Yusuf Marchbank (9.9) in the WR
19:05  GOOSE_EGG       0.70  Gus        Silas Stonebridge finished on nothing, projected 11.5
22:01  LEAD_CHANGE     0.52  Bex        in front by 1.6
```

Three properties are design constraints rather than niceties:

- **Idempotent, including across a restart.** A Moment's id is a hash of the play's own
  facts (the player, his cumulative total after it, and the week), never of the poll or the process
  that observed it. Two processes a week apart agree on the id. A crash at 4pm therefore
  does not replay the afternoon through a bar's PA.
- **Magnitude-scaled.** Every Moment carries a 0 to 1 magnitude that the audio and
  animation layers scale off, so a two-point reception and a sixty-yard touchdown do not
  get the same horn.
- **Bench-aware.** `BENCH_DISASTER` is invisible in the score, so it has its own detection
  path off the optimal lineup rather than falling out of a points delta.

### 🎯 Two numbers that are not the score

Both exist because the obvious number turned out to measure something else.

**Form**, out of 100, is what the ten cards are ranked on. Ordering an album by points at three in
the afternoon mostly ranks managers by how many of their players happened to kick off at one
o'clock, which is not a thing anybody did. Four parts, weighted, summing to one:

| Part | Weight | What it asks |
|---|---|---|
| Pace | 0.35 | points against what was *due by now*: every starter's projection prorated by how much of his real NFL game has been played |
| Winning | 0.25 | the live chance of taking the head-to-head, because a 60-point week is a bad week if the opponent has 90 |
| Lineup | 0.20 | the share of the best possible score that was actually started |
| Scale | 0.20 | this score against the best in the league, because a big score is still an achievement |

Pace is capped at twice the prorated projection before it is normalised: without a cap, one kickoff
return in the first quarter, when the denominator is tiny, pins that team at the top of the album
until teatime. Before the first snap every part that measures performance is neutral rather than
zero, because nothing has been measured yet.

**At stake**, on the Cheer panel, is the fantasy points still to come out of one NFL fixture. It
replaced a column that read `STAKE` on all sixteen rows, which is a label and not information: a
room deciding which of fourteen televisions to look at learned nothing from it. Each row is also
banded by consequence rather than by size, on whether both halves of somebody's head-to-head have a
starter in that game: the difference between a fixture worth a lot of points to one manager who is
already forty ahead, and a fixture that decides the week. Tapping one opens both: who is exposed,
and which head-to-heads it swings.

### Optimal lineup, and why greedy is wrong

Bench regret is the headline number of the whole app, so it is computed exactly, by
maximum-weight bipartite matching between players and slots rather than by sorting and
filling. Greedy fails on the case that matters: a flex-eligible player put in the flex
because he was the highest scorer left, when he was the only legal option for a slot
further down the list.

```bash
python3 -m pytest tests/test_scoring.py -k oracle    # against brute force and a bitmask DP
```

Slot eligibility comes from ESPN's own `eligibleSlots` rather than a guess at what a
position can fill. The guess and the truth happen to agree on this league's slots, so the
bug was latent and never live: no bench-regret figure anybody saw was ever wrong. It is
still worth having fixed, because the answer lives in the league's settings where a
commissioner can change it between seasons in four clicks, at which point a guess would
report 6 points of regret where the truth is 20, silently.

### Win probability

Monte Carlo, re-run every poll, seeded from the matchup and the current scores so two
identical polls return an identical number (a probability that flickers by a point every
thirty seconds is indistinguishable, to somebody watching, from something happening).

Simulation rather than a closed form because the tails are what the product uses. Fantasy
scoring is lumpy (a touchdown is a six-point step), so a manager needing 22 points from
a player projected for 8 is a long shot rather than a zero. A normal approximation puts
almost no weight there, which would make the Legendary card (a win from under 10%)
impossible to mint and would fire DOOM far too early.

## 🗓️ The season, not just the Sunday

Three things that are one feature seen from three sides.

**The week rolls over on its own.** The scoring period was always read from ESPN, and `mSettings`
was always cached for a day: so the period changed on a Tuesday morning and PUNT carried on serving
the Sunday that had already finished until some time on Wednesday. Nothing looked broken, so nobody
would have restarted it. The TTL is ten minutes now, and `tests/test_weeks.py` asserts it stays
under fifteen. When the period moves, the live feed empties all six of its per-week stores: the
Moment buffer, the chosen lines, the red-zone overlays, the notable list, the per-kind counts and
the win-probability lows. Three of those six used to be missed, which looks fine on the Tuesday and
puts last Sunday's commentary under this Sunday's scores a fortnight later, when the two-hour buffer
finally has something to push out. The dedupe set is deliberately kept, because every Moment id is
hashed with its week.

**Every week is written down**, to `data/state/history.sqlite3` (gitignored; `engine/history.py` is
the whole of it). Not a cache of ESPN, which will serve a past box score for as long as the league
exists. It is the half ESPN cannot give back: the Moments the engine detected as they happened, the
commentary it chose, and what the optimal lineup *was* before anybody edited a roster. Written on
every poll, because there is no reliable final whistle, and once more at the rollover before the
buffer is emptied.

**The week is selectable** from a menu on the league line in the header. The live week is the empty
value in that menu rather than its own number, so a URL with no week in it follows the season and a
bookmark of week 11 is not still week 11 in December. An archived page says so in a banner and does
not open the SSE stream: everything else on the page looks identical whether it is moving or
finished.

The header also shows the season being played rather than the year the league was created. ESPN
keeps the original name forever, so "Logan House 2023" reads as "Logan House 2026".

## 🔊 The commentary and the noise

Live play calls come from a deterministic phrase bank, not a model. The latency
budget is under 100 ms and the requirement is that a line can never invent a score;
a model fails both, and is perfectly capable of announcing a touchdown that did not
happen. Every number in every line is substituted from the Moment that triggered it,
so there is nowhere for an invented one to come from.

```bash
python3 tools/transcript.py                    # the afternoon's commentary
python3 tools/transcript.py --stats            # the repeat rate across a whole Sunday
python3 tools/phrase_lint.py                   # validate the bank
python3 tools/make_audio.py                    # rebuild the sprite from the manifest
python3 tools/make_licences.py --check         # fail the build if the credits have drifted
```

Lines are chosen on the **server** and pushed with the Moment. Ten phones in one
room have to hear the same sentence for the same touchdown; picking client-side
would give ten different ones.

### Where the sound comes from

Fourteen effects and two music beds, sourced from Freesound rather than synthesised. The rule used
to be that everything was made from oscillators, which kept the credits from drifting because
nothing in them was anybody else's. It also cost the audio: the synthesised horns carried under 8%
of their energy above 2 kHz, which is what "dull, like a tone through a blanket" measures as, and a
convincing crowd roar cannot be made out of oscillators.

Sampled sound is allowed now, and the safety is kept differently. `data/audio_sources.json` is what
both the sprite builder and the credits read, each entry pinned by sha256, so a sound cannot reach
the sprite without a licence line, and `tools/make_licences.py --check` fails the build if the two
disagree.

| Asset class | Licence | Why |
|---|---|---|
| The fourteen effects | **CC0** only | An attribution clause would put an obligation on everyone who clones a public repo, and a non-commercial one would make it undistributable |
| The two music beds | CC0 or **CC-BY 4.0** | The Sunday-night theme is the one exception worth making, and the credit is generated from the manifest into `static/audio/LICENCES.md` and rendered in the page footer, where the work is actually heard |

Broadcast themes, fight songs and stadium recordings are all copyrighted compositions owned by the
networks. Nothing here is one.

Speech is cached by the hash of exactly what is spoken, and synthesis starts when
the *server* picks the line rather than when a phone asks for the file, which hides
the latency behind the SSE round trip.

### Three things that were measured, not assumed

- **Repetition.** The first transcript run showed 236 lines from 75 distinct phrases
  with the closest repeat exactly 8 Moments apart, which is `cooldown: 240` divided
  by the 30 s poll: the mechanism working perfectly at a badly chosen setting.
  Retuning the cooldowns before writing any more lines moved the closest repeat to 26
  Moments; growing the bank to 410 took the repeat rate from 68% to 43% and the worst
  line from 11 uses to 5. Tuning the cheap parameter first was worth roughly half the
  improvement and cost nothing.
- **Clipping.** Web Audio hard-clips at the destination, and the spec's nominal bus
  levels sum to 1.42 with music fully ducked. Rescaled so all four buses ducked under
  a play call sum to exactly 1.0, with a test that reads the levels out of the
  JavaScript and asserts both that and the priority order, because the first attempt
  satisfied the sum and silently inverted the order.
- **Clicks at the loop seam.** Trimming every sound at an 8 ms zero-crossing window left a
  discontinuity the ear hears as a tick every eight seconds for four hours. Appending 14 ms of
  silence (longer than the trim window) fixes it. The first version of the check compared the
  first sample to the last, which is one ordinary sample step, and flagged a perfectly good loop.

## 🚀 Quick start

A fresh clone runs the whole app with **no ESPN account, no cookies and no configuration**. It falls
back to a recorded Sunday committed to the repository, and says so in a banner on every page.

```bash
git clone https://github.com/bellcheddar/PUNT.git
cd PUNT
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 -m app                    # http://127.0.0.1:8011
```

Watch a whole recorded Sunday go past in the terminal instead:

```bash
python3 tools/replay_check.py --speed 1800     # ten hours in twenty seconds
python3 tools/replay_check.py --once           # just the final scores
python3 tools/replay_check.py --list           # what recordings are available
```

To point it at a real league, copy `.env.example` to `.env` and fill in `LEAGUE_ID`, `ESPN_S2` and
`ESPN_SWID`. Both cookies come from a browser logged into ESPN (devtools, Application, Cookies,
`espn.com`); `SWID` includes its braces. They rotate every few months, and when they do the app
keeps serving cached data behind a commissioner banner rather than breaking.

## 🧱 Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Flask, blueprint per response kind | Matches the conventions of the other apps on the same box |
| Templating | Jinja2, server-rendered | First paint is HTML, no hydration cost on a phone |
| Liveness | htmx polling (30 s) plus SSE for instant events | No front-end framework, no build step |
| History | SQLite, one file, standard library | Ten people do not need a database server |
| Audio | Howler.js over Web Audio | Sprites, mobile unlock handling, per-bus volume |
| Animation | CSS transforms and Web Animations API | GPU-composited only: `transform` and `opacity`, never `top`/`width` |
| Text | Deterministic phrase bank, plus a small local model for the weekly recap | A model in the live path is both too slow and able to invent a score |
| Cache | In-process TTL cache with single-flight | Ten phones must produce one upstream poll, not ten |

No React, no Vue, no npm, no build step. The whole JS payload is htmx (16 kB gzipped) and howler.js,
both vendored rather than pulled from a CDN because the target environment is a bar's shared wifi.

## 🗂️ Repository layout

```
PUNT/
├── app.py                      # factory, blueprint registration
├── config.py                   # env-driven config, plus the startup secret audit
├── espn/
│   ├── feeds.py                # every endpoint, view name and TTL, in one table
│   ├── client.py               # cookie auth, backoff, LeagueRepository
│   ├── cache.py                # TTL cache with single-flight
│   ├── models.py               # total parsers: Team, Player, Matchup, GameState
│   └── replay.py               # recorder and replay transport
├── engine/                     # events, scoring, simulation, commentary, speech
│   └── history.py              # every week as it happened, in one SQLite file
├── data/
│   ├── phrases/                # YAML phrase banks
│   ├── audio_sources.json      # the audio manifest: every sound, pinned and licensed
│   ├── state/                  # gitignored: the dedupe set and the season's history
│   └── recordings/             # captured payloads; the demo Sunday is committed
├── static/                     # css, vendored js, audio sprite, generated icons
├── templates/                  # base, tabs/, partials/
├── views/                      # blueprints, split by response kind
├── tools/                      # twenty of them; see Tests below
└── tests/
```

`views/` is split by **response kind** (pages, htmx partials, JSON, media, admin) rather than by
panel, because caching, error handling and degradation differ per kind and not per panel: a broken
partial swaps in a stale banner, a broken page renders an empty state, and a broken API call returns
a 200 with `problems` populated.

## 🔌 The data layer

Cookies live server-side only. A browser cannot send them cross-origin, which is the whole reason
this app has a server: the Flask process holds them and the ten phones hold nothing.

| Feed | Cache TTL | Purpose |
|---|---|---|
| `mSettings` | 10 min | Scoring items, playoff seeds, roster slots, the members block, and `scoringPeriodId` |
| `mTeam` | 6 hours | Team names, abbreviations, uploaded logos, owner display names |
| `mMatchup` | 1 hour | Season grid, for all-play, luck and simulations |
| `mMatchupScore` + `mBoxscore` | 30 s | Per-slot player points and projected remainder |
| `mRoster` | 5 min (30 s live) | Starters versus bench, the basis of bench regret |
| `kona_player_info` | 15 min | Projections, injury flags, ownership |
| NFL scoreboard (public) | 20 s | Possession, down and distance, red zone, clock |

The `mSettings` TTL is minutes rather than the season it used to be for one reason: it carries
`scoringPeriodId`, which decides which week the entire application is showing. Everything else in
that payload is set once in August.

Three rules the whole layer is built around:

- **Every parser is total.** It accepts any JSON at all and returns a valid object, recording what it
  could not understand in `.problems` rather than raising. ESPN changes payload shapes without
  notice, and a `KeyError` here takes down a whole request while a degraded `Player` takes down one
  row of one panel, which then says so.
- **Ten phones, one poll.** The TTL cache holds a per-key lock across the fetch, so the moment of
  highest concurrency is not the moment the cache stops working. A failed refresh serves the stale
  value rather than raising.
- **Team identity is resolved live.** There is no manager list in this repository. Names, logos and
  abbreviations come from `mTeam` on every load, so a mid-season rename appears without a deploy.
  Card colours are hashed from the team id, so every phone agrees with no shared state and the
  colour survives a rename.

**`mSchedule` is not a real ESPN view.** It was asked for from the first commit and ESPN simply
ignored it, and the synthetic fixture answered to whatever PUNT asked, which hid the mistake for the
whole build. The season grid comes from `mMatchup`; the feed keeps the old name as its cache key.

## 🎬 The replay harness

Development happens on Tuesdays, when nothing is live. Without a replay harness nothing downstream
of the fetch layer is testable at all: no event detection, no commentary, no audio timing. It is the
first milestone for that reason.

```bash
RECORD=1 python3 -m app                                     # capture a live Sunday
REPLAY=2025-11-16-134500 REPLAY_SPEED=60 python3 -m app     # replay it at 60x
```

A recording is a directory of raw upstream payloads plus a manifest of when each arrived.
`ReplayTransport` implements the same protocol as the live client, so every layer above it (the
cache included) cannot tell the difference.

**The committed fixture is synthetic, deliberately.** A capture of a real Sunday is roughly 480 polls
of a payload approaching a megabyte, and it carries ten real people's ESPN display names and account
GUIDs. This is a public repository. `tools/make_fixture.py` instead generates a seeded, invented
ten-team Sunday containing one of every event the engine has to detect: a planted bench disaster
(41.2 points benched in favour of a starter who managed 1.4), a goose egg, a manager mathematically
out of it by mid-afternoon, and a Sunday-night lead change. That is what the tests assert against,
rather than hoping a real week happened to contain them.

```bash
python3 tools/make_fixture.py            # regenerate, seeded and byte-stable
python3 tools/make_fixture.py --check    # verify the committed fixture is current
```

It also has to be stress-tested with long names. The fixture's managers are Bex, Gus and Sam: median
four characters. The first real league had a median of fourteen, so every layout decision was made
against the easy case.

```bash
python3 tools/stress_names.py            # the demo, served with real-length names
```

## ⚙️ Configuration

Everything is read from the environment and nowhere else, which is what makes the startup secret
audit meaningful: if a cookie can only enter the process through `config.py`, there is exactly one
place it can leak from. On boot, PUNT scans the public static tree for its own cookie values and
refuses to start if it finds one.

| Variable | Default | Notes |
|---|---|---|
| `LEAGUE_ID` | — | The numeric id in the ESPN fantasy URL |
| `SEASON` | `2025` | |
| `ESPN_S2`, `ESPN_SWID` | — | Session cookies. Never committed, never logged, never rendered |
| `ROAST_LEVEL` | `1` | 0 safe, 1 teasing, 2 savage. Applies to the phrase bank |
| `POLL_SECONDS` | `30` | 30 is a floor and is enforced in code, not just documented |
| `RECORD` | `0` | Write every upstream response to `data/recordings/` |
| `REPLAY`, `REPLAY_SPEED` | — | Replay a recording instead of going upstream |
| `ADMIN_TOKEN` | — | Required for `POST /admin/refresh-cookies`. Unset denies everything |

## 🧪 Tests

```bash
pip install -r requirements-dev.txt
python3 -m pytest                              # 346 tests, no network, no cookies
python3 tools/deadcode.py                      # which lines never run during a whole Sunday
python3 tools/screenshot.py                    # captures at a real phone width
python3 tools/screenshot.py --check-overflow   # no horizontal overflow at 390px
python3 tools/a11y.py                          # contrast, no browser needed
python3 tools/a11y.py --page                   # focus rings, targets, live regions
python3 tools/perf.py                          # payload budgets
python3 tools/perf.py --page                   # frame cost, subresources, paint timings
```

Every test runs against the committed recording with sockets disabled by a fixture, so "replays with
no network access" is an assertion rather than a claim.

Two gates are worth naming. One is a single test,
`test_full_sunday_replays_at_60x_with_no_network_and_no_cookies`. The other is a golden file: the
whole Sunday's Moment timeline, 242 of them across 10.9 hours, compared byte for byte. Regenerate it
deliberately with `python3 -m tests.golden_regen` and read the diff, because a change to detection
that alters the afternoon is exactly the kind of thing somebody should have to look at.

### `tools/deadcode.py` reports zero, and that is the point

It is not test coverage. It drives a whole simulated Sunday, at eight points across the day, and
reports what the application never reached. Everything under `engine/`, `espn/` and
`views/viewmodels.py` runs; the eighty lines that cannot be reached each carry a `# cold:` comment
saying why. A new cold line means one of two things, and both need doing rather than noting: wire
the feature up, or say why it is unreachable.

It has found six features that looked finished and had never once executed, each with a passing test
beside it: INJURY, the Legendary card tier, the branch that reads ESPN's real lineup eligibility,
the dedupe set that stops a restart replaying the afternoon, half the phrase-trigger vocabulary, and
the recap validator's rejection path. A test can keep a line warm while the application never
reaches it, which is exactly what happened every time.

It also lied in three ways of its own, all fixed, all general: `sys.settrace` is per-thread, so
anything in a worker reports as dead; `if (` on its own line never gets a trace event, because the
interpreter reports the line of the condition's first operand; and rendering the view models only at
the final whistle marks every mid-afternoon branch cold.

### Tests that are there because they caught something real

- Asserting team totals never decrease failed immediately on a `-2.0` turnover in the fixture.
  Fantasy scores are not monotonic; the test was wrong and the fixture was right, and the event
  engine has to survive the same thing.
- Asserting every rostered player has a game on the NFL scoreboard caught a nineteen-team early
  window. The odd team out had no game, so every player on it had an unknown game state, never
  settled, and told their manager all night that somebody was still to play.
- Asserting a finished game reads finished within a poll caught the two feeds being coupled in the
  fixture generator: a minute in which nobody scored also dropped the game-state change in the same
  minute, so the last game of the night stayed "in progress" for ten minutes after it ended and a
  settled matchup came back from the simulator at 91% instead of 100%.
- A test claiming "needing 22 points from one player" was a long shot got 53% back, and was wrong:
  the player was projected for 22. The simulator was right. It now checks the coin-flip case
  explicitly so the tail test is measuring something.
- `/partials/moments?week=9` shipped broken and the whole suite said nothing, because it is the one
  fragment that does not come from the snapshot. There is now a test that fetches every route with
  every shape of week parameter, including nonsense ones.

## 📱 Mobile and the installed-app illusion

The app is designed at 390 px before anything else exists, and ships as a PWA: manifest, service
worker, iOS legacy meta tags, generated splash screens, safe-area insets. Fantasy data is never
served stale from the service worker; only the shell is.

The divergences that cost a day each if discovered late: iOS ignores most of `manifest.json` and
needs the legacy meta tags, the ringer switch mutes HTML5 audio but not Web Audio,
`DeviceOrientationEvent.requestPermission()` must be called from inside a user gesture *and* exists
on desktop Chrome (so gate on `(hover: none) and (pointer: coarse)`, not on the method), and
`100vh` changes mid-scroll on both platforms (use `100dvh`).

Capturing screenshots at a phone width needs `tools/screenshot.py` rather than `--window-size`:
Chrome's window has a 500 px platform minimum on macOS, so a 390 px capture is silently a crop of a
500 px layout. Everything looks broken and none of it is.

The same tool runs `--check-overflow`, which loads every route in a 390 px iframe and reports any
element wider than the viewport. Horizontal overflow is the failure this project keeps producing and
cannot see: a table column pushed past the right edge is simply not drawn, with no scrollbar and
nothing to suggest it exists.

**Look at the screenshots.** Eight separate defects here were invisible in the code and obvious in a
capture: truncated team names, a demo opening on ten zeros, an HTML entity rendered literally, the
TV type at phone size, every "IN" chip the wrong colour, and `best 80…` in the bench-regret table (a
number cut in half, which is worse than no number, because 80.4 and 809 truncate identically).

## ✅ To Do

Where PUNT actually is, newest first. Completed items keep their reasoning: the list doubles as the
project record, and the reasoning behind a finished decision is usually the most useful part.

### Built

- [x] **A LATEST strip along the top.** A slot-machine reel, one line at a time, of what has
      *moved* rather than what has *happened*: score jumps, win probability, playoff odds,
      clinching and elimination, album positions, bench regret, players running hot or cold
      against what they were due by now, and the commentary folded into the same strip so the
      room reads one thing and not two. Green good, red bad, and the direction is carried
      explicitly rather than read off the sign, because two of these invert (bench regret rising
      is bad; an album rank falling in number is good). Thresholds were set by counting a full
      replayed Sunday, not by taste: 542 lines over 10.9 hours is 0.83 a minute against a strip
      that can show about fifteen
- [x] **Every panel refreshes itself.** Three of them did not: bench regret, playoff odds and who
      is in trouble were rendered once when the page loaded and never again, so a phone left on
      the bar showed two o'clock's numbers at five with nothing on screen to say so. They were
      correct on arrival, which is why no screenshot and no test caught it. Every panel on every
      route now polls its own fragment, the panels are shared templates rather than a copy per
      tab (the Receipts tab had gone on leading every row with a username for months), and
      `tests/test_liveness.py` fails if a panel is added without a trigger
- [x] **Memoise the simulations.** `probabilities_for` was called five times per page render and
      nothing cached it, despite a comment claiming it did since the first commit. With every
      panel now polling, ten phones would have meant ten playoff simulations per poll: 2,500
      seasons each, 175 ms a time. Keyed on the player state that produced the answer rather than
      on the snapshot object, which is rebuilt per request and would have missed every time while
      looking like it worked. A poll window costs one simulation: the first phone pays 335 ms and
      every other one pays 20
- [x] **Follow the week automatically.** The scoring period was always read from ESPN and
      `mSettings` was always cached for a day, so the week rolled over on a Tuesday morning and
      PUNT kept serving the finished Sunday until Wednesday with nothing looking broken. Ten
      minutes now. The live feed empties all six of its per-week stores when the period moves;
      three of them used to be missed, which shows up a fortnight later as last Sunday's
      commentary under this Sunday's scores
- [x] **Record every week.** `data/state/history.sqlite3`, written on every poll and once more at
      the rollover. Not a cache of ESPN, which still has the box scores: it is the half ESPN
      cannot give back, the Moments as they were detected, the commentary that was chosen, and
      what the optimal lineup *was* before anybody edited a roster
- [x] **A week menu in the header.** The live week is the empty value in it, so a URL with no week
      follows the season and a bookmark of week 11 is not still week 11 in December. An archived
      page says so and does not open the stream
- [x] **Rank the album on form, not on the score.** Ordering ten cards by points at three in the
      afternoon mostly ranks managers by how many of their players kicked off at one o'clock
- [x] **Give the Cheer panel a column that varies.** It read `STAKE` on all sixteen rows, which
      distinguished nothing. It is the points still to come out of each fixture now, banded by how
      many head-to-heads that game can actually swing
- [x] **A detail sheet per panel.** Bench regret, who is in trouble, a commentary line, an NFL
      fixture and playoff odds each open something different, because "more about this row" means
      something different in each
- [x] **One page, no tabs.** The tabs were plain links, so every tab change was a new document, and
      audio needs a gesture per document: the tap that changed tabs was the tap that unlocked the
      sound, and the browser destroyed the document mid fade-in. The music could only be heard in
      the gap between the tap and the page changing, which is exactly what it sounded like
- [x] **Real sound.** Fourteen CC0 effects and two music beds from Freesound, replacing fourteen
      synthesised stings that carried under 8% of their energy above 2 kHz. The licence safety moved
      from "we made everything" to a sha256-pinned manifest that both the sprite builder and the
      generated credits read, with `--check` failing the build when they disagree
- [x] **A football for an icon.** The app icon used to be the wordmark set in a webfont fetched
      from the running app, and `icon-192.png` shipped for a week as the brand gradient with no
      lettering on it at all: at that size the capture finished before the font did, and every
      guard passed because a gradient is not a uniform image. It is a drawn path now, so nothing
      can arrive late. The 16 and 32 px favicons are the silhouette alone, measured rather than
      assumed: four lace treatments were rendered and downsampled, and every one that kept a mark
      came out as a white lens with a dark blob in it, which is an eye
- [x] **The sticker album.** Five rarity tiers, foil sheen and gyro tilt on `transform` only (no
      filters, no blend modes: both are frame-rate killers on mobile), and the monogram fallback
      for the managers who never upload a logo
- [x] **PWA shell.** Manifest, service worker, iOS legacy meta tags, generated splash screens,
      safe-area insets. The first deploy precached *unstamped* URLs the page never requests, so the
      offline shell came up with no CSS at all
- [x] **The engine.** Nine Moment kinds, idempotent across a restart; exact optimal lineup by
      maximum-weight bipartite matching, checked against an exhaustive oracle and an independent
      bitmask DP; Monte Carlo win probability with a lumpy per-player distribution; playoff odds
      from 2,500 simulated seasons against the real fixture list, with the magic number read out of
      the same simulation as the odds so the two cannot disagree
- [x] **410 phrase lines**, past the 400 target. What that bought, measured across a full Sunday
      rather than assumed: the repeat rate fell from 68% to 43%, the worst line from 11 uses to 5,
      and the closest repeat moved from 8 Moments apart to 30
- [x] **Every line of the app runs during a simulated Sunday.** `tools/deadcode.py` reports zero,
      and getting there found six features that looked finished and had never once executed
- [x] **Failure states end to end.** Wifi loss, stale banners, the cookie-expiry path, a poll that
      raises, a half-written capture, a full disk and ten concurrent clients against one upstream
      poll are all driven every time `deadcode.py` runs, which is how they stopped being
      hypothetical. The backoff ladder in particular had two real bugs in it and was reachable
      from nothing
- [x] **Deploy to `punt.mdeller.com`**, with a certificate, http2, the shared long-cache snippet on
      the stamped assets, and an entry in the launcher. Three things were wrong and only visible
      once it was serving: every team logo was revalidated on every page load (nginx `add_header`
      APPENDS, so the vhost's `no-cache` joined the logo routes' own `max-age` and won); the
      offline shell precached URLs the page never requests; and `/api/diagnostics` published the
      last four characters of a live session cookie
- [x] **Self-host the fonts.** A third-party font origin costs a second of first paint on exactly
      the shared wifi this app is designed for. Measured afterwards: 142 kB of faces actually
      fetched, down from 190 once `b, strong` was styled to 600 and the unused 700 weight stopped
      shipping
- [x] **Settle the superflex question.** No superflex and no QB-accepting flex, so the eligibility
      bug was latent and never live. Still worth having fixed, because the answer lives in ESPN's
      settings where a commissioner can change it between seasons, and the app reads it now rather
      than remembering it

### Outstanding

- [ ] **A season that has happened.** The eight panels above are built and live, but five of them
      read the settled weeks and the real league has one. They will say something by about
      November and very little before then. That is the data's fault, not the panels'
- [ ] **Reconcile bench regret against two real ESPN box scores.** By hand, against a week whose
      numbers are known. The test exists and is skipped, naming why
- [ ] **The recap's model path.** The fact pack, the validator and the retry loop are driven against
      stub backends on every `deadcode.py` run, so the logic is exercised; only a real local model's
      output is not. `ollama pull qwen2.5:1.5b-instruct`
- [ ] **Install Piper on the droplet.** It is the shipping TTS backend; macOS `say` stands in
      locally so the pipeline is testable end to end, but it is not on the server
- [ ] **The Big Board carousel.** Auto-rotating matchups, which is what makes the larger TV type
      scale workable: at 2.2x a 720p screen fits two and a half of five matchups, so it currently
      runs at 1.6
- [ ] **Real-device matrix.** Simulators do not reproduce the iOS audio-session or gyroscope
      permission behaviour, which is precisely where this app is most fragile. The 60 fps card
      target and the mute toggle both need a real iPhone
- [ ] **Decide whether real recordings may ever be committed.** `.gitignore` tracks only `demo-*`,
      on the assumption that ten managers' ESPN display names should not be in a public repository

## ⚠️ Known risks

| Risk | Mitigation |
|---|---|
| ESPN changes hostnames or view names mid-season | Every endpoint in one module, per-panel degradation, stale banners |
| `espn_s2` expires on a Sunday | Cached data continues serving, commissioner banner, admin refresh route |
| Audio silently fails on someone's phone | The visual path is always complete; nothing is audio-only |
| Commentary repetition kills the joke by week 3 | Cooldowns, a 410-line bank, weighted sampling |
| Banter lands badly on a real person | `ROAST_LEVEL`, managers only and never players, lowerable mid-season |
| A recap invents a statistic | Numeral and proper-noun validator, templated fallback |
| Ten clients hammer ESPN | One shared cache, one upstream poll regardless of client count |
| A manager uploads a broken logo, or renames to 40 characters | Server-side image proxy, monogram fallback, truncation at the measured width |
| A second gunicorn worker | The poller, the dedupe set and the Moment buffer are per-process: a second worker doubles the upstream poll rate and gives half the room a different commentary feed. One worker with threads, enforced by a test |

## 📄 Licence

MIT. See [LICENSE](LICENSE).

Audio assets carry their own licences, generated into
[`static/audio/LICENCES.md`](static/audio/LICENCES.md) from the manifest: the effects are CC0, and
the Sunday-night bed is CC-BY 4.0 and is credited in the page footer where it is heard.

PUNT is not affiliated with, endorsed by, or connected to ESPN or the National Football League. It
reads ESPN's undocumented fantasy endpoints with a league member's own credentials, at a polling
rate floored at 30 seconds, and is read-only.

---

## 👤 Author

**Marc C. Deller, D.Phil.**  
Structural biologist & drug discovery scientist  

<table>
<tr>
<td>🌐</td><td><a href="https://marcdeller.com" target="_blank" rel="noopener noreferrer">marcdeller.com</a></td>
<td>✉️</td><td><a href="mailto:marc@marcdeller.com">marc@marcdeller.com</a></td>
<td>🐙</td><td><a href="https://github.com/bellcheddar/PUNT" target="_blank" rel="noopener noreferrer">github.com/bellcheddar/PUNT</a></td>
</tr>
</table>
