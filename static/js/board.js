/* The Big Board: one matchup at a time, rotating, plus a takeover for the loud
 * moments.
 *
 * This is the only screen nobody touches. It is parked on a television across a
 * room for four hours, so everything here has to survive being ignored: the
 * rotation restarts itself after an htmx swap replaces the matchups underneath
 * it, and nothing depends on a pointer, a tap or a focused window.
 */

(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const TAKEOVER_MS = 5200;

  //: Below this magnitude a Moment is not worth covering the scores for. The
  //: board is the one surface where an interruption costs everybody in the room
  //: their view, so the bar is high: touchdowns and disasters, not chunk plays.
  const TAKEOVER_MAGNITUDE = 0.74;

  let board = null;
  let index = 0;
  let timer = null;

  function panels() {
    return board ? [...board.querySelectorAll('.board-window .matchup')] : [];
  }

  function show(next) {
    const all = panels();
    if (!all.length) return;
    index = ((next % all.length) + all.length) % all.length;
    all.forEach((panel, i) => {
      panel.classList.toggle('matchup--shown', i === index);
    });
    const dots = board.querySelectorAll('.board-dot');
    dots.forEach((dot, i) => dot.classList.toggle('board-dot--on', i === index));
  }

  function rotate() {
    show(index + 1);
  }

  function start() {
    board = document.querySelector('[data-board]');
    if (!board) return;
    clearInterval(timer);
    show(index);
    const seconds = Number(board.dataset.rotate) || 12;
    timer = setInterval(rotate, seconds * 1000);
  }

  // htmx replaces the matchups wholesale every poll, which removes the class
  // that marks the visible one. Without this the board goes blank thirty seconds
  // after it is switched on and stays that way until somebody reloads it.
  document.addEventListener('htmx:afterSwap', (event) => {
    if (board && board.contains(event.target)) show(index);
  });

  document.addEventListener('DOMContentLoaded', start);

  // --- the takeover --------------------------------------------------------

  /* A full-screen card for the loudest moments. The board's whole purpose is to
   * be glanceable from across a room, so a touchdown has to be legible without
   * anybody having found the right row first. */
  document.addEventListener('punt:moment', (event) => {
    const moment = event.detail || {};
    if (!document.querySelector('[data-board]')) return;
    if (moment.replayed || (moment.magnitude || 0) < TAKEOVER_MAGNITUDE) return;

    const existing = document.querySelector('.takeover');
    if (existing) existing.remove();

    const line = moment.line || {};
    const node = document.createElement('div');
    node.className = `takeover takeover--${(moment.kind || '').toLowerCase()}`;
    node.innerHTML = `
      <div class="takeover-kind">${(moment.kind || '').replace(/_/g, ' ')}</div>
      <div class="takeover-text">${line.text || moment.player || ''}</div>
      <div class="takeover-who">${(moment.managers || []).join(' &middot; ')}</div>`;
    document.body.appendChild(node);

    setTimeout(() => {
      node.classList.add('takeover--gone');
      setTimeout(() => node.remove(), reducedMotion ? 0 : 420);
    }, TAKEOVER_MS);
  });

  window.PUNT_BOARD = { show, rotate, start };
})();
