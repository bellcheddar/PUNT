# PUNT — Build Specification v1

**P**lay-by-play, **U**proar, **N**umbers & **T**rash-talk

A mobile-first fantasy football companion app for a ten-team bar league, built on ESPN's undocumented
fantasy API. Sticker-album visual identity, heavy on audio, animation and corny commentary. Hosted as a
Flask app at `punt.mdeller.com`.

**Author:** Marc C. Deller, D.Phil. · marc@marcdeller.com

---

## 1. Product intent

The ESPN app is a spreadsheet with a logo. PUNT is the opposite: it exists to make a Sunday afternoon in a
bar louder. It should feel like a broadcast, a card pack and a DJ set at once. Every design decision resolves in
favour of the room, not the individual.

**Primary use case:** ten people, one bar, ten phones, one shared TV, four hours.

### Design principles

| Principle | Consequence |
|---|---|
| Phone first, always | Every screen designed at 390 px wide before anything else exists |
| Loud by default | Audio on unless muted, not off unless enabled (behind a one-tap unlock gate) |
| Zero configuration | No settings screen. Anything configurable is a house rule, set once by the commissioner |
| Never a blank screen | Every panel has a loading, empty and stale state with a human-readable message |
| Roast the manager, not the player | Banter targets league members and their decisions, never real athletes |
| Indistinguishable from a native app | Installed to the home screen, full-bleed, no browser chrome, no page-reload flashes, gesture-correct on both platforms |
| Parity across iOS and Android | A feature that only works on one platform is not shipped as a feature, it is shipped as an enhancement with a working fallback |

### Non-goals

- Not a replacement for ESPN's roster management. Lineups are still set in ESPN. PUNT is read-only.
- No user accounts. One league, one shared instance, access by URL. Manager identity is chosen once and kept in `localStorage`.
- No betting, no odds markets, no money handling. Friendly fines are displayed as text only, never transacted.

---

## 2. Stack and constraints

| Layer | Choice | Rationale |
|---|---|---|
| Backend | Flask (Python 3.11+), blueprint per tab | Matches CODSWALLOP conventions on the mdeller box |
| Templating | Jinja2, server-rendered | First paint is HTML, no hydration cost on mobile |
| Liveness | htmx polling (30 s) plus SSE for instant events | No front-end framework, no build step |
| Audio | Howler.js over Web Audio API | Sprite support, mobile unlock handling, per-bus volume |
| Animation | CSS transforms and Web Animations API | GPU-composited only: `transform` and `opacity`, never `top`/`left`/`width` |
| Charts | Chart.js, lazy-loaded only on tabs that need it | Plotly is too heavy for this app |
| Text generation | Deterministic phrase bank (live) plus small local instruct model (weekly) | See section 6 |
| TTS | Piper (MIT licence, CPU, sub-second) | Self-hosted, no per-call cost, two voices |
| Cache | In-process TTL cache, Redis only if a second worker is added | Ten phones must produce one upstream poll, not ten |

**Hard constraints**

- No React, Vue, Angular, npm build step or webpack.
- Total JS payload under 150 kB gzipped excluding audio.
- First contentful paint under 1.5 s on 4G.
- Audio assets lazy-loaded after first paint, never blocking.

---

## 3. Repository layout

```
punt/
├── app.py                      # factory, blueprint registration
├── config.py                   # env-driven config, no secrets in source
├── espn/
│   ├── client.py               # cookie-authenticated fetch layer
│   ├── cache.py                # TTL cache, single upstream poll
│   ├── models.py               # dataclasses: Team, Player, Matchup, Slot
│   └── replay.py               # recorded-Sunday playback for development
├── engine/
│   ├── events.py               # poll diffing to discrete Moments
│   ├── scoring.py              # optimal lineup, bench regret, all-play, luck
│   ├── simulate.py             # Monte Carlo win probability and playoff odds
│   ├── commentary.py           # phrase-bank retrieval and slot filling
│   ├── factpack.py             # structured week facts for the weekly recap
│   └── recap.py                # local model call plus grounding validator
├── data/
│   ├── phrases/                # YAML phrase banks, one file per category
│   ├── factpack_schema.json
│   └── recordings/             # captured API payloads for replay
├── static/
│   ├── css/                    # theme variables plus components
│   ├── js/                     # audio.js, cards.js, tilt.js, live.js
│   └── audio/                  # sprites plus stings, all licence-cleared
├── templates/
│   ├── base.html
│   ├── tabs/                   # one per route
│   └── partials/               # htmx-swappable fragments
└── tests/
```

