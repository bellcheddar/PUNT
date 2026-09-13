/* The red-zone countdown overlay.
 *
 * When somebody in the league owns a player on a drive inside the five, the app
 * says so: a pulsing overlay with a rising riser, resolved by a horn or a record
 * scratch depending on what happened.
 *
 * The overlay is a promise -- it says something is about to happen -- so the
 * close is as carefully handled as the open. A drive that stalls on the two gets
 * a scratch, and an overlay left open would sit over the scores for the rest of
 * the afternoon.
 */

(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  //: A drive cannot be inside the five for this long. If the close event is ever
  //: missed -- a dropped SSE frame, a phone that slept -- the overlay takes
  //: itself away rather than becoming permanent furniture.
  const MAX_MS = 90_000;

  let overlay = null;
  let timeout = null;

  function close(outcome) {
    if (!overlay) return;
    clearTimeout(timeout);
    const node = overlay;
    overlay = null;
    node.dataset.outcome = outcome || 'stop';
    node.classList.add('countdown--closing');
    setTimeout(() => node.remove(), reducedMotion ? 0 : 520);
  }

  function open(detail) {
    close('stop');
    const involved = detail.involved || [];
    // Teams, never people: this line is painted across the whole screen.
    const teams = [...new Set(involved.map((p) => p.team))];

    overlay = document.createElement('div');
    overlay.className = 'countdown';
    overlay.setAttribute('role', 'status');
    overlay.innerHTML = `
      <div class="countdown-inner">
        <div class="countdown-pulse" aria-hidden="true"></div>
        <div class="countdown-label">INSIDE THE FIVE</div>
        <div class="countdown-who">${teams.join(' &middot; ')}</div>
        <div class="countdown-detail">
          ${involved.map((p) => p.player).slice(0, 3).join(', ')}
          ${detail.pro_team ? `&middot; ${detail.pro_team} v ${detail.opponent || '?'}` : ''}
        </div>
      </div>`;
    document.body.appendChild(overlay);

    if (window.PUNT_AUDIO) window.PUNT_AUDIO.play('riser', { magnitude: 1 });
    if (navigator.vibrate) { try { navigator.vibrate([10, 90, 10, 90, 10]); } catch { /* ignore */ } }

    timeout = setTimeout(() => close('stop'), MAX_MS);
  }

  function resolve(detail) {
    const scored = detail.state === 'score';
    if (overlay) {
      overlay.querySelector('.countdown-label').textContent = scored ? 'TOUCHDOWN' : 'NO GOOD';
    }
    if (window.PUNT_AUDIO) {
      // The horn is left to the Moment that the touchdown itself produces; this
      // is only the resolution of the overlay, so a stalled drive gets the
      // scratch and a scored one gets nothing extra rather than two horns.
      if (!scored) window.PUNT_AUDIO.play('scratch', { magnitude: 0.9 });
    }
    setTimeout(() => close(scored ? 'score' : 'stop'), scored ? 700 : 900);
  }

  document.addEventListener('punt:redzone', (event) => {
    const detail = event.detail || {};
    if (detail.state === 'enter') open(detail);
    else resolve(detail);
  });

  window.PUNT_COUNTDOWN = { open, close, resolve };
})();
