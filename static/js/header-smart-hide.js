/**
 * Smart-Hide del header público.
 *
 * Oculta la barra (translateY(-100%)) al bajar por catálogos largos y la
 * vuelve a mostrar al subir, liberando espacio vertical en móvil.
 *
 * - Usa scroll con { passive: true } + requestAnimationFrame (sin jank).
 * - Nunca oculta si el menú del avatar está abierto, si hay foco dentro del
 *   header, ni si el usuario tiene activada la preferencia "reduced motion".
 * - Si no hay header público en la página, no hace nada.
 */
(function () {
  'use strict';

  var header = document.querySelector('.publico-header');
  if (!header) return;

  // Respetar accesibilidad: sin animación/ocultado si el usuario lo pide.
  try {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return;
    }
  } catch (e) {
    /* si falla matchMedia, seguimos con el comportamiento normal */
  }

  var UMBRAL = 12;        // desplazamiento mínimo (px) para reaccionar
  var MOSTRAR_HASTA = 80; // cerca del tope siempre visible
  var ultimoY = window.pageYOffset || document.documentElement.scrollTop || 0;
  var acumulado = 0;
  var oculto = false;
  var ticking = false;

  function mostrar() {
    if (!oculto) return;
    header.style.transform = '';
    oculto = false;
  }

  function ocultar() {
    if (oculto) return;
    header.style.transform = 'translateY(-100%)';
    oculto = true;
  }

  function menuAbiertoOConFoco() {
    var menu = document.getElementById('nav-cliente-menu');
    if (menu && !menu.classList.contains('hidden')) return true;
    var activo = document.activeElement;
    return !!(activo && header.contains(activo));
  }

  function revisar() {
    ticking = false;
    var y = window.pageYOffset || document.documentElement.scrollTop || 0;
    var delta = y - ultimoY;
    ultimoY = y;

    if (y <= MOSTRAR_HASTA) {
      acumulado = 0;
      mostrar();
      return;
    }

    if (menuAbiertoOConFoco()) {
      acumulado = 0;
      mostrar();
      return;
    }

    acumulado += delta;

    if (acumulado > UMBRAL) {
      ocultar();
      acumulado = 0;
    } else if (acumulado < -UMBRAL) {
      mostrar();
      acumulado = 0;
    }
  }

  window.addEventListener(
    'scroll',
    function () {
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(revisar);
      }
    },
    { passive: true }
  );

  // Al recibir foco (teclado) dentro del header, siempre visible.
  document.addEventListener('focusin', function (evento) {
    if (header.contains(evento.target)) mostrar();
  });

  // Mantener visible si el menú del avatar se abre.
  var menu = document.getElementById('nav-cliente-menu');
  if (menu && window.MutationObserver) {
    var observador = new MutationObserver(function () {
      if (!menu.classList.contains('hidden')) mostrar();
    });
    observador.observe(menu, { attributes: true, attributeFilter: ['class'] });
  }

  // Restaurar al volver del bfcache / cambio de tamaño.
  window.addEventListener('pageshow', mostrar);
  window.addEventListener('resize', mostrar);
})();
