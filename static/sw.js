/* Service Worker de Localis (PWA).
 *
 * Estrategias:
 *  - App shell / navegación pública: network-first con fallback a caché y a /offline.
 *  - Estáticos (/static/): cache-first (rápido y barato).
 *  - API y zonas privadas: solo red (nunca se cachean).
 *
 * Reglas de seguridad: no se cachea nada de /api/, /admin, /comercio, /perfil,
 * /login/google ni /logout para no exponer datos de sesión.
 */
const VERSION = 'localis-v1';
const CACHE_ESTATICOS = `${VERSION}-estaticos`;
const CACHE_PAGINAS = `${VERSION}-paginas`;

const PRECACHE = [
  '/offline',
  '/static/manifest.json',
  '/static/css/responsive.css',
  '/static/css/flash.css',
  '/static/js/localis.js',
  '/static/js/flash.js',
  '/static/js/header-smart-hide.js',
  '/static/images/logo.png',
  '/static/images/logo-192.png',
  '/static/images/logo-maskable-192.png',
  '/static/images/logo-maskable-512.png',
  '/static/images/apple-touch-icon-180.png',
];

const RUTAS_PRIVADAS = ['/api/', '/admin', '/comercio', '/perfil', '/login/google', '/logout'];

function esPrivada(pathname) {
  return RUTAS_PRIVADAS.some((p) => pathname === p || pathname.startsWith(p));
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_ESTATICOS).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((claves) =>
        Promise.all(
          claves
            .filter((k) => k !== CACHE_ESTATICOS && k !== CACHE_PAGINAS)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (esPrivada(url.pathname)) return;

  // Navegación (documentos HTML)
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((respuesta) => {
          const copia = respuesta.clone();
          caches.open(CACHE_PAGINAS).then((cache) => cache.put(request, copia)).catch(() => {});
          return respuesta;
        })
        .catch(() =>
          caches.match(request).then((enCache) => enCache || caches.match('/offline'))
        )
    );
    return;
  }

  // Estáticos: cache-first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(
        (enCache) =>
          enCache ||
          fetch(request).then((respuesta) => {
            if (respuesta && respuesta.status === 200 && respuesta.type === 'basic') {
              const copia = respuesta.clone();
              caches.open(CACHE_ESTATICOS).then((cache) => cache.put(request, copia)).catch(() => {});
            }
            return respuesta;
          })
      )
    );
    return;
  }

  // Resto: red con fallback a caché
  event.respondWith(fetch(request).catch(() => caches.match(request)));
});
