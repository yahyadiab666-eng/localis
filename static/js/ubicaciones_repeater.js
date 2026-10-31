/* Repeater de ubicaciones (sedes) para formularios de comercio.
 *
 * Cada fila declara un nombre de sede y su enlace de Google Maps. La primera
 * fila es la principal. Se usa junto a una <template id="plantilla-ubicacion">
 * y una lista <div id="ubicaciones-lista">.
 *
 * Expone window.LocalisUbicaciones = { agregar, contar } para que cada plantilla
 * precargue las sedes existentes.
 */
(function () {
  'use strict';

  var MAX_UBICACIONES = 10;

  function iniciar() {
    var lista = document.getElementById('ubicaciones-lista');
    var plantilla = document.getElementById('plantilla-ubicacion');
    if (!lista || !plantilla) return;

    function filas() {
      return lista.querySelectorAll('.ubicacion-fila');
    }

    function actualizarEstado() {
      var total = filas().length;
      var boton = document.getElementById('btn-agregar-ubicacion');
      if (boton) {
        var lleno = total >= MAX_UBICACIONES;
        boton.disabled = lleno;
        boton.classList.toggle('opacity-50', lleno);
        boton.classList.toggle('cursor-not-allowed', lleno);
      }
      // La primera fila es la principal: se resalta su etiqueta.
      filas().forEach(function (fila, indice) {
        var etiqueta = fila.querySelector('[data-rol="principal"]');
        if (etiqueta) etiqueta.style.display = indice === 0 ? '' : 'none';
      });
    }

    function agregar(nombre, mapsUrl) {
      if (filas().length >= MAX_UBICACIONES) return null;
      var nodo = plantilla.content.firstElementChild.cloneNode(true);
      var inputNombre = nodo.querySelector('input[name="ubicacion_nombre"]');
      var inputMaps = nodo.querySelector('input[name="ubicacion_maps"]');
      if (inputNombre) inputNombre.value = nombre || '';
      if (inputMaps) inputMaps.value = mapsUrl || '';
      lista.appendChild(nodo);
      actualizarEstado();
      if (window.lucide && typeof window.lucide.createIcons === 'function') {
        window.lucide.createIcons();
      }
      return nodo;
    }

    // --- Ubicación actual -------------------------------------------------
    // En el teléfono (y sobre todo dentro de la app/TWA) el GPS de alta
    // precisión puede tardar o fallar bajo techo: se intenta primero con GPS y,
    // si no responde en 12 s, se reintenta con la ubicación de red. Cada paso se
    // informa en la propia fila (nada de alerts que parecen "no hacer nada").
    function cajaEstado(fila) {
      var caja = fila.querySelector('[data-ubicacion-estado]');
      if (caja) return caja;
      caja = document.createElement('p');
      caja.setAttribute('data-ubicacion-estado', '');
      caja.setAttribute('role', 'status');
      caja.setAttribute('aria-live', 'polite');
      caja.style.cssText = 'font-size:0.75rem;margin-top:0.35rem;line-height:1.35;';
      var campo = fila.querySelector('input[name="ubicacion_maps"]');
      if (campo && campo.parentNode) {
        campo.parentNode.insertBefore(caja, campo.nextSibling);
      } else {
        fila.appendChild(caja);
      }
      return caja;
    }

    function pintarEstado(fila, texto, tipo) {
      var caja = cajaEstado(fila);
      caja.textContent = texto;
      caja.style.color =
        tipo === 'error' ? '#b02a37' : tipo === 'ok' ? '#047857' : '#6c757d';
    }

    function pedirCoordenadas(altaPrecision, timeoutSeg, callback) {
      try {
        navigator.geolocation.getCurrentPosition(
          function (posicion) {
            callback(null, posicion);
          },
          function (error) {
            callback(error || { code: 2 }, null);
          },
          {
            enableHighAccuracy: !!altaPrecision,
            timeout: Math.max(5, timeoutSeg) * 1000,
            maximumAge: altaPrecision ? 0 : 60000,
          }
        );
      } catch (error) {
        callback({ code: 2, message: String(error) }, null);
      }
    }

    function permisoBloqueado(callback) {
      if (!navigator.permissions || !navigator.permissions.query) {
        callback(false);
        return;
      }
      try {
        navigator.permissions
          .query({ name: 'geolocation' })
          .then(function (estado) {
            callback(estado && estado.state === 'denied');
          })
          .catch(function () {
            callback(false);
          });
      } catch (error) {
        callback(false);
      }
    }

    // Rellena el enlace de Maps de la fila con la ubicación actual del usuario.
    function obtenerUbicacionActual(boton) {
      var fila = boton.closest('.ubicacion-fila');
      var campoMaps = fila ? fila.querySelector('input[name="ubicacion_maps"]') : null;
      if (!fila || !campoMaps) return;

      var htmlOriginal = boton.innerHTML;
      function restaurar() {
        boton.disabled = false;
        boton.removeAttribute('aria-busy');
        boton.innerHTML = htmlOriginal;
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
          window.lucide.createIcons();
        }
      }

      function fallo(mensaje, conFoco) {
        restaurar();
        pintarEstado(fila, mensaje, 'error');
        if (conFoco) campoMaps.focus();
      }

      // No se consulta el permiso ANTES de intentarlo: en la app/TWA (Custom
      // Tabs) la Permissions API puede decir "denied" aunque el aviso nativo
      // funcione. Se intenta siempre y, si falla por permiso, se afina el mensaje.
      function denegado() {
        permisoBloqueado(function (bloqueado) {
          fallo(
            bloqueado
              ? 'El navegador tiene bloqueada la ubicación para este sitio. Ábrelo en ' +
                  'el candado de la barra de direcciones (o en Ajustes → Aplicaciones → ' +
                  'Chrome → Permisos → Ubicación) y permite el acceso; luego intenta otra vez.'
              : 'No diste permiso de ubicación. Si no apareció el aviso, actívalo en el ' +
                  'candado de la barra de direcciones (o en los ajustes de la app) y ' +
                  'vuelve a tocar "Usar mi ubicación actual".',
            false
          );
        });
      }

      function listo(posicion) {
        var coords = posicion.coords || {};
        if (typeof coords.latitude !== 'number' || typeof coords.longitude !== 'number') {
          fallo('No se recibieron coordenadas válidas.', false);
          return;
        }
        var url =
          'https://www.google.com/maps?q=' +
          coords.latitude.toFixed(6) +
          ',' +
          coords.longitude.toFixed(6);
        campoMaps.value = url;
        campoMaps.dispatchEvent(new Event('input', { bubbles: true }));
        restaurar();
        var precision = Math.round(coords.accuracy || 0);
        var aviso =
          precision > 300
            ? ' (aproximada ±' + precision + ' m; si está muy lejos, acércate a una ventana o activa el GPS)'
            : precision
              ? ' (±' + precision + ' m)'
              : '';
        pintarEstado(fila, 'Ubicación lista' + aviso + '. Guarda para aplicarla. ', 'ok');
        // Enlace para comprobar el punto sin salir de la pantalla.
        var ver = document.createElement('a');
        ver.href = url;
        ver.target = '_blank';
        ver.rel = 'noopener';
        ver.textContent = 'Ver en el mapa';
        ver.style.cssText = 'color:#0d6efd;font-weight:600;text-decoration:underline;';
        cajaEstado(fila).appendChild(ver);
      }

      if (!navigator.geolocation) {
        fallo('Este navegador no soporta geolocalización. Pega el enlace de Google Maps a mano.', true);
        return;
      }
      if (!window.isSecureContext) {
        fallo('La ubicación solo funciona en conexiones seguras (https). Pega el enlace a mano.', true);
        return;
      }

      boton.disabled = true;
      boton.setAttribute('aria-busy', 'true');
      boton.textContent = 'Obteniendo…';
      pintarEstado(fila, 'Pidiendo permiso y buscando tu ubicación…', 'info');

      var intentar = function () {
        pedirCoordenadas(true, 12, function (errorGps, posicionGps) {
          if (posicionGps) {
            listo(posicionGps);
            return;
          }
          if (errorGps && errorGps.code === 1) {
            denegado();
            return;
          }
          // El GPS no respondió (bajo techo / sin satélites): ubicación de red.
          pintarEstado(fila, 'El GPS no respondió; probando con la red…', 'info');
          pedirCoordenadas(false, 20, function (errorRed, posicionRed) {
            if (posicionRed) {
              listo(posicionRed);
              return;
            }
            if (errorRed && errorRed.code === 1) {
              denegado();
              return;
            }
            if (errorRed && errorRed.code === 3) {
              fallo(
                'Se agotó el tiempo buscando la señal. Activa la ubicación del ' +
                  'teléfono, sal a un lugar abierto e intenta de nuevo.',
                false
              );
              return;
            }
            fallo(
              'Tu ubicación no está disponible ahora. Revisa que la ubicación del ' +
                'teléfono esté encendida y que la app tenga permiso; si sigue igual, ' +
                'pega el enlace de Google Maps a mano.',
              true
            );
          });
        });
      };

      intentar();
    }

    lista.addEventListener('click', function (evento) {
      var quitar = evento.target.closest('.btn-quitar-ubicacion');
      if (quitar) {
        if (filas().length <= 1) {
          alert('Debe quedar al menos una ubicación.');
          return;
        }
        var fila = quitar.closest('.ubicacion-fila');
        if (fila) fila.remove();
        actualizarEstado();
        return;
      }
      var usarActual = evento.target.closest('.btn-ubicacion-actual');
      if (usarActual) {
        obtenerUbicacionActual(usarActual);
      }
    });

    var botonAgregar = document.getElementById('btn-agregar-ubicacion');
    if (botonAgregar) {
      botonAgregar.addEventListener('click', function () {
        agregar('', '');
      });
    }

    window.LocalisUbicaciones = {
      agregar: agregar,
      contar: function () {
        return filas().length;
      },
      actualizarEstado: actualizarEstado
    };

    actualizarEstado();
  }

  iniciar();
})();