---

## 4. Data layer

### 4.1 ESPN client

Cookies live server-side only, read from environment. A browser cannot send them cross-origin, so the Flask app
is the only thing that ever holds them.

```python
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"
LEAGUE = f"{BASE}/seasons/{{season}}/segments/0/leagues/{{league_id}}"
NFL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
```

| Feed | Call | Cache TTL | Purpose |
|---|---|---|---|
| League settings | `?view=mSettings` | Season | Scoring items, playoff seeds, tiebreakers, roster slots |
| Schedule and teams | `?view=mSchedule&view=mTeam` | 1 hour | Season grid for all-play, luck, simulations |
| Live scoring | `?view=mMatchupScore&view=mBoxscore&scoringPeriodId=N` | 30 s | Per-slot player points, projected remainder |
| Team identity | `?view=mTeam` (plus `view=mSettings` for members) | 6 hours | Team names, abbreviations, uploaded logo URLs, owner display names, division |
| Rosters | `?view=mRoster` | 5 min (30 s when live) | Starters vs bench, the basis of bench regret |
| Player universe | `?view=kona_player_info` + `x-fantasy-filter` header | 15 min | Projections, injury flags, ownership |
| NFL game state | `NFL` scoreboard, no auth | 20 s | Possession, down and distance, red zone, clock |

**Rules**

- Poll at 30 s minimum. Never faster, even in development.
- Exponential backoff on any non-200, capped at 5 minutes, with the UI switching to a stale banner rather than an error.
- Every response is validated against the dataclasses in `models.py`. ESPN changes shapes without notice; a missing key must degrade one panel, never crash a request.
- `ESPN_S2` rotates. On a 401, log loudly, serve cached data, and surface a commissioner-only banner saying the cookies need refreshing.

### 4.2 Replay harness (build this first)

Development happens on Tuesdays when nothing is live. `espn/replay.py` must:

- Record every upstream response to `data/recordings/{date}/{seq}_{view}.json` when `RECORD=1`.
- Replay a recorded Sunday at configurable speed (`REPLAY=2024-11-17 REPLAY_SPEED=60`) through the identical client interface.
- Ship with at least one full recorded game day committed to the repo so a fresh clone can run the whole app with no cookies at all.

Without this, nothing downstream of section 5 is testable. It is the first milestone for a reason.

---

### 4.3 Team identity comes from ESPN, always

There is no manager list in this repo. Every name, logo and abbreviation on a card is resolved at runtime from
`mTeam`, so a mid-season team rename or a new logo upload appears in PUNT without a deploy.

- `teams[].name` and `teams[].abbrev` drive card and header text. Treat both as user-generated: they can be
  empty, emoji-only, 40 characters long, or contain markup. Sanitise, then truncate with an ellipsis at the
  card's measured width rather than a fixed character count.
- `teams[].logo` is a URL to a user-uploaded image on ESPN's CDN. It can be missing, a broken link, a hotlink
  ESPN blocks, or a wildly wrong aspect ratio. Proxy it through `/img/team/<id>` with server-side fetch, cache
  and normalisation to a square, and fall back to a generated monogram tile in the manager's assigned card
  colour. The fallback will be used by at least two managers all season, so it needs to look intentional.
- Owner display names come from the members block in `mSettings`, joined to teams by `owners[]` GUID. Where a
  team has multiple owners, use the first and note the rest on the manager profile.
