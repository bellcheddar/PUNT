/* The audio bus.
 *
 * Four buses with fixed relative levels, one sprite, and a duck. The ducking is
 * most of what makes it sound produced rather than merely loud: without it a
 * horn and a play call arrive on top of each other and the room hears neither.
 *
 * Mobile browsers are hostile here in specific, documented ways, and every one
 * of them is handled below rather than hoped about:
 *
 *   - iOS will not start an AudioContext without a user gesture, so nothing is
 *     loaded until the unlock gate fires `punt:unlock`.
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

  /* Levels from the build spec. `music` ducks under commentary and stings,
   * `stings` ducks under commentary, `commentary` ducks under nothing. */
  const BUSES = {
    music: { volume: 0.35, ducked: 0.12 },
    stings: { volume: 0.80, ducked: 0.30 },
    commentary: { volume: 1.00, ducked: 1.00 },
    ui: { volume: 0.50, ducked: 0.20 },
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
    bed = new window.Howl({
      src: ['/static/audio/bed.mp3', '/static/audio/bed.ogg'],
      html5: false,
      loop: true,
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
    const level = (BUSES[which] || BUSES.stings).volume;
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

  // --- unlock --------------------------------------------------------------

  async function unlock() {
    if (unlocked) return;
    unlocked = true;
    await load();
    if (!howl) return;

    // The bed starts inside the unlock handler, which is the only place iOS
    // will let it: the gesture that granted the context is the gesture that has
    // to start the sound. Faded in rather than cut in, because a loop arriving
    // at full level is startling in a quiet room.
    if (bed) {
      musicId = bed.play();
      bed.volume(0, musicId);
      bed.fade(0, BUSES.music.volume, 1400, musicId);
    }

    // A context that unlocked but produces no output is a real state on iOS
    // (silent switch plus an element path, a Bluetooth device that grabbed the
    // route). Checked once, a beat after the first sound, so the UI can say so
    // rather than leaving somebody tapping a mute button that is already off.
    play('tap', { magnitude: 0.4, bus: 'ui' });
    setTimeout(verifyOutput, 700);
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
    const button = document.querySelector('[data-mute]');
    if (button) {
      button.setAttribute('aria-pressed', String(value));
      button.title = value ? 'Unmute' : 'Mute';
      button.textContent = value ? '✕' : '♪';
    }
  }

  // --- wiring --------------------------------------------------------------

  document.addEventListener('punt:unlock', unlock);

  document.addEventListener('DOMContentLoaded', () => {
    setMuted(muted);
    document.querySelector('[data-mute]')?.addEventListener('click', () => setMuted(!muted));

    // A desktop has no unlock gate (there is no permission to ask for), so the
    // first real interaction is the gesture.
    const once = () => { unlock(); document.removeEventListener('pointerdown', once); };
    document.addEventListener('pointerdown', once);
  });

  /* Moments arrive from the SSE stream with the line the *server* chose, so ten
   * phones in one room play the same sting for the same touchdown. A replayed
   * moment from the backlog fills the feed but fires nothing: a horn for a
   * touchdown that happened forty minutes ago is worse than no horn. */
  document.addEventListener('punt:moment', (event) => {
    const moment = event.detail || {};
    if (moment.replayed) return;
    const line = moment.line || {};
    play(line.audio, { magnitude: moment.magnitude });
    if (line.speech) speak(line.speech, line.audio ? 260 : 0);
  });

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
