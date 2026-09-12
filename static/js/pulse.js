/* Show that something changed, without anybody having to watch for it.
 *
 * Every panel re-renders itself on a thirty second poll by swapping its whole
 * innerHTML, which is invisible: a number goes from 61.1 to 74.6 between two
 * frames and nothing marks the moment. A reader glancing up has no way to tell
 * a page that just updated from one that has been sitting there for an hour.
 *
 * So: remember what each row said before the swap, compare afterwards, and mark
 * the ones that moved. Row granularity rather than cell, deliberately -- a
 * table where six cells light up separately is a fruit machine, and the thing
 * worth noticing is "this team did something", not "this digit differs".
 *
 * Four different treatments, because four different things change and a single
 * flash for all of them says less than no flash at all:
 *
 *   a row whose numbers moved   a brief wash, green up and red down
 *   a card whose score moved    a soft lift, because a card is an object
 *   a card that changed rank    a short slide from where it came
 *   a brand new row             a fade in rather than an appearance
 *
 * All four are `transform` and `opacity` only, all four are under a second, and
 * all four are off entirely under `prefers-reduced-motion`: this runs every
 * thirty seconds for four hours on a phone in somebody's hand.
 */
(() => {
  'use strict';

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const steady = new URLSearchParams(location.search).get('punt') === 'steady';

  /* Remembered per swap target, so two panels showing the same team cannot
     confuse each other. WeakMap: when htmx throws the element away, so does
     this, with no bookkeeping. */
  const before = new WeakMap();

  /* A row's identity is the team it names. Everything on this page is a table
     of ten teams, so that is a stable key across a re-render even when the rows
     have been re-sorted, which several of these panels do. */
  function rowsOf(root) {
    const out = new Map();
    root.querySelectorAll('[data-team], [data-sheet]').forEach((el) => {
      const key = el.dataset.team
        || (el.dataset.sheet || '').match(/\/(\d+)$/)?.[1]
        || el.dataset.sheet;
      if (!key) return;
      // Numbers only. A row whose commentary changed but whose figures did not
      // has not moved, and lighting it up would be noise.
      const digits = (el.textContent.match(/-?\d+\.?\d*/g) || []).join(' ');
      if (!out.has(key)) out.set(key, { el, digits });
    });
    return out;
  }

  function total(text) {
    const first = (text.match(/-?\d+\.?\d*/) || [])[0];
    return first === undefined ? null : parseFloat(first);
  }

  function mark(el, cls) {
    el.classList.remove(cls);
    // Force a reflow so re-adding the class restarts the animation: without it
    // a row that changes on two consecutive polls only animates once.
    void el.offsetWidth;
    el.classList.add(cls);
    el.addEventListener('animationend', () => el.classList.remove(cls), { once: true });
  }

  function compare(target) {
    const was = before.get(target);
    if (!was) return;
    const now = rowsOf(target);

    now.forEach((entry, key) => {
      const old = was.get(key);
      if (!old) { mark(entry.el, 'is-new'); return; }
      if (old.digits === entry.digits) return;

      const card = entry.el.classList.contains('card');
      if (card) {
        // A card is an object on a table, so it lifts rather than washes. The
        // rank badge is the other thing that can change about it, and a card
        // that has moved up the album deserves to say so differently from one
        // that merely scored.
        const rank = entry.el.querySelector('.card-rank');
        const oldRank = old.el?.querySelector?.('.card-rank');
        mark(entry.el, rank && oldRank && rank.textContent !== oldRank.textContent
          ? 'is-moved' : 'is-lifted');
        return;
      }
      const wasTotal = total(old.digits);
      const nowTotal = total(entry.digits);
      mark(entry.el, nowTotal !== null && wasTotal !== null && nowTotal < wasTotal
        ? 'is-down' : 'is-up');
    });
  }

  function watch(target) {
    if (!target || !target.querySelectorAll) return;
    before.set(target, rowsOf(target));
  }

  if (reduced.matches || steady) return;

  // htmx fires `beforeSwap` with the old content still in place, which is the
  // only moment the previous values exist to be read.
  document.addEventListener('htmx:beforeSwap', (e) => watch(e.target));
  document.addEventListener('htmx:afterSwap', (e) => compare(e.target));
})();
