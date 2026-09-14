/* The LATEST wheel: step the reel up one row every few seconds.
 *
 * Driven from here rather than from a CSS @keyframes loop, and the reason is
 * not taste. An infinite CSS animation stops Chrome's --virtual-time-budget
 * from ever settling, and every headless probe in this repo uses it:
 * tools/screenshot.py, tools/a11y.py --page and tools/perf.py --page would all
 * hang the moment this landed on the page. A finite transition per step leaves
 * the document idle in between, so virtual time settles as it always did.
 *
 * Everything below is therefore about knowing when NOT to run:
 *   - the page is hidden (a backgrounded tab throttles timers anyway, and a
 *     wheel that spins unseen just burns battery)
 *   - the reader has asked for reduced motion (the CSS shows a list instead)
 *   - a capture is in progress (?punt=steady), or the probes hang
 *   - there is only one row, so there is nothing to step to
 */
(() => {
  'use strict';

  const STEP_MS = 4200;        // long enough to read a line of thirteen words
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  const steady = new URLSearchParams(location.search).get('punt') === 'steady';

  let timer = null;

  function wheels() {
    return Array.from(document.querySelectorAll('[data-wheel]'));
  }

  function step(wheel) {
    const reel = wheel.querySelector('[data-reel]');
    if (!reel) return;
    const rows = reel.children.length;
    if (rows < 2) return;

    const at = Number(reel.dataset.at || 0) + 1;
    // The last row steps back to the first. A transition from the bottom of the
    // reel to the top would rewind the whole strip visibly, so the jump home is
    // made with the transition off and the next step re-enables it.
    if (at >= rows) {
      reel.style.transition = 'none';
      reel.dataset.at = '0';
      reel.style.transform = 'translateY(0)';
      // Read back a layout property so the browser commits the untransitioned
      // position before the transition is restored. Without this the style
      // changes coalesce and the rewind animates after all.
      void reel.offsetHeight;
      reel.style.transition = '';
      return;
    }
    reel.dataset.at = String(at);
    reel.style.transform = `translateY(-${at * rowHeight(wheel)}px)`;
  }

  function rowHeight(wheel) {
    const row = wheel.querySelector('.wheel-row');
    // Measured rather than assumed: the row height is a CSS custom property and
    // a phone with a large text setting will not give back the 30 it says.
    return row ? row.getBoundingClientRect().height : 30;
  }

  function tick() {
    if (document.visibilityState !== 'visible') return;
    wheels().forEach(step);
  }

  function start() {
    if (timer || reduced.matches || steady) return;
    timer = window.setInterval(tick, STEP_MS);
  }

  function stop() {
    if (timer) window.clearInterval(timer);
    timer = null;
  }

  // A poll swaps the strip's innerHTML, which resets the reel to the top. That
  // is the right behaviour for a panel called LATEST: the newest line is first,
  // so restarting lands on the thing that just happened.
  document.addEventListener('htmx:afterSwap', (event) => {
    if (event.target && event.target.querySelector && event.target.querySelector('[data-wheel]')) {
      const reel = event.target.querySelector('[data-reel]');
      if (reel) { reel.dataset.at = '0'; reel.style.transform = 'translateY(0)'; }
    }
  });

  // A Moment arriving over SSE beats waiting up to thirty seconds for the poll,
  // so a new change is put straight on the front of the reel.
  document.addEventListener('punt:change', (event) => {
    const change = event.detail;
    const reel = document.querySelector('[data-reel]');
    if (!reel || !change) return;
    if (reel.querySelector(`[data-change="${change.id}"]`)) return;
    const row = document.createElement('div');
    const tone = change.good === true ? 'good' : change.good === false ? 'bad' : 'flat';
    row.className = `wheel-row wheel-row--${tone}`;
    row.dataset.change = change.id;
    row.dataset.sheet = `/partials/detail/change/${change.id}`;
    const dot = document.createElement('span');
    dot.className = 'dot';
    dot.style.setProperty('--hue', change.hue);
    const kind = document.createElement('span');
    kind.className = 'wheel-kind';
    kind.textContent = change.kind;
    // The speaker says this line is the one that just made a noise.
    if (change.sound) {
      row.classList.add('wheel-row--sound');
      row.dataset.sound = change.sound;
    }
    const text = document.createElement('span');
    text.className = 'wheel-text';
    // textContent, never innerHTML: the line is assembled from ESPN's team and
    // player names, which are whatever ten people typed into a web form.
    text.textContent = change.text;
    row.append(dot, kind, text);
    if (change.value) {
      const value = document.createElement('span');
      value.className = 'wheel-value';
      value.textContent = change.value;
      row.appendChild(value);
    }
    reel.prepend(row);
    // Prepending shifts every row down by one, so the reel has to be nudged to
    // keep showing what the reader was already looking at.
    reel.dataset.at = String(Number(reel.dataset.at || 0) + 1);
    reel.style.transition = 'none';
    reel.style.transform = `translateY(-${Number(reel.dataset.at) * rowHeight(reel.parentElement)}px)`;
    void reel.offsetHeight;
    reel.style.transition = '';
    // And trim, or an afternoon of this grows the DOM without bound.
    while (reel.children.length > 60) reel.lastElementChild.remove();
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') start(); else stop();
  });
  reduced.addEventListener('change', () => { stop(); start(); });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
