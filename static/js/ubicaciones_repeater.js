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
        var filaSede = usarActual.closest('.ubicacion-fila');
        var campoMaps = filaSede ? filaSede.querySelector('input[name="ubicacion_maps"]') : null;
        if (!campoMaps) return;
        if (!navigator.geolocation) {
          alert('Tu navegador no soporta geolocalización.');
          return;
        }
        navigator.geolocation.getCurrentPosition(
          function (pos) {
            campoMaps.value =
              'https://www.google.com/maps?q=' +
              pos.coords.latitude +
              ',' +
              pos.coords.longitude;
          },
          function () {
            alert('No se pudo obtener tu ubicación. Verifica los permisos del navegador.');
          }
        );
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
