// AOLA Tasks — Service Worker
// Cache network-first pour /e/<token>/expenses et les assets statiques,
// fallback cache si hors-ligne.
const CACHE = 'aola-tasks-v1';

self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter(n => n !== CACHE).map(n => caches.delete(n)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Network-first, cache-fallback
  e.respondWith((async () => {
    try {
      const fresh = await fetch(req);
      if (fresh && fresh.ok) {
        const cache = await caches.open(CACHE);
        cache.put(req, fresh.clone());
      }
      return fresh;
    } catch (err) {
      const cached = await caches.match(req, {ignoreSearch: true});
      if (cached) return cached;
      // Rien en cache : on renvoie une page HTML minimale hors-ligne
      if (req.headers.get('accept')?.includes('text/html')) {
        return new Response(
          '<!doctype html><meta charset="utf-8"><title>Hors ligne</title>' +
          '<div style="padding:40px;font:16px/1.5 system-ui;text-align:center">' +
          '<h2>📵 Hors ligne</h2>' +
          '<p>Reviens sur cette page une fois le réseau revenu.</p>' +
          '</div>',
          {headers: {'Content-Type': 'text/html; charset=utf-8'}}
        );
      }
      throw err;
    }
  })());
});
