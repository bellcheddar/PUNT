/* PUNT front end. Deliberately small: htmx does the updating, the server does
 * the rendering, and this file only handles the three things that cannot be
 * done from the server -- installing, the live stream, and remembering which of
 * the ten teams this phone belongs to.
 */

(() => {
  'use strict';

  // --- surviving a tab change ----------------------------------------------

  /* The tab bar is boosted, so changing tabs swaps the body instead of loading
   * a new document. That is not a performance decision, it is the only way the
   * music can play at all.
   *
   * Audio needs a user gesture per DOCUMENT. With plain links, tapping a tab
   * was the gesture: it unlocked the audio, started the bed's 1400 ms fade-in,
   * and then the browser tore the document down mid-fade and the next page
   * began locked again. The music could only ever be heard in the gap between
   * the tap and the page changing, which is exactly what it sounded like.
   *
   * Keeping the document means the AudioContext, the bed and the mute state all
   * survive. It also means DOMContentLoaded fires once for the whole visit, so
   * anything that wires up an ELEMENT has to run again after each swap. That is
   * what `PUNT_READY` is for. Anything that listens on `document` itself must
   * NOT use it, or it binds again on every tab change.
   */
  window.PUNT_READY = (fn) => {
    document.addEventListener('DOMContentLoaded', fn);
    document.addEventListener('punt:navigated', fn);
  };

  // A boosted navigation is the one that replaces the body; the thirty-second
  // polls swap partials deeper in the page. Discriminating on the target rather
  // than on a flag in the event detail, because the target is what actually
  // determines whether this page's elements are still the ones on screen.
  document.addEventListener('htmx:afterSettle', (event) => {
    if (event.detail && event.detail.target === document.body) {
      document.dispatchEvent(new CustomEvent('punt:navigated'));
    }
  });

  // --- service worker ------------------------------------------------------

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch((error) => {
        // Never load-bearing. A refused registration (private browsing, an
        // insecure origin during development) must not take the app with it.
        console.info('service worker not registered:', error.message);
      });
    });
  }

  // --- identity ------------------------------------------------------------

  // The only thing this app stores locally: which of the ten teams this phone
  // is. No accounts, no login, one URL for the whole league.
  const TEAM_KEY = 'punt.team';

  function storedTeam() {
    try { return window.localStorage.getItem(TEAM_KEY); } catch { return null; }
  }

  function rememberTeam(id) {
    try { window.localStorage.setItem(TEAM_KEY, String(id)); } catch { /* private mode */ }
  }

  // --- install hint --------------------------------------------------------

  const isStandalone = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  const isIosSafari = /iP(hone|ad|od)/.test(navigator.userAgent)
    && /Safari/.test(navigator.userAgent) && !/CriOS|FxiOS/.test(navigator.userAgent);

  // iOS fires no beforeinstallprompt, so the only way to be installed there is
  // for somebody to be told about the Share menu. Shown once, to iOS Safari
  // visitors who are not already installed.
  const steady = new URLSearchParams(location.search).get('punt') === 'steady';

  if (isIosSafari && !isStandalone && !steady && !localStorage.getItem('punt.installHintSeen')) {
    const hint = document.createElement('div');
    hint.className = 'banner banner--demo';
    hint.innerHTML = '<strong>Put PUNT on your home screen.</strong> '
      + 'Tap Share, then <b>Add to Home Screen</b>. It runs full screen with no browser bar, '
      + 'which is the only way the audio and the tilt work properly.';
    document.querySelector('main')?.before(hint);
    try { localStorage.setItem('punt.installHintSeen', '1'); } catch { /* ignore */ }
  }

  // Android does fire it. Suppress the mini-infobar (which covers the bottom of
  // the screen, where the tab bar lives) and offer it as a banner instead.
  //
  // Deliberately not a second round button in the header: at 390 px the header
  // is a wordmark, the league name and one control, and adding a second one
  // truncated the league name to make room for something most visitors will tap
  // once, ever.
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    if (isStandalone || steady) return;

    const banner = document.createElement('div');
    banner.className = 'banner banner--demo';
    banner.innerHTML = '<strong>Install PUNT.</strong> It runs full screen with no browser bar, '
      + 'which is how the audio and the tilt are meant to work. '
      + '<button class="banner-action" type="button">Install</button>';

    banner.querySelector('button').addEventListener('click', async () => {
      banner.remove();
      event.prompt();
      await event.userChoice;
    }, { once: true });

    document.querySelector('main')?.before(banner);
  });

  // --- live stream ---------------------------------------------------------

  // Moments arrive here the instant the poller sees them. The htmx polling on
  // each tab is the fallback, so a dropped connection degrades to a 30 s refresh
  // rather than to silence.
  let stream = null;

  function connect() {
    if (stream || !('EventSource' in window)) return;
    stream = new EventSource('/stream');

    stream.addEventListener('moment', (event) => {
      let moment;
      try { moment = JSON.parse(event.data); } catch { return; }
      document.dispatchEvent(new CustomEvent('punt:moment', { detail: moment }));
      announce(moment);
      // Phase 4 hangs the audio bus off this event. Until then the feed just
      // refreshes itself so the new line appears without waiting for the poll.
      if (window.htmx) {
        const feed = document.getElementById('feed');
        if (feed) window.htmx.trigger(feed, 'punt:refresh');
      }
    });

    stream.addEventListener('redzone', (event) => {
      let detail;
      try { detail = JSON.parse(event.data); } catch { return; }
      document.dispatchEvent(new CustomEvent('punt:redzone', { detail }));
    });

    stream.onerror = () => {
      // EventSource reconnects on its own using the server's `retry:` hint.
      // Closing and recreating it here would defeat that and produce a
      // reconnect storm on a bar's wifi, which is the thing to avoid.
      document.documentElement.dataset.stream = 'reconnecting';
      // Not announced immediately. A single dropped frame is normal on a shared
      // connection and a banner that flickers on every blip trains people to
      // ignore banners, which is worse than not having one.
      scheduleConnectionNotice();
    };
    stream.addEventListener('hello', () => {
      document.documentElement.dataset.stream = 'live';
      clearConnectionNotice();
    });
  }

  /* One sentence per Moment, for a screen reader.
   *
   * Deliberately not every Moment: a polite live region that speaks 241 times in
   * an afternoon is not accessibility, it is a second problem. The same
   * magnitude bar the Big Board's takeover uses, so what gets read aloud is what
   * would have interrupted the room anyway. */
  const ANNOUNCE_MAGNITUDE = 0.55;

  function announce(moment) {
    if (!moment || moment.replayed || (moment.magnitude || 0) < ANNOUNCE_MAGNITUDE) return;
    const region = document.querySelector('[data-announce]');
    if (!region) return;
    const line = (moment.line && moment.line.text)
      || `${(moment.kind || '').replace(/_/g, ' ').toLowerCase()}, `
         + `${moment.player || ''} for ${(moment.managers || []).join(' and ')}`;
    region.textContent = line;
  }

  // --- the connection banner ----------------------------------------------

  /* What a phone shows when the venue's wifi goes. The server's own stale banner
   * covers "ESPN is not answering"; this covers "this phone cannot reach
   * anything", which is a different sentence and sends somebody to a different
   * place to fix it.
   */
  const NOTICE_DELAY = 12_000;
  let noticeTimer = null;
  let notice = null;

  function scheduleConnectionNotice() {
    if (notice || noticeTimer) return;
    noticeTimer = setTimeout(showConnectionNotice, NOTICE_DELAY);
  }

  function showConnectionNotice() {
    noticeTimer = null;
    if (notice) return;
    notice = document.createElement('div');
    notice.className = 'banner banner--error';
    notice.setAttribute('role', 'status');
    notice.innerHTML = navigator.onLine
      ? '<strong>Lost the live feed.</strong> Scores are still refreshing every '
        + 'thirty seconds and will catch up on their own. Nothing needs doing.'
      : '<strong>This phone is offline.</strong> The scores on screen are the last '
        + 'ones it saw. It will catch up by itself when the wifi comes back.';
    document.querySelector('main')?.before(notice);
  }

  function clearConnectionNotice() {
    clearTimeout(noticeTimer);
    noticeTimer = null;
    if (notice) { notice.remove(); notice = null; }
  }

  window.addEventListener('offline', showConnectionNotice);
  window.addEventListener('online', () => {
    clearConnectionNotice();
    // Reconcile immediately rather than waiting for the next poll: somebody who
    // just watched their wifi come back is looking at the screen right now.
    if (window.htmx) {
      document.querySelectorAll('[hx-trigger*="every"]').forEach((el) => {
        window.htmx.trigger(el, 'punt:refresh');
      });
    }
  });

  // A successful htmx swap proves the network is working, whatever the stream
  // thinks. The scores are the thing people actually care about.
  document.body?.addEventListener('htmx:afterSwap', clearConnectionNotice);
  document.addEventListener('DOMContentLoaded', () => {
    document.body.addEventListener('htmx:afterSwap', clearConnectionNotice);
    if (!navigator.onLine) showConnectionNotice();
  });

  // Backgrounded tabs throttle timers, so reconcile on return rather than
  // trusting anything to have kept running.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && window.htmx) {
      document.querySelectorAll('[hx-trigger*="every"]').forEach((el) => {
        window.htmx.trigger(el, 'punt:refresh');
      });
    }
  });

  window.addEventListener('load', connect);

  window.PUNT = { storedTeam, rememberTeam };
})();
