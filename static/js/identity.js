/* Which of the ten teams is this phone?
 *
 * The only thing PUNT stores locally. There are no accounts and no login: one
 * league, one shared instance, access by URL, and each phone remembers once
 * which team is theirs. That choice is what makes "your matchup" and
 * "your card" mean anything without a server ever knowing who anybody is.
 *
 * Chosen once on first run. Everything degrades if it is refused or if storage
 * is unavailable (a private window, a browser set to block site data): the app
 * simply shows the league without highlighting anybody.
 */

(() => {
  'use strict';

  const KEY = 'punt.team';

  /* See the note in packrip.js: `?punt=steady` skips the first-run overlays so a
   * capture or a demo link lands on the app in use. */
  function steadyState() {
    return new URLSearchParams(location.search).get('punt') === 'steady';
  }

  function read() {
    // In steady state the highlight needs a team without anybody having chosen
    // one, so the flag names the first team in the league rather than none.
    if (steadyState()) return new URLSearchParams(location.search).get('team') || null;
    try { return localStorage.getItem(KEY); } catch { return null; }
  }

  function write(id) {
    try { localStorage.setItem(KEY, String(id)); } catch { /* private mode */ }
  }

  /** Mark everything belonging to this phone's team. Re-run after every swap,
   *  because htmx replaces the nodes wholesale on each poll. */
  function apply() {
    const mine = read();
    document.documentElement.dataset.myTeam = mine || '';

    // The Cheer tab is the one surface whose *content* depends on whose phone
    // this is, not just its highlighting, so it is re-requested with the team.
    const cheer = document.getElementById('cheer');
    if (cheer && mine && cheer.dataset.team !== mine) {
      cheer.dataset.team = mine;
      cheer.setAttribute('hx-get', `/partials/cheer?team=${encodeURIComponent(mine)}`);
      if (window.htmx) {
        window.htmx.process(cheer);
        window.htmx.ajax('GET', `/partials/cheer?team=${encodeURIComponent(mine)}`,
                         { target: cheer, swap: 'innerHTML' });
      }
    }
    for (const node of document.querySelectorAll('[data-team]')) {
      node.classList.toggle('is-mine', Boolean(mine) && node.dataset.team === mine);
    }
  }

  async function teams() {
    const response = await fetch('/api/state', { headers: { Accept: 'application/json' } });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const state = await response.json();
    // Sorted by team name, because that is what each row leads with. Sorting by
    // one thing while displaying another makes an
    // ordered list look unordered, which on a ten-item picker is worse than no
    // sort at all.
    return (state.album || []).slice()
      .sort((a, b) => String(a.name).localeCompare(String(b.name)));
  }

  function buildChooser(list, { dismissible }) {
    const chooser = document.createElement('div');
    chooser.className = 'chooser';
    chooser.innerHTML = `
      <div class="chooser-inner">
        <h2 class="chooser-title">Which one are you?</h2>
        <p class="chooser-note">Kept on this phone only. Nothing is sent anywhere,
          and there is no account to make.</p>
        <div class="chooser-grid"></div>
        ${dismissible ? '<button class="chooser-skip" type="button">Not now</button>' : ''}
      </div>`;

    const grid = chooser.querySelector('.chooser-grid');
    for (const team of list) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'chooser-team';
      button.style.setProperty('--hue', team.hue);
      button.innerHTML = `<span class="chooser-crest">${team.monogram}</span>`
        + `<span class="chooser-names"><b>${team.name}</b><span>${team.record || ''}</span></span>`;
      button.addEventListener('click', () => {
        write(team.id);
        apply();
        chooser.remove();
      });
      grid.appendChild(button);
    }

    chooser.querySelector('.chooser-skip')?.addEventListener('click', () => chooser.remove());
    document.body.appendChild(chooser);
  }

  async function choose({ force = false } = {}) {
    if (!force && read()) return;
    try {
      const list = await teams();
      if (!list.length) return;
      buildChooser(list, { dismissible: true });
    } catch (error) {
      // Never load-bearing. If the league cannot be read, the app still works;
      // it just does not know whose phone this is.
      console.info('team chooser unavailable:', error.message);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    // On body, which survives a boosted swap: bound once.
    document.body.addEventListener('htmx:afterSwap', apply);
  });

  window.PUNT_READY(() => {
    apply();
    // Only asked on the tab where it means something immediately.
    if (document.body.dataset.tab === 'today' && !steadyState()) choose();
    document.querySelector('[data-choose-team]')?.addEventListener('click', () => choose({ force: true }));
  });

  window.PUNT_ID = { read, choose, apply };
})();
