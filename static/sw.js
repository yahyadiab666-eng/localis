/* Service Worker básico de Localis (PWA).
 *
 * Estrategia: SIN caché (passthrough). No usa respondWith, por lo que el
 * navegador resuelve cada petición normalmente contra la red. Así se evita
 * interferir con sesiones, contenido dinámico de Flask ni imágenes.
 */
self.addEventListener('install', function () {
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', function () {
  // Passthrough intencional: no interceptamos respuestas.
});
