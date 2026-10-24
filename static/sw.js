/* АНЫҚ Service Worker — v1
   Стратегия: Cache-First для статики, Network-First для API/страниц */

const CACHE_NAME = 'anyk-v1';
const STATIC_ASSETS = [
  '/',
  '/static/style.css',
  '/static/app.js',
  '/static/manifest.json',
  '/static/icon.svg',
];

// ── Install: кэшируем статику ─────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('[SW] Частичная ошибка кэширования:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// ── Activate: чистим старые кэши ─────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// ── Fetch ─────────────────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Не перехватываем POST /share-target, /analyze и API-запросы
  if (event.request.method !== 'GET') return;
  if (url.pathname.startsWith('/analyze') || url.pathname.startsWith('/share-target')) return;

  // Cache-first для /static/
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) =>
        cached || fetch(event.request).then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
          return response;
        })
      )
    );
    return;
  }

  // Network-first для страниц (/, /report/*)
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Кэшируем главную страницу
        if (url.pathname === '/') {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((c) => c.put(event.request, clone));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
