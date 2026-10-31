/* Service Worker de Localis (PWA).
 *
 * Estrategias:
 *  - App shell / navegación pública: network-first con fallback a caché y a /offline.
 *  - Estáticos (/static/): **stale-while-revalidate** (se sirve la copia y se
 *    actualiza en segundo plano). Antes era cache-first puro y, con URLs de
 *    versión fijas, un teléfono podía quedarse para siempre con un CSS viejo
 *    (el panel admin salía sin estilos).
 *  - API y zonas privadas: solo red (nunca se cachean).
 *
 * Reglas de seguridad: no se cachea nada de /api/, /admin, /comercio, /perfil,
 * /login/google ni /logout para no exponer datos de sesión.
 */
const VERSION = 'localis-v2';
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
          // No cachear respuestas degeneradas (BD no disponible): evita
          // "envenenar" la portada con una lista vacía tras inactividad.
          const cacheable =
            respuesta.status === 200 &&
            respuesta.headers.get('X-Localis-No-Cache') !== '1';
          if (cacheable) {
            const copia = respuesta.clone();
            caches.open(CACHE_PAGINAS).then((cache) => cache.put(request, copia)).catch(() => {});
          }
          return respuesta;
        })
        .catch(() =>
          caches.match(request).then((enCache) => enCache || caches.match('/offline'))
        )
    );
    return;
  }

  // Estáticos: stale-while-revalidate. Se responde al instante con la copia
  // (si la hay) y se pide una versión nueva para la próxima visita, así nunca
  // queda clavado un CSS/JS viejo.
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((enCache) => {
        // La red se pide SIEMPRE (actualiza la copia aunque ya haya caché).
        const red = fetch(request)
          .then((respuesta) => {
            if (respuesta && respuesta.status === 200 && respuesta.type === 'basic') {
              const copia = respuesta.clone();
              caches
                .open(CACHE_ESTATICOS)
                .then((cache) => cache.put(request, copia))
                .catch(() => {});
            }
            return respuesta;
          })
          .catch(() => enCache || Response.error());

        // Con copia: se sirve ya; sin copia: se espera a la red.
        return enCache || red;
      })
    );
    return;
  }

  // Resto: red con fallback a caché
  event.respondWith(fetch(request).catch(() => caches.match(request)));
});