- **Assign card colours deterministically** from the team ID hash so a given team keeps its palette all season
  across every device, with no stored state.
- Ten teams means the album grid is a 2x5 on phones and a 5x2 on the TV view. Both fit without scrolling, which
  is worth protecting: no pagination, no virtualisation needed anywhere in the album.

The only identity the app stores locally is *which* of the ten teams the current phone belongs to, chosen once
on first run and kept in `localStorage`.

---

## 5. Event model

The core abstraction. Polls produce state; the app needs **Moments**.

```python
@dataclass
class Moment:
    id: str                 # stable hash, prevents duplicate firing
    kind: str               # TOUCHDOWN | BIG_PLAY | LEAD_CHANGE | MILESTONE |
                            # BENCH_DISASTER | INJURY | DOOM | CLINCH | GOOSE_EGG
    magnitude: float        # 0-1, drives audio and animation intensity
    managers: list[str]     # who this affects
    player: str | None
    delta_points: float
    win_prob_delta: float   # signed, from simulate.py
    context: dict           # down, distance, quarter, clock, opponent
    ts: datetime
```

`engine/events.py` diffs consecutive polls and emits Moments. Requirements:

- **Idempotent.** The same underlying play must never emit twice, even across a restart. Dedupe on `Moment.id`.
- **Magnitude-scaled.** A 2-point reception and a 60-yard touchdown must not get the same horn.
- **Bench-aware.** `BENCH_DISASTER` fires when a benched player outscores the starter in that slot by a configurable margin. This is the funniest event in the app and needs its own detection path.
- **GOOSE_EGG** fires at the end of a game window for any starter finishing on zero.
- **DOOM** fires when a manager's win probability drops below 5% while their opponent still has players active.

Moments feed three consumers simultaneously: the SSE stream, the commentary engine, and the audio bus.

---

## 6. Commentary engine

The requirement is retrieval-only text generation with no training. Split by latency budget:

| Surface | Budget | Mechanism |
|---|---|---|
| Live play call | Under 100 ms | Deterministic phrase bank, slot-filled. No model in the path. |
| Between-play banter | Under 500 ms | Same bank, lower-priority queue |
| Weekly recap | Up to 10 s, once a week | Small local instruct model over a retrieved fact pack |

A model in the live path would be both too slow and prone to inventing scores. The bank is not a compromise here,
it is the correct engineering answer: instant, free, and incapable of hallucinating a number.

### 6.1 Phrase bank schema

YAML in `data/phrases/`, one file per category (`touchdown.yaml`, `bench.yaml`, `doom.yaml`, `filler.yaml`).

```yaml
- id: td_rush_short
  trigger:
    kind: TOUCHDOWN
    play_type: rush
    yards: {lte: 3}
  tone: [hype, corny]
  roast_level: 0          # 0 = safe, 1 = teasing, 2 = savage
  weight: 3
  cooldown: 900           # seconds before this line can repeat
  text: "{{player}} from {{yards}} out! {{manager}}, you may now breathe."
  audio: horn_01
  voice: pbp              # pbp | colour
```

**Selection algorithm:** filter by trigger match, drop anything inside its cooldown, drop anything above the
league's configured `roast_level`, then weighted-random sample. Seed the RNG per `(week, moment.id)` so the same
Sunday replays identically in tests.

**Volume target:** at least 400 lines across categories before launch. Fewer than that and the repetition becomes
obvious inside one afternoon. Include a `filler` category for dead air between plays, weighted low.

### 6.2 Weekly recap

Retrieval-only, grounded, no fine-tuning.

1. `factpack.py` assembles a structured JSON object of the week: final scores, optimal-lineup deltas, bench
   regret, all-play records, luck index, biggest single-play swing, goose eggs, standings movement.
2. Retrieve the top *k* stylistic exemplars from the phrase corpus matching the week's dominant story shape
   (blowout, nail-biter, upset, collapse).
