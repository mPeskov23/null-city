/* Service Worker for NULL CITY PWA */
'use strict';

const CACHE_NAME = 'null-city-pwa-v1';

const CORE_ASSETS = [
  '/',
  '/index.html',
  '/hub.html',
  '/privacy.html',
  '/terms.html',
  '/status.html',
  '/manifest.webmanifest',
  '/src/style.css',
  '/src/signal.css',
  '/src/opening-cover.css',
  '/src/i18n.css',
  '/src/hub.css',
  '/src/i18n-catalog.js',
  '/src/i18n.js',
  '/src/opening-cover.js',
  '/src/campaign.js',
  '/src/terrain-assets.js',
  '/src/city-meta.js',
  '/src/building-assets.js',
  '/src/city-meta2.js',
  '/src/city-meta3.js',
  '/src/pack2-assets.js',
  '/src/walkcycle-assets.js',
  '/src/object-assets.js',
  '/src/engine.js',
  '/src/art.js',
  '/src/characters.js',
  '/src/noir-art.js',
  '/src/menu.js',
  '/src/signal-ui.js',
  '/src/places.js',
  '/src/capital.js',
  '/src/app.js',
  '/src/hub.js',
  '/src/pwa.js',
  '/assets/icon-192.png',
  '/assets/icon-512.png',
  '/assets/icon-maskable-192.png',
  '/assets/icon-maskable-512.png',
  '/assets/apple-touch-icon.png',
  '/assets/city/terrain.png',
  '/assets/city/buildings.png',
  '/assets/city/long_buildings.png',
  '/assets/city/vehicles_game.png',
  '/assets/city/characters_game.png',
  '/assets/city/parks.png',
  '/assets/characters/main_characters_walkcycles.png',
  '/assets/characters/npc_8_roles_walkcycles.png',
  '/assets/terrain/asphalt_paving_additional_tiles.png',
  '/assets/terrain/asphalt_cobblestone_full_tileset.png',
  '/assets/terrain/stone-atlas.png',
  '/assets/terrain/stone-platform.png',
  '/assets/terrain/road-atlas.png',
  '/assets/objects/midnight-ramen.png',
  '/assets/signal-lost/opening-cover.png',
  '/assets/signal-lost/tape-room.png',
  '/assets/signal-lost/portraits.png',
  '/assets/nulls/bg-room-svg.svg'
];

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(async cache => {
      // Fetch core assets safely without letting one failure break install
      await Promise.allSettled(
        CORE_ASSETS.map(url =>
          cache.add(new Request(url, { cache: 'reload' })).catch(err => {
            console.warn('[PWA-SW] Precaching skip for:', url, err.message);
          })
        )
      );
    })
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.map(key => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const req = event.request;
  const url = new URL(req.url);

  // Never intercept non-GET requests or requests to other origins
  if (req.method !== 'GET' || url.origin !== location.origin) {
    return;
  }

  // Network-only for API endpoints (auth, rooms, SSE events, etc.)
  if (url.pathname.startsWith('/api/')) {
    return;
  }

  // Navigation requests: Network first, fall back to cached page
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(async () => {
        const cache = await caches.open(CACHE_NAME);
        const match = (await cache.match(url.pathname)) || (await cache.match('/index.html')) || (await cache.match('/'));
        return match || Response.error();
      })
    );
    return;
  }

  // Static assets: Cache first with network fallback & background update
  event.respondWith(
    caches.match(req, { ignoreSearch: true }).then(cached => {
      if (cached) {
        return cached;
      }
      return fetch(req).then(response => {
        if (!response || response.status !== 200 || response.type !== 'basic') {
          return response;
        }
        const toCache = response.clone();
        caches.open(CACHE_NAME).then(cache => {
          cache.put(req, toCache);
        });
        return response;
      });
    })
  );
});
