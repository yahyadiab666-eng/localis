/**
 * Utilidades globales Localis: alertas, límites de plan y pago móvil OCR.
 */

(function () {
  'use strict';

  const ALERTA_DURACION_MS = 4000;

  function obtenerCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute('content');
    const input = document.querySelector('input[name="csrf_token"]');
    return input ? input.value : '';
  }

  window.mostrarAlertaLocalis = function mostrarAlertaLocalis(mensaje, tipo) {
    const contenedorId = 'localis-alertas-flotantes';
    let contenedor = document.getElementById(contenedorId);
    if (!contenedor) {
      contenedor = document.createElement('div');
      contenedor.id = contenedorId;
      contenedor.className = 'fixed top-4 right-4 z-[300] space-y-2';
      document.body.appendChild(contenedor);
    }

    const colores = {
      error: 'bg-red-500',
      exito: 'bg-emerald-600',
      info: 'bg-amber-600',
    };

    const alerta = document.createElement('div');
    alerta.className =
      'localis-alerta p-4 rounded-xl shadow-lg text-white text-sm flex items-center gap-3 transition-opacity duration-500 ' +
      (colores[tipo] || colores.info);
    alerta.textContent = mensaje;
    alerta.style.opacity = '1';
    contenedor.appendChild(alerta);

    setTimeout(function () {
      alerta.style.opacity = '0';
    }, ALERTA_DURACION_MS - 500);

    setTimeout(function () {
      alerta.remove();
      if (!contenedor.children.length) {
        contenedor.remove();
      }
    }, ALERTA_DURACION_MS);
  };

  window.mostrarModalLimitePlan = function mostrarModalLimitePlan(mensaje) {
    let modal = document.getElementById('modal-limite-plan');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'modal-limite-plan';
      modal.className =
        'modal-push activo';
      modal.innerHTML =
        '<div class="panel p-6 space-y-4 max-w-md">' +
        '<div class="text-center">' +
        '<div class="w-14 h-14 bg-amber-100 rounded-2xl flex items-center justify-center mx-auto mb-3 text-2xl">⚠️</div>' +
        '<h3 class="font-display text-xl font-bold text-gray-900">Límite de productos alcanzado</h3>' +
        '<p id="modal-limite-plan-texto" class="text-stone-600 text-sm mt-2"></p>' +
        '</div>' +
        '<button type="button" id="modal-limite-plan-cerrar" class="w-full py-3 rounded-full font-bold text-sm bg-amber-500 text-stone-900">Ver planes y suscripción</button>' +
        '</div>';
      document.body.appendChild(modal);

      modal.addEventListener('click', function (evento) {
        if (evento.target === modal) {
          modal.classList.remove('activo');
        }
      });

      document.getElementById('modal-limite-plan-cerrar').addEventListener('click', function () {
        modal.classList.remove('activo');
        if (typeof window.abrirModalPlan === 'function') {
          window.abrirModalPlan('pro');
        }
      });
    }

    const texto = document.getElementById('modal-limite-plan-texto');
    if (texto) {
      texto.textContent =
        mensaje ||
        'Has alcanzado el límite de productos de tu plan actual. Actualiza tu suscripción para seguir publicando.';
    }
    modal.classList.add('activo');
  };

  function inicializarAlertasFlash() {
    document.querySelectorAll('.localis-flash-alerta').forEach(function (nodo) {
      setTimeout(function () {
        nodo.style.opacity = '0';
      }, ALERTA_DURACION_MS - 500);
      setTimeout(function () {
        nodo.remove();
      }, ALERTA_DURACION_MS);
    });
  }

  function actualizarEstadoTiendaEnPantalla(datos) {
    if (!datos) return;

    document.querySelectorAll('[data-estado-suscripcion]').forEach(function (nodo) {
      nodo.textContent = datos.estado || 'activo';
    });

    document.querySelectorAll('[data-fecha-vencimiento]').forEach(function (nodo) {
      if (datos.fecha_vencimiento) {
        nodo.textContent = datos.fecha_vencimiento;
      }
    });
  }

  function inicializarFormularioPagoMovil() {
    const formulario = document.getElementById('form-pago-movil');
    if (!formulario) return;

    const inputComprobante = document.getElementById('comprobante');
    const preview = document.getElementById('preview-comprobante');
    const boton = document.getElementById('btn-verificar-pago');
    const textoBoton = document.getElementById('btn-verificar-pago-texto');
    const spinner = document.getElementById('btn-verificar-pago-spinner');
    const contenedorFormulario = document.getElementById('contenedor-form-pago');
    const contenedorExito = document.getElementById('contenedor-pago-exito');

    if (inputComprobante && preview) {
      inputComprobante.addEventListener('change', function () {
        preview.innerHTML = '';
        const archivo = inputComprobante.files && inputComprobante.files[0];
        if (!archivo) return;

        const imagen = document.createElement('img');
        imagen.src = URL.createObjectURL(archivo);
        imagen.className = 'max-h-48 rounded-xl border border-stone-200 mx-auto';
        imagen.onload = function () {
          URL.revokeObjectURL(imagen.src);
        };
        preview.appendChild(imagen);
      });
    }

    formulario.addEventListener('submit', function (evento) {
      evento.preventDefault();

      const archivo = inputComprobante && inputComprobante.files && inputComprobante.files[0];
      if (!archivo) {
        window.mostrarAlertaLocalis('Selecciona la captura del comprobante.', 'error');
        return;
      }

      if (boton) boton.disabled = true;
      if (textoBoton) textoBoton.textContent = 'Verificando pago...';
      if (spinner) spinner.classList.remove('hidden');

      const formData = new FormData(formulario);
      formData.set('comprobante', archivo);

      fetch('/api/pagos/verificar', {
        method: 'POST',
        headers: {
          'X-CSRFToken': obtenerCsrfToken(),
        },
        body: formData,
        credentials: 'same-origin',
      })
        .then(function (respuesta) {
          return respuesta.json().then(function (datos) {
            return { ok: respuesta.ok, status: respuesta.status, datos: datos };
          });
        })
        .then(function (resultado) {
          if (resultado.ok) {
            window.mostrarAlertaLocalis(
              resultado.datos.mensaje || 'Pago verificado correctamente.',
              'exito'
            );
            if (contenedorFormulario) contenedorFormulario.classList.add('hidden');
            if (contenedorExito) contenedorExito.classList.remove('hidden');
            actualizarEstadoTiendaEnPantalla(resultado.datos);
            if (typeof window.cerrarModal === 'function') {
              setTimeout(function () {
                window.cerrarModal('modal-plan');
              }, 2500);
            }
            return;
          }

          window.mostrarAlertaLocalis(
            resultado.datos.error || resultado.datos.mensaje || 'No se pudo verificar el pago.',
            'error'
          );
        })
        .catch(function () {
          window.mostrarAlertaLocalis('Error de conexión al verificar el pago.', 'error');
        })
        .finally(function () {
          if (boton) boton.disabled = false;
          if (textoBoton) textoBoton.textContent = 'Verificar comprobante y activar plan';
          if (spinner) spinner.classList.add('hidden');
        });
    });
  }

  function inicializarFormularioProductoApi() {
    const formulario = document.getElementById('form-producto-api');
    if (!formulario) return;

    formulario.addEventListener('submit', function (evento) {
      evento.preventDefault();

      const boton = formulario.querySelector('button[type="submit"]');
      if (boton) boton.disabled = true;

      const formData = new FormData(formulario);

      fetch('/api/productos/crear', {
        method: 'POST',
        headers: {
          'X-CSRFToken': obtenerCsrfToken(),
        },
        body: formData,
        credentials: 'same-origin',
      })
        .then(function (respuesta) {
          return respuesta.json().then(function (datos) {
            return { ok: respuesta.ok, status: respuesta.status, datos: datos };
          });
        })
        .then(function (resultado) {
          if (resultado.ok) {
            window.mostrarAlertaLocalis(
              resultado.datos.mensaje || 'Producto agregado con éxito.',
              'exito'
            );
            setTimeout(function () {
              window.location.href = formulario.dataset.redirect || '/comercio';
            }, 900);
            return;
          }

          const mensaje =
            resultado.datos.error ||
            resultado.datos.mensaje ||
            'No se pudo crear el producto.';

          if (resultado.status === 400 && mensaje.indexOf('límite de productos') !== -1) {
            window.mostrarModalLimitePlan(mensaje);
          } else {
            window.mostrarAlertaLocalis(mensaje, 'error');
          }
        })
        .catch(function () {
          window.mostrarAlertaLocalis('Error de conexión al crear el producto.', 'error');
        })
        .finally(function () {
          if (boton) boton.disabled = false;
        });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    inicializarAlertasFlash();
    inicializarFormularioPagoMovil();
    inicializarFormularioProductoApi();
  });
})();
