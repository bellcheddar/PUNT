/* Tap anything that names a team, see that team's afternoon.
 *
 * One delegated listener on `document` rather than a handler per element: the
 * cards, the score rows and every table body are replaced by htmx every thirty
 * seconds, and per-element listeners would be gone after the first poll. This
 * is also why it survives a swap without any rebinding.
 */
(() => {
  'use strict';

  const dialog = document.getElementById('sheet');
  const body = document.getElementById('sheet-body');
  if (!dialog || !body) return;

  let openFor = null;

  async function open(url) {
    openFor = url;
    body.innerHTML = '<div class="empty">Loading…</div>';
    if (!dialog.open) dialog.showModal();
    try {
      const response = await fetch(url, { headers: { 'HX-Request': 'true' } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      // A slow answer for a sheet the reader has already closed or moved on
      // from must not overwrite what they are looking at now.
      if (openFor !== url || !dialog.open) return;
      body.innerHTML = await response.text();
    } catch (error) {
      body.innerHTML = '<div class="empty"><strong>Could not load that</strong>'
                     + 'The scores on the page behind this are still good.</div>';
      console.info('team sheet failed:', error.message);
    }
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-sheet-close]')) { dialog.close(); return; }
    // Never hijack a real link or a control that already does something.
    if (event.target.closest('a, button, input, select, summary')) return;

    // `data-sheet` names the endpoint outright, so a row can open whatever kind
    // of detail belongs to the panel it is in: a bench-regret row opens the
    // whole optimal lineup, a cheer row opens that NFL game. `data-team` is the
    // fallback and still opens the team, which is right for a card and for a
    // score row where the team IS the subject.
    const owner = event.target.closest('[data-sheet], [data-team]');
    if (!owner || !dialog) return;
    const url = owner.dataset.sheet
      || (owner.dataset.team ? `/partials/team/${owner.dataset.team}` : null);
    if (!url) return;
    event.preventDefault();
    open(url);
    if (window.PUNT_AUDIO) window.PUNT_AUDIO.play('tap', { magnitude: 0.35, bus: 'ui' });
  });

  // Clicking the backdrop closes it. A <dialog>'s backdrop clicks register on
  // the dialog itself, so the test is whether the point was outside its box.
  dialog.addEventListener('click', (event) => {
    if (event.target !== dialog) return;
    const box = dialog.getBoundingClientRect();
    const outside = event.clientX < box.left || event.clientX > box.right
                 || event.clientY < box.top || event.clientY > box.bottom;
    if (outside) dialog.close();
  });

  dialog.addEventListener('close', () => { openFor = null; });
})();
