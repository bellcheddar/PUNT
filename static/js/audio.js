/* The audio bus.
 *
 * Four buses with fixed relative levels, one sprite, and a duck. The ducking is
 * most of what makes it sound produced rather than merely loud: without it a
 * horn and a play call arrive on top of each other and the room hears neither.
 *
 * Mobile browsers are hostile here in specific, documented ways, and every one
 * of them is handled below rather than hoped about:
 *
 *   - No browser plays sound before a tap, click or key press unless it has
 *     decided to allow auto-play, and iOS never does. The sprite loads at once,
 *     the theme starts the moment the AudioContext runs, and until then a
 *     prompt asks for the gesture. See `start`.
 *   - The iOS ringer switch mutes HTML5 audio but not Web Audio, so every Howl
 *     is created with `html5: false`.
 *   - Autoplay is blocked everywhere, so every play is fire-and-forget. Nothing
 *     in a UI path ever waits on a sound.
 *   - Twenty separate fetches on a bar's shared wifi will not work, so it is one
 *     sprite.
 */

(() => {
  'use strict';

  const MUTE_KEY = 'punt.muted';

  /* Levels, in the spec's proportions but scaled for headroom. `music` ducks
   * under commentary and stings, `stings` ducks under commentary, `commentary`
   * ducks under nothing.
   *
   * The scaling is not cosmetic. Web Audio hard-clips at the destination, and
   * the spec's nominal levels (music 0.35, stings 0.80, commentary 1.00) sum to
   * 1.42 even with music fully ducked -- and a sting and a play call genuinely do
   * overlap, because the play call is started 260 ms into a 600 ms horn on
   * purpose. These are set so the worst simultaneous case lands exactly at 1.0,
   * which tests/test_audio_levels.py asserts. Turning the phone up is free;
   * clipping is not.
   */
  const BUSES = {
    music: { volume: 0.26, ducked: 0.07 },
    stings: { volume: 0.58, ducked: 0.11 },
    commentary: { volume: 0.78, ducked: 0.78 },
    ui: { volume: 0.36, ducked: 0.04 },
  };

  const DUCK_DOWN_MS = 200;
  const DUCK_UP_MS = 600;

  let sprite = null;
  let howl = null;
  let bed = null;
  let unlocked = false;
  let muted = readMuted();
  let ducking = 0;          // how many things are currently talking over the top
  let duckTimer = null;

  function readMuted() {
    try { return localStorage.getItem(MUTE_KEY) === '1'; } catch { return false; }
  }

  function writeMuted(value) {
    try { localStorage.setItem(MUTE_KEY, value ? '1' : '0'); } catch { /* ignore */ }
  }

  // --- loading -------------------------------------------------------------

  async function load() {
    if (howl || !window.Howl) return;
    try {
      const response = await fetch('/static/audio/sprite.json');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      sprite = await response.json();
    } catch (error) {
      console.info('no audio sprite:', error.message);
      return;
    }

    howl = new window.Howl({
      src: ['/static/audio/sprite.mp3', '/static/audio/sprite.ogg'],
      sprite: sprite.sprite,
      // Web Audio, not an HTML5 element: the iOS ringer switch silences the
      // element path and leaves Web Audio alone, and the ringer switch is on in
      // a bar roughly always.
      html5: false,
      preload: true,
      volume: 1.0,
    });

    howl.once('loaderror', (_id, error) => {
      console.info('audio sprite failed to decode:', error);
      howl = null;
    });

    // The bed is a separate file rather than a sprite region: Howler loops a
    // whole file cleanly and loops a region with an audible gap at the seek.
    //
    // The orchestral Sunday-night theme everywhere, on the wall and in the hand.
    // It used to be the 128 bpm watch-party loop on a phone, on the reasoning
    // that brass across a room and brass six inches from your ear are different
    // propositions -- which is true of a LOOP, and this does not loop. It plays
    // once. One pass of the theme is the thing that says the afternoon has
    // started, and that is worth the same on both.
    //
    // `bed-party` is still built, still credited and still shipped: it is what
    // the second one is for, and the switch back is this line.
    const stem = 'bed-epic';
    // Once, not on a loop. It is a theme, and a theme that comes round again
    // every forty-five seconds for four hours stops being a theme and becomes a
    // thing people ask you to turn off. The stings and the commentary carry the
    // afternoon after it has played.
    bed = new window.Howl({
      src: [`/static/audio/${stem}.mp3`, `/static/audio/${stem}.ogg`],
      html5: false,
      loop: false,
      volume: 0,
    });
  }

  // --- ducking -------------------------------------------------------------

  /* Ramped rather than stepped. A hard volume change on a music bed is audible
   * as a click; 200 ms down and 600 ms up is the shape that reads as a mix
   * decision rather than as a fault. */
  function rampMusic(target, ms) {
    if (!bed || musicId === null) return;
    bed.fade(bed.volume(musicId), target * (muted ? 0 : 1), ms, musicId);
  }

  function duck() {
    ducking += 1;
    clearTimeout(duckTimer);
    rampMusic(BUSES.music.ducked, DUCK_DOWN_MS);
  }

  function unduck(afterMs) {
    duckTimer = setTimeout(() => {
      ducking = Math.max(0, ducking - 1);
      if (ducking === 0) rampMusic(BUSES.music.volume, DUCK_UP_MS);
    }, Math.max(0, afterMs));
  }

  // --- playing -------------------------------------------------------------

  let musicId = null;

  /* Every call is fire-and-forget and returns nothing useful on purpose. A UI
   * path that awaits a sound is a UI path that stalls when the sound is blocked,
   * and on a phone something is always blocked. */
  function play(name, { magnitude = 1, bus = null } = {}) {
    if (!howl || muted || !unlocked || !name) return;
    if (!sprite.sprite[name]) return;

    const which = bus || sprite.buses[name] || 'stings';
    const profile = BUSES[which] || BUSES.stings;
    // Stings duck under commentary too, not just music. Without this the horn
    // and the play call fight and the room hears neither.
    const level = speaking ? profile.ducked : profile.volume;
    try {
      // Magnitude scales within the bus, never across it: a small touchdown is
      // quieter than a big one, and neither is ever louder than a play call.
      const id = howl.play(name);
      howl.volume(level * Math.max(0.25, Math.min(1, magnitude)), id);
      if (which === 'stings' || which === 'commentary') {
        duck();
        unduck(sprite.sprite[name][1] + 120);
      }
    } catch (error) {
      console.info('play failed:', error.message);
    }
  }

  // --- starting ------------------------------------------------------------

  /* Browsers will not play sound until the page has had a tap, a click or a key
   * press, and iOS has no exceptions at all. No page can get round that. What a
   * page CAN do is not wait for a gesture it does not need, and say so when it
   * does.
   *
   * The theme used to start only inside the first tap's handler, so it never
   * played on its own anywhere -- including the places that allow it: desktop
   * Chrome on a site somebody visits often, Safari set to allow auto-play, an
   * installed app. And where a tap was needed, nothing on screen said so, which
   * reads as "the intro music is broken".
   *
   * So the sprite loads at once, and the sound starts the moment the browser's
   * AudioContext is running: straight away where auto-play is allowed, on the
   * first gesture where it is not, with a prompt asking for that gesture in the
   * meantime. `unlocked` stays false until the context really runs, so nothing
   * queues up while it is blocked -- a horn started into a suspended context
   * plays when it resumes, and a first tap would release a backlog of them.
   */
  const steady = new URLSearchParams(location.search).get('punt') === 'steady';
  let starting = null;
  let prompt = null;

  function context() {
    return window.Howler && window.Howler.ctx;
  }

  async function start() {
    if (!starting) {
      starting = (async () => {
        await load();
        const ctx = context();
        if (!howl || !ctx) return;
        if (ctx.state === 'running') {
          begin();
          return;
        }
        ctx.addEventListener('statechange', () => {
          if (ctx.state === 'running') begin();
        });
        document.documentElement.dataset.audio = muted ? 'muted' : 'locked';
        showPrompt();
      })();
    }
    return starting;
  }

  function begin() {
    if (unlocked) return;
    unlocked = true;
    hidePrompt();
    // Faded in rather than cut in, because a theme arriving at full level is
    // startling in a quiet room.
    if (bed) {
      musicId = bed.play();
      bed.volume(0, musicId);
      bed.fade(0, BUSES.music.volume, 1400, musicId);
    }
    // A context that runs but produces no output is a real state on iOS (a
    // Bluetooth device that grabbed the route). Checked a beat after the first
    // sound, so the UI can say so rather than leaving somebody tapping a mute
    // button that is already off.
    play('tap', { magnitude: 0.4, bus: 'ui' });
    setTimeout(verifyOutput, 700);
  }

  /* The gesture. Resumes the context inside the handler, which is the only
   * place iOS allows it; Howler does the same from its own listeners, and
   * whichever gets there first, `statechange` starts the sound. */
  function unlock() {
    const ctx = context();
    if (ctx && ctx.state !== 'running') ctx.resume().catch(() => {});
    start().then(() => {
      const now = context();
      if (now && now.state === 'running') begin();
    });
  }

  function showPrompt() {
    if (prompt || muted || steady || unlocked) return;
    prompt = document.createElement('button');
    prompt.type = 'button';
    prompt.className = 'sound-prompt';
    prompt.textContent = '\u{1F50A} Tap anywhere to start the sound';
    prompt.addEventListener('click', unlock);
    document.body.appendChild(prompt);
  }

  function hidePrompt() {
    if (prompt) { prompt.remove(); prompt = null; }
  }

  function verifyOutput() {
    const context = window.Howler && window.Howler.ctx;
    if (!context) return;
    if (context.state === 'suspended') {
      document.documentElement.dataset.audio = 'suspended';
      return;
    }
    document.documentElement.dataset.audio = muted ? 'muted' : 'on';
  }

  // --- mute ----------------------------------------------------------------

  function setMuted(value) {
    muted = value;
    writeMuted(value);
    if (window.Howler) window.Howler.mute(value);
    if (speaking) speaking.muted = value;
    document.documentElement.dataset.audio = value ? 'muted' : (unlocked ? 'on' : 'locked');
    // Muting hides the prompt; unmuting before the sound has started brings it
    // back, because the unmute tap is itself the gesture and starts it anyway.
    if (value) hidePrompt(); else if (!unlocked && starting) showPrompt();
    const button = document.querySelector('[data-mute]');
    if (button) {
      button.setAttribute('aria-pressed', String(value));
      button.title = value ? 'Unmute' : 'Mute';
      button.textContent = value ? '✕' : '♪';
    }
  }

  // --- wiring --------------------------------------------------------------

  document.addEventListener('punt:unlock', unlock);

  // The mute button is a NEW element after every boosted tab change, so this
  // runs again each time. `setMuted` reads the same module-level flag, which is
  // why the mute state survives a tab change now rather than resetting.
  window.PUNT_READY(() => {
    setMuted(muted);
    document.querySelector('[data-mute]')?.addEventListener('click', () => setMuted(!muted));
  });

  document.addEventListener('DOMContentLoaded', () => {
    // Once per document, not per tab: this listens on `document`, which the
    // boosted swap does not replace.
    //
    // Not under `?punt=steady`. A running AudioContext keeps the page busy, and
    // Chrome's --virtual-time-budget then never settles: the first headless
    // check of this change hung exactly the way CLAUDE.md says every capture
    // tool here will. A still page loads its audio on a gesture, as before.
    if (!steady) start();
    // Every gesture a browser counts, not only a pointer: a keyboard user never
    // produces a pointerdown, and never heard the theme.
    const once = () => {
      unlock();
      document.removeEventListener('pointerdown', once);
      document.removeEventListener('keydown', once);
    };
    document.addEventListener('pointerdown', once);
    document.addEventListener('keydown', once);
  });

  /* Moments are no longer played from here. alert.js owns every sound effect
   * so that each one arrives with a banner saying what it was. */

  /* Speech on the commentary bus.
   *
   * A plain Audio element rather than a Howl: each line is a one-off URL that
   * will never be played again, and creating a Howl per line would leak a
   * decoded buffer for every touchdown of the afternoon.
   *
   * The delay lets the sting land first. A horn and a play call starting on the
   * same frame is the thing ducking exists to prevent, and starting them
   * together defeats it before the ramp has moved.
   */
  let speaking = null;

  function speak(url, delayMs) {
    if (muted || !unlocked) return;
    setTimeout(() => {
      try {
        // One at a time. Two play calls over each other is unintelligible, and
        // on a busy afternoon Moments genuinely do arrive in the same poll.
        if (speaking) { speaking.pause(); speaking = null; }
        const audio = new Audio(url);
        audio.volume = BUSES.commentary.volume;
        speaking = audio;
        duck();
        audio.addEventListener('ended', () => { speaking = null; unduck(0); }, { once: true });
        audio.addEventListener('error', () => { speaking = null; unduck(0); }, { once: true });
        // Fire and forget, like everything else here: a rejected play must not
        // leave the music bed ducked for the rest of the afternoon.
        audio.play().catch(() => { speaking = null; unduck(0); });
      } catch {
        unduck(0);
      }
    }, delayMs);
  }

  window.PUNT_AUDIO = { play, speak, unlock, setMuted, isMuted: () => muted };
})();
