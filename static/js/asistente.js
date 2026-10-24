/* Asistente de IA de Localis: widget flotante (informativo, con datos en vivo).
 *
 * Vanilla JS, sin dependencias. Habla con:
 *   - /api/asistente/chat     (responder)
 *   - /api/asistente/escalar  (derivar a una persona -> ticket + WhatsApp)
 *   - /api/asistente/feedback (👍/👎)
 * Todo es defensivo: si falta un elemento o la red falla, se avisa en vez de
 * romper la pagina. El contexto de la conversacion vive AQUI (navegador).
 */
(function () {
  'use strict';

  var raiz = document.getElementById('asistente-widget');
  if (!raiz) return;

  var burbuja = document.getElementById('asistente-burbuja');
  var panel = document.getElementById('asistente-panel');
  var cerrar = document.getElementById('asistente-cerrar');
  var lista = document.getElementById('asistente-mensajes');
  var form = document.getElementById('asistente-form');
  var entrada = document.getElementById('asistente-entrada');
  var enviar = document.getElementById('asistente-enviar');
  var soporte = document.getElementById('asistente-soporte');
  var feedback = document.getElementById('asistente-feedback');

  if (!burbuja || !panel || !lista || !form || !entrada) return;

  var CHAT_URL = raiz.getAttribute('data-chat-url') || '/api/asistente/chat';
  var ESCALAR_URL = raiz.getAttribute('data-escalar-url') || '/api/asistente/escalar';
  var FEEDBACK_URL = raiz.getAttribute('data-feedback-url') || '/api/asistente/feedback';
  var CSRF = raiz.getAttribute('data-csrf') || '';
  var ROL = raiz.getAttribute('data-rol') || 'invitado';
  var WHATSAPP = raiz.getAttribute('data-whatsapp') || '';
  var LOGUEADO = raiz.getAttribute('data-logueado') === '1';
  var ACCION_OK_URL = raiz.getAttribute('data-accion-confirmar-url') || '/api/asistente/accion/confirmar';
  var ACCION_NO_URL = raiz.getAttribute('data-accion-cancelar-url') || '/api/asistente/accion/cancelar';
  var MAX_HISTORIAL = 8;
  var CSRF_URL = raiz.getAttribute('data-csrf-url') || '/api/asistente/csrf';
  // El token ya no viaja en el HTML (la pagina publica debe poder cachearse en
  // un CDN): se pide una sola vez y se reutiliza. Se pide en paralelo al
  // arranque del widget, asi que al primer clic suele estar ya listo.
  var csrfListo = CSRF ? Promise.resolve(CSRF) : cargarCsrf();

  function cargarCsrf() {
    return fetch(CSRF_URL, {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { CSRF = (d && d.csrf_token) || ''; return CSRF; })
      .catch(function () { return ''; });
  }

  function peticionAsistente(url, opciones) {
    return csrfListo.then(function (token) {
      var base = Object.assign({}, opciones || {});
      base.credentials = 'same-origin';
      base.headers = Object.assign({}, (opciones && opciones.headers) || {}, {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRFToken': token || CSRF || ''
      });
      return fetch(url, base);
    });
  }

  var SALUDO = {
    cliente: 'Hola, soy el asistente de Localis. Te doy informacion y guia. ¿Que necesitas?',
    invitado: 'Hola, soy el asistente de Localis. Puedo darte informacion para buscar productos o usar la plataforma.',
    comerciante: 'Hola. Te doy informacion sobre tu tienda: inventario, imagenes, planes y pagos.',
    admin: 'Hola. Puedo darte informacion sobre la operacion y la bandeja tecnica.'
  };

  var historial = [];
  var esperando = false;

  function iconos() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  }

  function cabeceras() {
    return {
      'Content-Type': 'application/json',
      'Accept': 'application/json',
      'X-CSRFToken': CSRF
    };
  }

  function abrir() {
    panel.hidden = false;
    burbuja.setAttribute('aria-expanded', 'true');
    document.body.classList.add('asistente-abierto');
    if (!lista.dataset.iniciado) {
      lista.dataset.iniciado = '1';
      agregarMensaje(SALUDO[ROL] || SALUDO.invitado, 'asistente');
    }
    entrada.focus();
  }

  function cerrarPanel() {
    panel.hidden = true;
    burbuja.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('asistente-abierto');
    burbuja.focus();
  }

  function agregarMensaje(texto, tipo) {
    var div = document.createElement('div');
    div.className = 'asistente-msg asistente-msg--' + (tipo || 'asistente');
    div.textContent = texto;
    lista.appendChild(div);
    lista.scrollTop = lista.scrollHeight;
    return div;
  }

  function mostrarEscribiendo() {
    var div = document.createElement('div');
    div.className = 'asistente-msg asistente-msg--asistente';
    div.id = 'asistente-typing';
    div.innerHTML =
      '<span class="asistente-escribiendo"><span></span><span></span><span></span></span>';
    lista.appendChild(div);
    lista.scrollTop = lista.scrollHeight;
  }

  function quitarEscribiendo() {
    var div = document.getElementById('asistente-typing');
    if (div && div.parentNode) div.parentNode.removeChild(div);
  }

  function bloquear(estado) {
    esperando = estado;
    if (enviar) enviar.disabled = estado;
  }

  function enviarMensaje() {
    if (esperando) return;
    var texto = (entrada.value || '').trim();
    if (!texto) return;

    agregarMensaje(texto, 'usuario');
    entrada.value = '';
    autoAlto();
    bloquear(true);
    mostrarEscribiendo();
    if (feedback) feedback.hidden = true;

    peticionAsistente(CHAT_URL, {
      method: 'POST',
      headers: cabeceras(),
      body: JSON.stringify({ mensaje: texto, historial: historial.slice(-MAX_HISTORIAL) })
    })
      .then(function (resp) {
        return resp.json().catch(function () {
          return { ok: false, mensaje: 'No pude leer la respuesta del asistente.' };
        });
      })
      .then(function (datos) {
        quitarEscribiendo();
        datos = datos || {};
        if (datos.ok && datos.respuesta) {
          agregarMensaje(datos.respuesta, 'asistente');
          historial.push({ autor: 'usuario', contenido: texto });
          historial.push({ autor: 'asistente', contenido: datos.respuesta });
          historial = historial.slice(-MAX_HISTORIAL);
          if (feedback) feedback.hidden = false;
          if (datos.accion) mostrarAccion(datos.accion);
        } else {
          agregarMensaje(
            datos.mensaje || 'No pude responder ahora mismo. Intenta de nuevo.',
            'error'
          );
        }
      })
      .catch(function () {
        quitarEscribiendo();
        agregarMensaje('Error de conexion con el asistente. Revisa tu internet.', 'error');
      })
      .then(function () {
        bloquear(false);
        entrada.focus();
      });
  }

  function autoAlto() {
    entrada.style.height = 'auto';
    entrada.style.height = Math.min(entrada.scrollHeight, 120) + 'px';
  }

  function mostrarAccion(accion) {
    if (!accion || !accion.id) return;
    var caja = document.createElement('div');
    caja.className = 'asistente-accion';

    var texto = document.createElement('div');
    texto.className = 'asistente-accion__texto';
    texto.textContent = accion.resumen || 'Accion pendiente de confirmar';

    var botones = document.createElement('div');
    botones.className = 'asistente-accion__botones';
    var confirmar = document.createElement('button');
    confirmar.type = 'button';
    confirmar.className = 'asistente-accion__ok';
    confirmar.textContent = 'Confirmar';
    var cancelar = document.createElement('button');
    cancelar.type = 'button';
    cancelar.className = 'asistente-accion__no';
    cancelar.textContent = 'Cancelar';
    botones.appendChild(confirmar);
    botones.appendChild(cancelar);

    caja.appendChild(texto);
    caja.appendChild(botones);
    lista.appendChild(caja);
    lista.scrollTop = lista.scrollHeight;

    var bloquearBotones = function () {
      confirmar.disabled = true;
      cancelar.disabled = true;
    };

    confirmar.addEventListener('click', function () {
      bloquearBotones();
      peticionAsistente(ACCION_OK_URL, {
        method: 'POST',
        headers: cabeceras(),
        body: JSON.stringify({ id: accion.id })
      })
        .then(function (r) {
          return r.json().catch(function () { return {}; });
        })
        .then(function (datos) {
          if (caja.parentNode) caja.parentNode.removeChild(caja);
          agregarMensaje(
            (datos && datos.mensaje) || 'No pude aplicar el cambio.',
            datos && datos.ok ? 'asistente' : 'error'
          );
        })
        .catch(function () {
          if (caja.parentNode) caja.parentNode.removeChild(caja);
          agregarMensaje('No pude aplicar el cambio. Intenta de nuevo.', 'error');
        });
    });

    cancelar.addEventListener('click', function () {
      bloquearBotones();
      peticionAsistente(ACCION_NO_URL, {
        method: 'POST',
        headers: cabeceras(),
        body: JSON.stringify({ id: accion.id })
      }).catch(function () {});
      if (caja.parentNode) caja.parentNode.removeChild(caja);
      agregarMensaje('Cancelado: no cambie nada.', 'asistente');
    });
  }

  function enviarFeedback(util) {
    if (feedback) feedback.hidden = true;
    peticionAsistente(FEEDBACK_URL, {
      method: 'POST',
      headers: cabeceras(),
      body: JSON.stringify({ util: util })
    }).catch(function () {});
  }

  function abrirSoporte() {
    var cuerpo = {
      motivo: 'El usuario pidio hablar con una persona.',
      historial: historial.slice(-MAX_HISTORIAL)
    };
    var continuar = function () {
      peticionAsistente(ESCALAR_URL, {
        method: 'POST',
        headers: cabeceras(),
        body: JSON.stringify(cuerpo)
      })
        .then(function (r) {
          return r.json().catch(function () { return {}; });
        })
        .then(function (datos) {
          var url = (datos && datos.whatsapp_url) || WHATSAPP;
          if (url) window.open(url, '_blank', 'noopener');
          agregarMensaje(
            datos && datos.ticket_creado
              ? 'Abrimos WhatsApp y registramos tu caso para seguimiento. La atencion puede tardar un poco.'
              : 'Abrimos WhatsApp con el soporte. La atencion puede tardar un poco.',
            'asistente'
          );
        })
        .catch(function () {
          if (WHATSAPP) window.open(WHATSAPP, '_blank', 'noopener');
        });
    };

    if (!LOGUEADO) {
      var correo = window.prompt(
        'Para dar seguimiento a tu caso, dejamos tu correo (opcional):'
      );
      if (correo) cuerpo.correo = correo;
    }
    continuar();
  }

  burbuja.addEventListener('click', function () {
    if (panel.hidden) {
      abrir();
    } else {
      cerrarPanel();
    }
  });
  if (cerrar) cerrar.addEventListener('click', cerrarPanel);
  if (soporte) soporte.addEventListener('click', abrirSoporte);
  if (feedback) {
    feedback.addEventListener('click', function (evento) {
      var boton = evento.target.closest('button[data-util]');
      if (!boton) return;
      enviarFeedback(boton.getAttribute('data-util') === '1');
    });
  }

  form.addEventListener('submit', function (evento) {
    evento.preventDefault();
    enviarMensaje();
  });

  entrada.addEventListener('input', autoAlto);
  entrada.addEventListener('keydown', function (evento) {
    if (evento.key === 'Enter' && !evento.shiftKey) {
      evento.preventDefault();
      enviarMensaje();
    }
  });

  document.addEventListener('keydown', function (evento) {
    if (evento.key === 'Escape' && !panel.hidden) cerrarPanel();
  });

  iconos();
})();