3. Prompt a small local instruct model. Suggested: Qwen2.5-1.5B-Instruct or Llama-3.2-3B-Instruct, served through
   MLX on Apple silicon or llama.cpp GGUF on the server. Temperature 0.85, max 200 tokens.
4. **Validator, non-negotiable:** every numeral in the output must appear in the fact pack. Every proper noun must
   appear in the fact pack. On failure, regenerate up to three times, then fall back to a fully templated recap.
   Log every rejection so the prompt can be tuned.

The validator is what makes this safe to run unattended. A recap that invents a score is worse than no recap,
because the league will believe it.

### 6.3 Voice

Piper TTS, two voices: `pbp` (fast, higher energy) for play calls and `colour` (slower, drier) for banter.
Synthesise on the server, cache by phrase hash so repeated lines cost nothing after the first use. Pre-synthesise
the entire bank at deploy time as a build step, which makes live playback a static file fetch.

---

## 7. Audio system

Audio is a first-class subsystem, not a decoration. It is also where mobile browsers are most hostile.

### 7.1 Buses

| Bus | Default | Ducks under | Contents |
|---|---|---|---|
| `music` | 0.35 | commentary, stings | Loop bed, countdown tracks |
| `stings` | 0.8 | commentary | Horns, airhorns, record scratches, sad trombone |
| `commentary` | 1.0 | nothing | TTS play calls and banter |
| `ui` | 0.5 | commentary | Taps, card flips, pack rips |

Ducking: on a commentary trigger, ramp `music` to 0.12 over 200 ms, hold, ramp back over 600 ms after the clip
ends. This one behaviour is most of what makes it sound produced rather than noisy.

### 7.2 Mobile gotchas (all must be handled)

- **iOS requires a user gesture** to unlock `AudioContext`. Ship a full-screen splash: *TAP TO KICK OFF*. Unlock the context and start the music bed inside that handler.
- **The iOS ringer switch mutes HTML5 audio.** Web Audio with the playback category survives it. Use Howler's `html5: false` for anything that must be heard, and detect a silent context by checking for a zero-output analyser after unlock, then prompt the user.
- **Autoplay is blocked everywhere.** Never assume a sound played. Every audio call is fire-and-forget with a failure log, never awaited in a UI path.
- **Backgrounded tabs throttle timers.** Use SSE plus `visibilitychange` to reconcile on return rather than trusting `setInterval`.
- Preload as a single sprite per bus. Twenty separate `.mp3` fetches on a bar's shared wifi will not work.
- Respect `prefers-reduced-motion` for animation and provide an obvious persistent mute toggle in the header regardless.

### 7.3 Asset licensing (read this before sourcing anything)

The actual broadcast themes for Sunday and Monday night football are copyrighted compositions owned by the
networks. **Do not use them, do not source them from a rip, and do not let a contributor drop one in.** The same
applies to team fight songs, stadium anthem recordings and any commercially released track.

Source instead from:

- Commissioned or self-made original stings in that brass-and-timpani idiom, which is a genre convention rather than a protected work.
- CC0 and public-domain libraries (Freesound CC0 filter, Pixabay Audio) for horns, crowd beds, whistles, record scratches, buzzers.
- Royalty-free production music with an explicit licence file committed to `static/audio/LICENCES.md` naming each asset, its source URL and its licence.

Every asset in the repo needs a line in that file. No exceptions, no orphan files.

---

## 8. Visual system: the sticker album

### 8.1 Card anatomy

Every manager is a collectible card, minted weekly. Card front carries: manager name, team name, week score,
record, luck index, bench regret, and a rarity treatment.

| Tier | Trigger | Treatment |
|---|---|---|
| Common | Default | Matte gradient, no sheen |
| Rare | Top-3 score that week | Foil sweep, subtle |
| Epic | Highest score that week | Animated foil, particle edge |
| Legendary | Season-high score, or a win from under 10% win probability | Full holographic, confetti on reveal, permanent album slot |
| Cursed | Lowest score, or bench regret over 40 | Desaturated, cracked-foil overlay, sad trombone on flip |

