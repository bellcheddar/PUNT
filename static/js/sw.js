/* PUNT service worker.
 *
 * Served from the site root (see the /sw.js route) rather than from /static/,
 * because a worker's default scope is its own directory: registered from
 * /static/js/ it would control nothing the app actually navigates to.
 *
 * One rule matters more than the rest, and it is the reason this file is short:
 * FANTASY DATA IS NEVER CACHED HERE. The server's TTL cache is the only cache
 * allowed to hold a score. A service worker that serves a stale boxscore has no
 * way to know it is stale, cannot be invalidated by the server, and produces the
 * single worst failure this app could have -- a phone in the bar showing a
 * touchdown that happened twenty minutes ago, with no indication anything is
 * wrong. Everything under /api, /partials and /stream therefore goes to the
 * network and nowhere else.
 */

const VERSION = 'punt-v1';
const SHELL = [
  '/',
  '/album',
  '/static/css/theme.css',
  '/static/css/fonts.css',
  '/static/js/htmx.min.js',
  '/static/js/app.js',
  '/static/icons/icon-192.png',
  '/static/fonts/anton-400-latin.woff2',
  '/static/fonts/inter-400-latin.woff2',
  '/static/fonts/inter-600-latin.woff2',
  '/static/fonts/roboto-mono-400-latin.woff2',
];

/** Paths whose responses must never be cached. */
const LIVE = /^\/(api|partials|stream|admin)\b/;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      // Individually, not addAll: addAll rejects the whole install if any one
      // request fails, so a single renamed asset silently leaves the app with
      // no offline shell at all.
      .then((cache) => Promise.allSettled(SHELL.map((url) => cache.add(url))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (LIVE.test(url.pathname)) return;   // straight to the network, never cached

  // Static assets are cache-first: they carry a ?v= stamp, so a deploy changes
  // the URL rather than needing the cache invalidated.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((hit) => hit || fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      })),
    );
    return;
  }

  // Pages are network-first with the cached shell as the fallback, so a phone
  // that loses the bar's wifi gets the app rather than a dinosaur, and the
  // stale-data banner inside it explains what it is looking at.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok && request.mode === 'navigate') {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(() => caches.match(request).then((hit) => hit || caches.match('/'))),
  );
});
