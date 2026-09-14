/* Every sound effect, with a banner that says what it was.
 *
 * After the first real Sunday the complaint was simple: horns, trombones and
 * whooshes went off with nothing on screen to say what they meant. So this file
 * is now the only place a sound effect is played. `sound()` shows the banner and
 * plays the sprite in one call, which makes an unexplained sound something the
 * code cannot express rather than something to remember not to do.
 * `tests/test_sound_alerts.py` fails if any other file plays a sprite, except a
 * tap on the UI bus, which is the answer to the reader's own finger.
 *
 * The same event also puts a line on the LATEST wheel: the server sends a
 * `change` beside every Moment and every red-zone sound, so the banner and the
 * ticker always agree about what just happened.
 */
(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  //: What each sprite MEANS, in the words the banner uses. Every sprite in
  //: static/audio/sprite.json that can be played appears here; the test holds
  //: that line, so a new sprite cannot ship without being explained.
  const SOUNDS = {
    horn_01: 'Horn: points on the board',
    horn_02: 'Big horn: a big swing',
    horn_03: 'Triple horn: a huge moment',
    trombone: 'Sad trombone: bad news',
    whoosh: 'Whoosh: a big play',
    riser: 'Rising tone: a drive inside the five',
    scratch: 'Record scratch: the drive stalled',
    doom: 'Doom drum: a matchup slipping away',
    chime: 'Chime: a milestone',
    buzzer: 'Buzzer: time up',
    crowd: 'Crowd: the room goes up',
    flip: 'Flip: a card turned over',
    rip: 'Rip: a pack opened',
  };

  //: The banner's headline for each Moment kind.
  const KINDS = {
    TOUCHDOWN: 'Touchdown',
    BIG_PLAY: 'Big play',
    LEAD_CHANGE: 'Lead change',
    GOOSE_EGG: 'Goose egg',
    DOOM: 'Doom',
    CLINCH: 'Clinched',
    BENCH_DISASTER: 'Bench disaster',
    MILESTONE: 'Milestone',
    INJURY: 'Injury',
    RED_ZONE: 'Red zone',
    NO_GOOD: 'No good',
  };

  //: Long enough to read a line of commentary, short enough that a busy
  //: afternoon does not stack them up behind each other.
  const SHOW_MS = 5200;
  //: A touchdown usually arrives with a big play and a lead change on the same
  //: poll. Three banners in a row is plenty; anything older than that is on the
  //: ticker already.
  const QUEUE = 3;

  const queue = [];
  let showing = null;

  function region() {
    let node = document.querySelector('[data-alerts]');
    if (!node) {
      node = document.createElement('div');
      node.className = 'alerts';
      node.dataset.alerts = '';
      node.setAttribute('role', 'status');
      node.setAttribute('aria-live', 'polite');
      document.body.appendChild(node);
    }
    return node;
  }

  function build(info) {
    const node = document.createElement('div');
    const tone = info.good === true ? 'good' : info.good === false ? 'bad' : 'flat';
    node.className = `alert alert--${tone}`;
    if (info.hue != null) node.style.setProperty('--hue', info.hue);

    // textContent throughout, never innerHTML: team and player names are
    // whatever ten people typed into ESPN.
    const head = document.createElement('div');
    head.className = 'alert-head';
    const kind = document.createElement('span');
    kind.className = 'alert-kind';
    kind.textContent = KINDS[info.kind] || String(info.kind || '').replace(/_/g, ' ');
    const sound = document.createElement('span');
    sound.className = 'alert-sound';
    const muted = window.PUNT_AUDIO && window.PUNT_AUDIO.isMuted();
    sound.textContent = `${muted ? '\u{1F507}' : '\u{1F50A}'} ${SOUNDS[info.name] || info.name}`;
    head.append(kind, sound);

    const text = document.createElement('div');
    text.className = 'alert-text';
    text.textContent = info.text || '';

    node.append(head, text);
    if (info.teams && info.teams.length) {
      const who = document.createElement('div');
      who.className = 'alert-who';
      // Teams, never people.
      who.textContent = info.teams.join(' · ');
      node.appendChild(who);
    }
    node.addEventListener('click', () => dismiss(node));
    return node;
  }

  function dismiss(node) {
    if (!node || node.dataset.gone) return;
    node.dataset.gone = '1';
    node.classList.add('alert--gone');
    setTimeout(() => {
      node.remove();
      if (showing === node) { showing = null; next(); }
    }, reducedMotion ? 0 : 260);
  }

  function next() {
    if (showing || !queue.length) return;
    showing = build(queue.shift());
    region().appendChild(showing);
    const node = showing;
    setTimeout(() => dismiss(node), SHOW_MS);
  }

  function banner(info) {
    queue.push(info);
    while (queue.length > QUEUE) queue.shift();
    next();
  }

  /* Play a sprite and say what it was. The banner goes up whether or not the
   * audio is unlocked or muted: on a silent phone it is the only way to know
   * the room just heard a horn. */
  function sound(name, info = {}) {
    if (!name) return;
    banner({ ...info, name });
    if (window.PUNT_AUDIO) window.PUNT_AUDIO.play(name, { magnitude: info.magnitude ?? 1 });
  }

  /* Moments arrive from the SSE stream with the line the SERVER chose, so ten
   * phones in one room play the same sting for the same touchdown. A replayed
   * moment from the backlog fills the feed but fires nothing: a horn for a
   * touchdown that happened forty minutes ago is worse than no horn. */
  document.addEventListener('punt:moment', (event) => {
    const moment = event.detail || {};
    if (moment.replayed) return;
    const line = moment.line || {};
    if (line.audio) {
      sound(line.audio, {
        kind: moment.kind,
        text: line.text || moment.player || '',
        teams: moment.teams || [],
        magnitude: moment.magnitude,
        good: moment.win_prob_delta > 0.01 ? true : moment.win_prob_delta < -0.01 ? false : null,
      });
    }
    // Speech explains itself, so it needs no banner of its own.
    if (line.speech && window.PUNT_AUDIO) window.PUNT_AUDIO.speak(line.speech, line.audio ? 260 : 0);
  });

  window.PUNT_ALERT = { sound, banner, SOUNDS, KINDS };
})();