Cursed cards are the point. They must look as considered as Legendary ones.

**Wordmark:** four letters is short enough to sit in the header at full size on a 390 px screen without
truncation, and short enough to run vertically down the card edge as foil, the way a trading-card brand mark
does. Card backs carry the wordmark and nothing else. Commission it from the vibe-icon skill once the card
geometry is locked, not before.

### 8.2 Effects

- **Foil sheen:** one rotated linear-gradient pseudo-element, translated on `transform` only. No filters, no blend modes on mobile, both are frame-rate killers.
- **Gyro tilt:** `DeviceOrientationEvent`. On iOS 13+ this requires `DeviceOrientationEvent.requestPermission()` called from inside a user gesture. Chain it to the same *TAP TO KICK OFF* handler as the audio unlock so the user grants both at once. Fall back to pointer-move on desktop and to no tilt if permission is refused.
- **Pack rip:** the weekly reveal is a pack-opening animation, cards flipping one at a time with a rip sound, lowest score revealed last. This is the emotional centre of the Monday visit and deserves the most polish.
- **Countdown moments:** when a manager's player is inside the five yard line, the app runs a DJ-style countdown overlay: beat-matched pulse, rising riser sound, then either a horn or a record scratch depending on the outcome. Triggered off red-zone state from the NFL scoreboard feed, cancelled cleanly if the drive ends without a score.
- **Haptics:** `navigator.vibrate()` on Android for scoring moments. Unsupported in iOS Safari, so treat as progressive enhancement and never rely on it.

Performance budget: 60 fps on an iPhone 12. Animate `transform` and `opacity` only. Any card list over six items
virtualises or paginates.

---

### 8.3 Making a browser page behave like an installed app

The app ships as a PWA. iPhone Safari and Android Chrome are the two targets and they diverge in ways that will
cost a day each if discovered late.

**Install and shell**

- `manifest.json` with `display: standalone`, `orientation: portrait`, `theme_color`, `background_color`, and
  512/192 px maskable icons. Android Chrome will then offer a genuine install prompt.
- iOS ignores most of the manifest. It needs the legacy meta tags as well: `apple-mobile-web-app-capable`,
  `apple-mobile-web-app-status-bar-style=black-translucent`, `apple-touch-icon`, and generated
  `apple-touch-startup-image` splash screens per device size. Without these, an iPhone "install" opens a
  Safari tab with a URL bar and the illusion dies immediately.
- iOS gives no `beforeinstallprompt` event, so ship a one-time in-app card explaining Share then Add to Home
  Screen, shown only to iOS Safari visitors who are not already in standalone mode
  (`navigator.standalone === false`).
- Service worker caches the app shell, CSS, JS, fonts and audio sprites with a cache-first strategy, and passes
  all `/api` and `/partials` requests straight through network-first. Fantasy data must never be served stale
  from a service worker: the TTL cache on the server is the only cache allowed to hold scores.

**Viewport and safe areas**

- `<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">`.
- Use `100dvh`, never `100vh`. On both platforms the address bar collapse changes `vh` mid-scroll and produces a
  visible jump, which is the single most obvious tell that something is a web page.
- Pad the bottom tab bar with `env(safe-area-inset-bottom)` for the iPhone home indicator, and the header with
  `env(safe-area-inset-top)` for notches and punch-holes. Android gesture navigation needs the same bottom
  inset.
- `theme-color` meta set to the header colour so the Android status bar matches rather than sitting as a
  white stripe.

**Gestures and touch**

- `overscroll-behavior: none` on the body to kill Chrome's pull-to-refresh, which will otherwise reload the app
  every time someone swipes down on the album.
- `-webkit-tap-highlight-color: transparent`, `user-select: none` on all chrome, `touch-action: manipulation`
  to remove the 300 ms double-tap-zoom delay.
