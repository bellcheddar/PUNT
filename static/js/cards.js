/* Card tilt: gyroscope on a phone, pointer on a desktop, nothing if refused.
 *
 * Everything here writes two custom properties, `--tx` and `--ty`, in the range
 * -1 to 1. All the actual movement is CSS. That keeps the animation on the
 * compositor and means a card with no tilt handler at all is simply a flat card
 * rather than a broken one.
 *
 * The awkward part is iOS. `DeviceOrientationEvent.requestPermission()` exists
 * only there, must be called from inside a user gesture, and only over HTTPS.
 * It is therefore chained to the same "tap to kick off" gate that Phase 4 uses
 * to unlock the AudioContext, because both need one gesture and asking twice is
 * how people say no to the second one.
 */

(() => {
  'use strict';

  const GATE_KEY = 'punt.gatePassed';
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  let cards = [];
  let frame = null;
  let target = { x: 0, y: 0 };

  function collect() {
    cards = Array.from(document.querySelectorAll('.card'));
  }

  function apply() {
    frame = null;
    for (const card of cards) {
      card.style.setProperty('--tx', target.x.toFixed(3));
      card.style.setProperty('--ty', target.y.toFixed(3));
    }
  }

  function set(x, y) {
    // Clamped, then written on the next frame. Writing a custom property per
    // pointer event would style-recalc far more often than the screen refreshes.
    target = { x: Math.max(-1, Math.min(1, x)), y: Math.max(-1, Math.min(1, y)) };
    if (frame === null) frame = requestAnimationFrame(apply);
  }

  // --- pointer (desktop, and the fallback everywhere else) -----------------

  function startPointer() {
    window.addEventListener('pointermove', (event) => {
      set((event.clientX / window.innerWidth) * 2 - 1,
          (event.clientY / window.innerHeight) * 2 - 1);
    }, { passive: true });
    // Return to flat when the pointer leaves, or the last position sticks and
    // every card stays frozen mid-tilt.
    document.addEventListener('pointerleave', () => set(0, 0), { passive: true });
  }

  // --- gyroscope -----------------------------------------------------------

  function startGyro() {
    window.addEventListener('deviceorientation', (event) => {
      if (event.gamma === null && event.beta === null) return;
      // gamma is left/right in degrees, beta is front/back. Divided by 30 rather
      // than 90 because nobody rotates a phone through a right angle to look at
      // a card: the useful range is a wrist movement.
      set((event.gamma || 0) / 30, ((event.beta || 0) - 40) / 30);
    }, { passive: true });
    document.documentElement.dataset.tilt = 'gyro';
  }

  function gyroNeedsPermission() {
    return typeof window.DeviceOrientationEvent !== 'undefined'
      && typeof window.DeviceOrientationEvent.requestPermission === 'function';
  }

  /* Whether this device plausibly has an orientation sensor to ask about.
   *
   * The near-universal recipe for "is this iOS" is
   * `typeof DeviceOrientationEvent.requestPermission === 'function'`, and it is
   * out of date: desktop Chrome ships that method now. Measured directly --
   * headless Chrome on macOS reports `DeviceOrientationEvent: function,
   * requestPermission: function` -- and using it as a proxy put the full-screen
   * unlock gate in front of every desktop visitor, including the screenshot
   * harness, which is how it was noticed.
   *
   * Input modality is the honest question. A coarse pointer with no hover is a
   * touch device; anything else gets the pointer tilt, which needs no permission
   * and no gate.
   */
  function isTouchDevice() {
    return window.matchMedia('(hover: none) and (pointer: coarse)').matches;
  }

  async function requestGyro() {
    if (!gyroNeedsPermission()) {
      if ('DeviceOrientationEvent' in window) startGyro();
      return;
    }
    try {
      const state = await window.DeviceOrientationEvent.requestPermission();
      if (state === 'granted') startGyro();
      else document.documentElement.dataset.tilt = 'refused';
    } catch {
      // Thrown when not called from a gesture, or off a secure origin. Neither
      // is recoverable here and neither is worth an error message: the pointer
      // fallback is already running.
      document.documentElement.dataset.tilt = 'unavailable';
    }
  }

  // --- the gate ------------------------------------------------------------

  function buildGate() {
    const gate = document.createElement('div');
    gate.className = 'gate';
    gate.innerHTML = `
      <div class="gate-inner">
        <span class="gate-mark">PUNT</span>
        <button class="gate-button" type="button">TAP TO KICK OFF</button>
        <p class="gate-note">Turns on the tilt, and (from Phase 4) the sound.</p>
      </div>`;

    gate.querySelector('.gate-button').addEventListener('click', async () => {
      // Both permissions are requested from inside this one handler. Splitting
      // them across two taps is how the second one gets refused.
      await requestGyro();
      document.dispatchEvent(new CustomEvent('punt:unlock'));
      try { sessionStorage.setItem(GATE_KEY, '1'); } catch { /* private mode */ }
      gate.classList.add('gate--gone');
      setTimeout(() => gate.remove(), 320);
    });

    document.body.appendChild(gate);
  }

  function gatePassed() {
    try { return sessionStorage.getItem(GATE_KEY) === '1'; } catch { return false; }
  }

  // --- init ----------------------------------------------------------------

  function init() {
    collect();
    if (reducedMotion) return;
    startPointer();

    // The gate only earns its place where there is something behind it to
    // unlock. On a desktop the pointer tilt already works, so a full-screen
    // splash in front of the scores would be theatre.
    if (!isTouchDevice()) return;
    if (gyroNeedsPermission()) {
      if (!gatePassed()) buildGate();
    } else if ('DeviceOrientationEvent' in window) {
      startGyro();
    }
  }

  // Cards are swapped in by htmx on every poll, so the list has to be rebuilt.
  document.body?.addEventListener('htmx:afterSwap', collect);
  // `collect` is rebound on body, which the boosted swap keeps, so guard it;
  // `init` is about the cards on THIS page and has to run for each one.
  document.addEventListener('DOMContentLoaded', () => {
    document.body.addEventListener('htmx:afterSwap', collect);
  });
  window.PUNT_READY(init);

  window.PUNT_CARDS = { collect, set };
})();
