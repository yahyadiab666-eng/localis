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

    // Rellena el enlace de Maps de la fila con la ubicación actual del usuario.
    function obtenerUbicacionActual(boton) {
      var fila = boton.closest('.ubicacion-fila');
      var campoMaps = fila ? fila.querySelector('input[name="ubicacion_maps"]') : null;
      if (!campoMaps) return;

      function ayudaManual(mensaje) {
        alert(mensaje + ' Puedes pegar el enlace de Google Maps a mano.');
        campoMaps.focus();
      }

      if (!navigator.geolocation) {
        ayudaManual('Tu navegador no soporta geolocalización.');
        return;
      }
      if (!window.isSecureContext) {
        ayudaManual('La ubicación solo funciona en conexiones seguras (https).');
        return;
      }

      var htmlOriginal = boton.innerHTML;
      function restaurar() {
        boton.disabled = false;
        boton.removeAttribute('aria-busy');
        boton.innerHTML = htmlOriginal;
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
          window.lucide.createIcons();
        }
      }

      boton.disabled = true;
      boton.setAttribute('aria-busy', 'true');
      boton.textContent = 'Obteniendo…';

      navigator.geolocation.getCurrentPosition(
        function (pos) {
          campoMaps.value =
            'https://www.google.com/maps?q=' +
            pos.coords.latitude.toFixed(6) +
            ',' +
            pos.coords.longitude.toFixed(6);
          campoMaps.dispatchEvent(new Event('input', { bubbles: true }));
          restaurar();
        },
        function (error) {
          restaurar();
          var codigo = error && error.code;
          if (codigo === 1) {
            ayudaManual('Permiso de ubicación denegado.');
          } else if (codigo === 2) {
            ayudaManual('Tu ubicación no está disponible (revisa el GPS o la conexión).');
          } else if (codigo === 3) {
            ayudaManual('Se agotó el tiempo al obtener tu ubicación.');
          } else {
            ayudaManual('No se pudo obtener tu ubicación.');
          }
        },
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
      );
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