- Minimum 44x44 px touch targets, bottom tab bar in the thumb zone, nothing interactive in the top corners.
- Swipe left and right between tabs as an enhancement, with the tab bar always available as the primary path.
- `position: fixed` behaves badly on iOS when the keyboard opens. There is no text input in this app, which
  neatly avoids the entire problem: keep it that way.

**Platform divergences to handle explicitly**

| Capability | iOS Safari | Android Chrome | Handling |
|---|---|---|---|
| Audio unlock | Gesture required, ringer switch mutes HTML5 audio | Gesture required | Shared unlock gate, Web Audio path, see 7.2 |
| Gyroscope tilt | `requestPermission()` from a gesture, HTTPS only | Works without prompt | Chain the iOS prompt to the unlock gate, pointer fallback |
| Haptics | `navigator.vibrate` unsupported | Supported | Progressive enhancement, never load-bearing |
| Install prompt | Manual, Share menu | `beforeinstallprompt` | Platform-detected instructions |
| Fullscreen API | Unsupported on iPhone | Supported | TV mode relies on standalone display, not Fullscreen API |
| Back gesture | Edge swipe navigates history | System back button | Routes must be real URLs so back behaves sanely |

---

## 9. Routes and screens

All routes render mobile-first. `?tv=1` on any route drops navigation, scales typography roughly 2.2x and disables
interaction, for the bar screen.

| Route | Tab | Contents |
|---|---|---|
| `/` | Today | Your matchup card pair, live delta, next thing to watch, commentary feed |
| `/album` | Album | All ten manager cards this week, pack-rip reveal, season album by week |
| `/cheer` | Cheer | Per live NFL game: CHEER / BOO / CONFLICTED verdict with a one-sentence reason |
| `/swing` | Swing | Live win probability curve, biggest swing of the day, gut-punch leaderboard |
| `/receipts` | Receipts | Bench regret, all-play table, luck index, weekly awards, fines ledger |
| `/multiverse` | Multiverse | Playoff odds, magic numbers, scenario explorer |
| `/big-board` | (TV) | Auto-rotating matchup carousel, watch-now rail, full-screen scoring takeover |

**Navigation:** fixed bottom tab bar, five items maximum, thumb-reachable, 44 px minimum touch targets. Multiverse
folds under Receipts if six proves too many.

### API and stream routes

```
GET  /api/state                     # current league snapshot, JSON
GET  /partials/matchup/<id>         # htmx fragment, hx-trigger="every 30s"
GET  /partials/album                # htmx fragment
GET  /stream                        # SSE: Moments pushed as they are detected
GET  /audio/phrase/<hash>.mp3       # pre-synthesised TTS, immutable, long cache
POST /admin/refresh-cookies         # commissioner only, env-gated token
```

---

## 10. Configuration

```
LEAGUE_ID=          SEASON=            ESPN_S2=          ESPN_SWID=
ROAST_LEVEL=1       # 0 safe, 1 teasing, 2 savage
POLL_SECONDS=30     RECORD=0           REPLAY=            REPLAY_SPEED=1
RECAP_MODEL=qwen2.5-1.5b-instruct     RECAP_BACKEND=mlx  # mlx | llamacpp
```

No secret is ever committed, logged, or rendered into a template. Add a startup assertion that fails loudly if
`ESPN_S2` appears anywhere in the static directory.

---

## 11. Build phases

Each phase ends in something demonstrable. Do not start a phase before its predecessor's acceptance criteria pass.

### Phase 1 — Skeleton and replay
ESPN client, dataclasses, TTL cache, recorder, replay harness, Flask skeleton with all routes returning stub
templates.
**Done when:** a full recorded Sunday replays at 60x speed with no network access and no cookies.

### Phase 2 — Engine
Event detection with dedupe, scoring maths (optimal lineup, bench regret, all-play, luck), Monte Carlo win
probability.
**Done when:** replaying a recorded Sunday emits a plausible Moment timeline, and bench regret figures reconcile
by hand against the ESPN box score for two known weeks.

