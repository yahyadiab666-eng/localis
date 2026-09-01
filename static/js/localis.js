/**
 * Utilidades globales Localis: alertas, límites de plan y pago móvil OCR.
 */

(function () {
  'use strict';

  const ALERTA_DURACION_MS = 4000;

  window.PLACEHOLDER_PRODUCTO = '/static/img/placeholder-producto.svg';

  window.urlImagenRespaldoProducto = function urlImagenRespaldoProducto(productoId) {
    if (!productoId) return window.PLACEHOLDER_PRODUCTO;
    return '/imagen-producto?producto_id=' + encodeURIComponent(productoId);
  };

  window.rescatarImagenProducto = function rescatarImagenProducto(img) {
    if (!img) return;
    img.onerror = null;
    var fallback = window.PLACEHOLDER_PRODUCTO;
    if (img.getAttribute('src') !== fallback) {
      img.src = fallback;
      return;
    }
    img.removeAttribute('src');
    var wrap = img.closest('.localis-img-producto-wrap');
    if (wrap) {
      wrap.classList.add('localis-img-producto-wrap--vacio');
      return;
    }
    if (img.classList.contains('localis-img-producto-thumb')) {
      img.classList.add('localis-img-producto-thumb--vacio');
    }
  };

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
      contenedor.className = 'fixed top-4 right-4 z-[300] space-y-2 flash-mobile';
      document.body.appendChild(contenedor);
    }

    const colores = {
      error: 'bg-red-500',
      exito: 'bg-emerald-600',
      info: 'bg-amber-600',
    };

    const alerta = document.createElement('div');
    alerta.className =
      'localis-flash-alerta flash-auto-dismiss p-4 rounded-xl shadow-lg text-white text-sm flex items-center gap-3 transition-opacity duration-500 ' +
      (colores[tipo] || colores.info);
    alerta.textContent = mensaje;
    alerta.style.opacity = '1';
    contenedor.appendChild(alerta);

    window.setTimeout(function () {
      alerta.classList.add('localis-flash-saliendo');
    }, ALERTA_DURACION_MS - 500);

    window.setTimeout(function () {
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
      modal.className = 'modal-push activo';
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
        window.location.href = '/comercio/planes?abrir_pago=pro';
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
    if (typeof window.inicializarAlertasAutoOcultas === 'function') {
      window.inicializarAlertasAutoOcultas();
    }
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

  function mostrarExitoPago(contenedorFormulario, contenedorExito, datos) {
    if (contenedorFormulario) contenedorFormulario.classList.add('hidden');
    if (contenedorExito) contenedorExito.classList.remove('hidden');
    actualizarEstadoTiendaEnPantalla(datos);
    if (typeof window.cerrarModal === 'function') {
      window.setTimeout(function () {
        window.cerrarModal('modal-plan');
      }, 2500);
    }
  }

  function inicializarFormularioPagoMovil() {
    const formulario = document.getElementById('form-pago-movil');
    if (!formulario) return;

    const inputComprobante = document.getElementById('comprobante');
    const preview = document.getElementById('preview-comprobante');
    const botonOcr = document.getElementById('btn-verificar-pago');
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

      const planTipoInput = document.getElementById('input-plan-tipo');
      const planTipo = planTipoInput ? planTipoInput.value : '';
      const cotizacion = window.cotizacionPlanActual;
      const esDowngrade = cotizacion && cotizacion.tipo_cambio === 'downgrade';

      const archivo = inputComprobante && inputComprobante.files && inputComprobante.files[0];
      if (!esDowngrade && !archivo) {
        window.mostrarAlertaLocalis('Debes adjuntar la captura del comprobante de pago.', 'error');
        return;
      }

      if (botonOcr) botonOcr.disabled = true;
      if (textoBoton) {
        textoBoton.textContent = esDowngrade
          ? 'Programando cambio...'
          : 'Verificando comprobante...';
      }
      if (spinner) spinner.classList.remove('hidden');

      const formData = new FormData(formulario);
      if (archivo) {
        formData.set('comprobante', archivo);
      } else {
        formData.delete('comprobante');
      }
      formData.set('plan_tipo', planTipo);

      const endpoint = esDowngrade ? '/api/pagos/programar-cambio' : '/api/pagos/verificar';

      fetch(endpoint, {
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
              resultado.datos.mensaje || 'Cambio de plan procesado correctamente.',
              'exito'
            );
            if (esDowngrade) {
              window.setTimeout(function () {
                window.location.reload();
              }, 1200);
              return;
            }
            mostrarExitoPago(contenedorFormulario, contenedorExito, resultado.datos);
            return;
          }

          window.mostrarAlertaLocalis(
            resultado.datos.error || resultado.datos.mensaje || 'No se pudo procesar el cambio de plan.',
            'error'
          );
        })
        .catch(function () {
          window.mostrarAlertaLocalis('Error de conexión al procesar el cambio de plan.', 'error');
        })
        .finally(function () {
          if (botonOcr) botonOcr.disabled = false;
          if (textoBoton) {
            textoBoton.textContent = esDowngrade
              ? 'Programar cambio de plan'
              : 'Verificar comprobante y activar plan';
          }
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
            window.setTimeout(function () {
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

  function inicializarBusquedaProductosPanel() {
    const input = document.getElementById('busqueda-productos-panel');
    if (!input) return;

    const elementos = document.querySelectorAll('[data-producto-busqueda]');
    const contador = document.getElementById('busqueda-productos-contador');
    const sinResultados = document.getElementById('busqueda-sin-resultados');
    const total = elementos.length;

    input.addEventListener('input', function () {
      const termino = input.value.trim().toLowerCase();
      let visibles = 0;

      elementos.forEach(function (el) {
        const texto = (el.getAttribute('data-producto-busqueda') || '').toLowerCase();
        const coincide = !termino || texto.indexOf(termino) !== -1;
        el.classList.toggle('hidden', !coincide);
        if (coincide) visibles += 1;
      });

      if (contador) {
        if (termino) {
          contador.textContent = visibles + ' de ' + total + ' producto' + (total !== 1 ? 's' : '');
        } else {
          contador.textContent = total + ' producto' + (total !== 1 ? 's' : '');
        }
      }

      if (sinResultados) {
        sinResultados.classList.toggle('hidden', !termino || visibles > 0);
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    inicializarAlertasFlash();
    inicializarFormularioPagoMovil();
    inicializarFormularioProductoApi();
    inicializarBusquedaProductosPanel();
  });
})();
