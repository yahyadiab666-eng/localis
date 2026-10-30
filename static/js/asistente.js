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
  var FLUJO_URL = raiz.getAttribute('data-flujo-url') || '/api/asistente/chat/flujo';
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
  // Cierra el turno actual (guarda historial, feedback y accion). Se reasigna en
  // cada envio para conocer el texto exacto que se envio.
  var cerrarTurno = function () {};

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

  function escaparHtml(texto) {
    return String(texto == null ? '' : texto).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // El modelo responde con markdown ligero (**negrita**, `codigo`, vinetas y
  // numeraciones). Se escapa TODO primero y despues se aplica un subconjunto
  // seguro, de modo que nunca se inyecta HTML: solo se embellece el texto.
  function formatearAsistente(texto) {
    var t = escaparHtml(texto);
    t = t.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>');
    t = t.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    // Normaliza vinetas "- " / "* " al inicio de linea a un punto medio.
    t = t.replace(/(^|\n)\s*[-*]\s+/g, '$1\u2022 ');
    // Conserva listas numeradas ("1. ") y convierte los saltos de linea.
    t = t.replace(/\n/g, '<br>');
    return t;
  }

  function agregarMensaje(texto, tipo) {
    var div = document.createElement('div');
    div.className = 'asistente-msg asistente-msg--' + (tipo || 'asistente');
    if (tipo === 'asistente') {
      div.innerHTML = formatearAsistente(texto);
    } else {
      div.textContent = texto;
    }
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

    var cuerpo = JSON.stringify({
      mensaje: texto,
      historial: historial.slice(-MAX_HISTORIAL)
    });

    cerrarTurno = function (mensajeAsistente, accion) {
      historial.push({ autor: 'usuario', contenido: texto });
      if (mensajeAsistente) {
        historial.push({ autor: 'asistente', contenido: mensajeAsistente });
      }
      historial = historial.slice(-MAX_HISTORIAL);
      if (feedback && mensajeAsistente) feedback.hidden = false;
      if (accion) mostrarAccion(accion);
      bloquear(false);
      entrada.focus();
    };

    // Camino clasico (JSON). Se usa si el navegador no soporta leer el cuerpo en
    // streaming o si el endpoint de flujo no esta disponible.
    var porJson = function () {
      peticionAsistente(CHAT_URL, { method: 'POST', headers: cabeceras(), body: cuerpo })
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
            cerrarTurno(datos.respuesta, datos.accion);
          } else {
            agregarMensaje(
              datos.mensaje || 'No pude responder ahora mismo. Intenta de nuevo.',
              'error'
            );
            cerrarTurno('');
          }
        })
        .catch(function () {
          quitarEscribiendo();
          agregarMensaje('Error de conexion con el asistente. Revisa tu internet.', 'error');
          cerrarTurno('');
        });
    };

    if (typeof window.ReadableStream === 'undefined' || !window.fetch) {
      porJson();
      return;
    }

    // Camino en streaming (Server-Sent Events). Nunca reenvia la peticion: si algo
    // falla tras empezar, se muestra el aviso en vez de repetir (evita duplicar).
    peticionAsistente(FLUJO_URL, { method: 'POST', headers: cabeceras(), body: cuerpo })
      .then(function (resp) {
        var tipo = resp.headers.get('Content-Type') || '';
        if (!resp.ok || tipo.indexOf('text/event-stream') === -1 || !resp.body) {
          // Error del servidor (JSON) o sin soporte: mostrar y cerrar el turno.
          return resp
            .json()
            .catch(function () { return {}; })
            .then(function (datos) {
              quitarEscribiendo();
              agregarMensaje(
                (datos && datos.mensaje) ||
                  'No pude responder ahora mismo. Intenta de nuevo.',
                'error'
              );
              cerrarTurno('');
            });
        }
        return leerFlujo(resp, cerrarTurno);
      })
      .catch(function () {
        quitarEscribiendo();
        agregarMensaje('Error de conexion con el asistente. Revisa tu internet.', 'error');
        cerrarTurno('');
      });
  }

  function leerFlujo(resp, cerrarTurno) {
    var lector = resp.body.getReader();
    var decodificador = new TextDecoder('utf-8');
    var pendiente = '';
    var acumulado = '';
    var burbuja = null;
    var accion = null;

    function aplicar(evento, data) {
      var obj = {};
      try { obj = JSON.parse(data); } catch (e) { obj = {}; }
      if (evento === 'accion') {
        accion = obj;
      } else if (evento === 'delta') {
        acumulado += obj.texto || '';
        if (!burbuja) {
          quitarEscribiendo();
          burbuja = agregarMensaje('', 'asistente');
        }
        burbuja.innerHTML = formatearAsistente(acumulado);
        lista.scrollTop = lista.scrollHeight;
      }
    }

    function consumir() {
      var bloques = pendiente.split('\n\n');
      pendiente = bloques.pop();
      bloques.forEach(function (bloque) {
        var evento = 'message';
        var data = '';
        bloque.split('\n').forEach(function (linea) {
          if (linea.indexOf('event:') === 0) evento = linea.slice(6).trim();
          else if (linea.indexOf('data:') === 0) data += linea.slice(5).replace(/^ /, '');
        });
        aplicar(evento, data);
      });
    }

    function paso() {
      return lector.read().then(function (lectura) {
        if (lectura.done) {
          consumir();
          quitarEscribiendo();
          if (!burbuja) {
            agregarMensaje('No pude responder ahora mismo. Intenta de nuevo.', 'error');
          }
          cerrarTurno(acumulado, accion);
          return;
        }
        pendiente += decodificador.decode(lectura.value, { stream: true });
        consumir();
        return paso();
      });
    }

    return paso();
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

  // Abridor publico: otros paneles (p. ej. el de administracion) tienen un boton
  // "Asistente IA" que abre el chat sin que el usuario busque la burbuja.
  window.localisAbrirAsistente = function () {
    if (panel.hidden) abrir();
    else entrada.focus();
  };

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