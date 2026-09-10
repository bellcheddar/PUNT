/* The pack rip: the weekly reveal, and the emotional centre of the Monday visit.
 *
 * Ten cards are minted every week. Opening them one at a time, lowest score
 * revealed last, is the difference between "here is a table of scores" and the
 * thing the league actually turns up for. The spec asks for the most polish here
 * and this is where it goes.
 *
 * Everything animates on `transform` and `opacity` only. The cards are face-up
 * in the HTML and turned face-down by this file before a rip, so with JavaScript
 * off the album is simply an album: the reveal is a flourish, not the content.
 */

(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const STAGGER = 420;
  const FLIP_MS = 620;


  /* `?punt=steady` skips the first-run overlays.
   *
   * Not a setting and not a feature: it exists so a capture, a demo link or a
   * bar screen can land straight on the app in use rather than on whichever
   * one-time overlay a fresh browser profile is due. Seeding localStorage from
   * a harness page was tried first and is not reliable enough to build captures
   * on -- the value is set and the iframe still reads a fresh profile.
   */
  function steadyState() {
    return new URLSearchParams(location.search).get('punt') === 'steady';
  }

  function seenKey(week) { return `punt.pack.${week}`; }

  function alreadyRipped(week) {
    try { return localStorage.getItem(seenKey(week)) === '1'; } catch { return false; }
  }

  function markRipped(week) {
    try { localStorage.setItem(seenKey(week), '1'); } catch { /* private mode */ }
  }

  function buzz(ms) {
    // Android only; iOS Safari has no vibrate at all. Progressive enhancement,
    // never load-bearing: the reveal reads exactly the same without it.
    if (navigator.vibrate) { try { navigator.vibrate(ms); } catch { /* ignore */ } }
  }

  // --- confetti ------------------------------------------------------------

  function confetti(origin) {
    if (reducedMotion) return;
    const canvas = document.createElement('canvas');
    canvas.className = 'confetti';
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = window.innerWidth * dpr;
    canvas.height = window.innerHeight * dpr;
    document.body.appendChild(canvas);

    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    const colours = ['#fcb900', '#ff2fd0', '#9b51e0', '#4a9fd4', '#00d084'];
    const bits = Array.from({ length: 90 }, () => ({
      x: origin.x, y: origin.y,
      vx: (Math.random() - 0.5) * 11,
      vy: -Math.random() * 13 - 3,
      size: 3 + Math.random() * 5,
      spin: (Math.random() - 0.5) * 0.4,
      angle: Math.random() * Math.PI,
      colour: colours[(Math.random() * colours.length) | 0],
    }));

    const started = performance.now();
    (function frame(now) {
      const elapsed = now - started;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (const bit of bits) {
        bit.vy += 0.32;             // gravity
        bit.x += bit.vx;
        bit.y += bit.vy;
        bit.angle += bit.spin;
        ctx.save();
        ctx.translate(bit.x, bit.y);
        ctx.rotate(bit.angle);
        ctx.globalAlpha = Math.max(0, 1 - elapsed / 1800);
        ctx.fillStyle = bit.colour;
        ctx.fillRect(-bit.size / 2, -bit.size / 2, bit.size, bit.size * 0.6);
        ctx.restore();
      }
      if (elapsed < 1800) requestAnimationFrame(frame);
      else canvas.remove();
    })(started);
  }

  // --- reveal --------------------------------------------------------------

  function faceDown(cards) {
    for (const card of cards) {
      card.style.setProperty('--flip', '1');
      card.style.setProperty('--pop', '0.86');
      card.style.opacity = '0';
    }
  }

  function reveal(card, index) {
    card.style.transition = 'none';
    card.style.opacity = '1';
    // Force a style flush so the transition below actually runs from the
    // face-down state rather than being coalesced away with it.
    void card.offsetWidth;
    card.style.transition = `transform ${FLIP_MS}ms cubic-bezier(.22,1.1,.36,1)`;
    card.style.setProperty('--flip', '0');
    card.style.setProperty('--pop', '1');

    buzz(card.dataset.tier === 'cursed' ? 40 : 18);

    if (card.dataset.tier === 'legendary') {
      setTimeout(() => {
        const box = card.getBoundingClientRect();
        confetti({ x: box.left + box.width / 2, y: box.top + box.height / 2 });
        buzz([30, 40, 30]);
      }, FLIP_MS * 0.55);
    }
  }

  function revealAll(cards, done) {
    if (reducedMotion) {
      for (const card of cards) {
        card.style.opacity = '1';
        card.style.setProperty('--flip', '0');
        card.style.setProperty('--pop', '1');
      }
      done();
      return;
    }
    // Sorted by data-reveal: highest score first, lowest last, so the reveal
    // builds towards the worst week in the league rather than the best.
    const order = [...cards].sort((a, b) => a.dataset.reveal - b.dataset.reveal);
    order.forEach((card, index) => setTimeout(() => reveal(card, index), index * STAGGER));
    setTimeout(done, order.length * STAGGER + FLIP_MS);
  }

  // --- the pack ------------------------------------------------------------

  function buildPack(grid, cards, week) {
    const pack = document.createElement('div');
    pack.className = 'pack';
    pack.innerHTML = `
      <div class="pack-foil">
        <span class="pack-mark">PUNT</span>
        <span class="pack-week">WEEK ${week}</span>
        <span class="pack-count">${cards.length} CARDS</span>
      </div>
      <button class="pack-button" type="button">TAP TO RIP</button>`;
    grid.before(pack);
    // Collapse the grid while the pack is sealed. The cards are already there
    // and already face-down, but ten invisible cards leave a screen-high void
    // under the packet that reads as a rendering failure.
    grid.classList.add('album-grid--sealed');

    const rip = () => {
      pack.classList.add('pack--ripped');
      buzz([12, 26, 12]);
      // The pack tears, then the reveal starts under it. 520ms is the tear.
      setTimeout(() => {
        pack.remove();
        grid.classList.remove('album-grid--sealed');
        revealAll(cards, () => markRipped(week));
      }, reducedMotion ? 0 : 520);
    };

    pack.querySelector('.pack-button').addEventListener('click', rip, { once: true });
    pack.addEventListener('click', (event) => {
      if (event.target.closest('.pack-button')) return;
      rip();
    }, { once: true });
  }

  // --- init ----------------------------------------------------------------

  function start() {
    const grid = document.querySelector('[data-album]');
    if (!grid) return;
    const cards = grid.querySelectorAll('.card');
    if (!cards.length) return;

    const week = grid.dataset.week || '0';
    if (steadyState() || alreadyRipped(week)) return;

    faceDown(cards);
    buildPack(grid, cards, week);
  }

  function again() {
    const grid = document.querySelector('[data-album]');
    if (!grid) return;
    try { localStorage.removeItem(seenKey(grid.dataset.week || '0')); } catch { /* ignore */ }
    document.querySelector('.pack')?.remove();
    grid.classList.remove('album-grid--sealed');
    start();
  }

  document.addEventListener('DOMContentLoaded', start);
  // htmx swaps the grid on every poll. Re-running the whole rip mid-afternoon
  // would be maddening, so only the face-down state is reapplied if a pack is
  // still on screen waiting to be opened.
  document.addEventListener('htmx:afterSwap', () => {
    if (!document.querySelector('.pack')) return;
    const grid = document.querySelector('[data-album]');
    if (grid) faceDown(grid.querySelectorAll('.card'));
  });

  window.PUNT_PACK = { again, start };
})();
