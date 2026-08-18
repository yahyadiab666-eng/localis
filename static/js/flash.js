/**
 * Auto-oculta alertas flash después de unos segundos en todas las vistas.
 */
(function () {
  'use strict';

  var DURACION_VISIBLE_MS = 4500;
  var DURACION_FADE_MS = 450;

  var SELECTOR_ALERTAS =
    '.localis-flash-alerta, .flash-auto-dismiss, .flash-mobile > div, ' +
    '.alertas-contenedor .alerta, .login-card .alerta';

  function ocultarAlerta(nodo) {
    if (!nodo || nodo.classList.contains('localis-flash-saliendo')) {
      return;
    }
    if (nodo.classList.contains('flash-persist')) {
      return;
    }
    nodo.classList.add('localis-flash-saliendo');
    window.setTimeout(function () {
      var contenedor = nodo.parentElement;
      nodo.remove();
      if (
        contenedor &&
        (contenedor.classList.contains('flash-mobile') ||
          contenedor.classList.contains('alertas-contenedor')) &&
        !contenedor.children.length
      ) {
        contenedor.remove();
      }
    }, DURACION_FADE_MS);
  }

  function inicializarAlertasAutoOcultas() {
    document.querySelectorAll(SELECTOR_ALERTAS).forEach(function (nodo) {
      window.setTimeout(function () {
        ocultarAlerta(nodo);
      }, DURACION_VISIBLE_MS);
    });
  }

  window.inicializarAlertasAutoOcultas = inicializarAlertasAutoOcultas;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inicializarAlertasAutoOcultas);
  } else {
    inicializarAlertasAutoOcultas();
  }
})();