### Phase 3 — Sticker album, silent
Card component with all five rarity tiers, album grid, pack-rip animation, gyro tilt with permission flow, bottom
tab bar. PWA shell: manifest, service worker, iOS meta tags and splash screens, safe-area insets, overscroll and
tap-highlight suppression. Team names and logos resolved live from ESPN with the monogram fallback. No audio yet.
**Done when:** 60 fps on a real iPhone and a mid-range Android with ten cards on screen, and the pack rip is genuinely satisfying.

### Phase 4 — Audio and commentary
Phrase bank at 400+ lines, selection algorithm, Piper synthesis build step, Howler buses, ducking, unlock gate,
countdown overlay.
**Done when:** a 60x replay produces a coherent audio track with no clipping, no overlap, no repeated line inside
its cooldown, and the mute toggle works instantly on iOS.

### Phase 5 — Recap and remaining tabs
Fact pack, local model recap with validator, Cheer, Swing, Multiverse tabs, TV mode.
**Done when:** three consecutive weekly recaps generate with zero validator rejections reaching the user, and
`?tv=1` is readable from twelve feet.

### Phase 6 — Bar hardening
Wifi failure states, stale banners, cookie-expiry path, ten concurrent clients against one upstream poll,
licence audit of every audio asset.
**Done when:** pulling the network cable mid-Sunday degrades gracefully on every tab and recovers without a reload.

---

## 12. Testing

- **Replay-driven integration tests** are the backbone. Assert Moment timelines and commentary output against
  committed recordings with a fixed RNG seed.
- **Golden-file commentary tests:** a recorded week produces a byte-identical commentary transcript. Any phrase
  bank edit that changes output must update the golden file deliberately.
- **Grounding tests for the recap:** assert the validator rejects a deliberately corrupted fact pack.
- **Scoring maths** unit-tested against hand-calculated ESPN box scores, including flex eligibility edge cases.
- **Mobile smoke test on real hardware** before every release. Simulators do not reproduce the iOS audio or
  gyroscope permission behaviour, which is precisely where this app is most fragile.

### Device matrix (minimum before each release)

| Device class | Browser | Must verify |
|---|---|---|
| iPhone, recent | Safari, installed to home screen | Audio unlock, ringer switch, gyro permission, safe-area insets, splash screen |
| iPhone, older or SE | Safari | 60 fps on the pack rip, `100dvh` behaviour, album fits without scroll |
| Android, flagship | Chrome, installed | Install prompt, theme-color, back button, haptics |
| Android, mid-range | Chrome | Frame rate under load, audio sprite decode time, gesture-nav bottom inset |
| Android, Samsung | Samsung Internet | Rendering divergences, service worker behaviour |
| Desktop | Chrome and Safari | TV mode at `?tv=1`, pointer tilt fallback |

Ten managers means realistically five or six distinct devices in the room. Ask the league what they carry and
test on those specifically rather than on an abstract matrix.

---

## 13. Known risks

| Risk | Mitigation |
|---|---|
| ESPN changes hostnames or view names mid-season | All endpoints in one module, per-panel degradation, stale banners |
| `espn_s2` expires on a Sunday | Cached data continues serving, commissioner banner, admin refresh route |
| Audio silently fails on someone's phone | Visual-only path is always complete; nothing is audio-only |
| Commentary repetition kills the joke by week 3 | Cooldowns, 400-line minimum, weighted sampling, seasonal bank additions |
| Banter lands badly on a real person | `ROAST_LEVEL` config, managers only, never players, commissioner can lower it mid-season |
| Recap invents a statistic | Numeral and proper-noun validator, templated fallback |
| Ten clients hammer ESPN | Single shared cache, one upstream poll regardless of client count |
| A feature works on Android and not iOS, or vice versa | Divergence table in 8.3, fallback required before a capability ships, real-device matrix each release |
| A manager uploads a broken or absurd logo, or renames to 40 characters | Server-side image proxy with normalisation, monogram fallback, measured truncation |

---

*Built by Marc C. Deller, D.Phil. · marcdeller.com · marc@marcdeller.com*
