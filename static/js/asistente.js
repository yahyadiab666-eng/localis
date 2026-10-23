/* Asistente de IA de Localis: widget flotante, solo informativo.
 *
 * Vanilla JS, sin dependencias. Habla con /api/asistente/chat y arma el enlace
 * de WhatsApp para el soporte humano. Todo es defensivo: si falta un elemento
 * o la red falla, se muestra un aviso en vez de romper la pagina.
 *
 * El contexto de la conversacion vive AQUI (en el navegador) y se envia con
 * cada pregunta; el servidor no guarda nada ni toca la base de datos.
 */
(function () {
  'use strict';

  var raiz = document.getElementById('asistente-widget');
  if (!raiz) return;

  var burbuja = document.getElementById('asistente-burbuja');
  var panel = document.getElementById('asistente-panel');
  var cerrar = document.getElementById('asistente-cerrar');
  var lista = document.getElementById('asistente-mensajes');
  var cajaSugerencias = document.getElementById('asistente-sugerencias');
  var form = document.getElementById('asistente-form');
  var entrada = document.getElementById('asistente-entrada');
  var enviar = document.getElementById('asistente-enviar');
  var soporte = document.getElementById('asistente-soporte');

  if (!burbuja || !panel || !lista || !form || !entrada) return;

  var CHAT_URL = raiz.getAttribute('data-chat-url') || '/api/asistente/chat';
  var CSRF = raiz.getAttribute('data-csrf') || '';
  var ROL = raiz.getAttribute('data-rol') || 'invitado';
  var WHATSAPP = raiz.getAttribute('data-whatsapp') || '';
  var MAX_HISTORIAL = 8;

  var SUGERENCIAS_INICIALES = {
    cliente: ['¿Como busco un producto?', '¿Como contacto a un comercio?'],
    invitado: ['¿Que es Localis?', '¿Como registro mi comercio?'],
    comerciante: ['¿Como importo mi inventario?', '¿Por que mi producto no tiene foto?'],
    admin: ['¿Como funciona la bandeja tecnica?', '¿Que estados de pago existen?']
  };

  var SALUDO = {
    cliente: 'Hola, soy el asistente de Localis. Te doy informacion y guia. ¿En que te ayudo?',
    invitado: 'Hola, soy el asistente de Localis. Puedo darte informacion para buscar productos o usar la plataforma.',
    comerciante: 'Hola. Te doy informacion sobre tu tienda: inventario, imagenes, planes y mas.',
    admin: 'Hola. Puedo darte informacion sobre la operacion y la bandeja tecnica.'
  };

  var historial = [];
  var esperando = false;

  function iconos() {
    if (window.lucide && typeof window.lucide.createIcons === 'function') {
      window.lucide.createIcons();
    }
  }

  function abrir() {
    panel.hidden = false;
    burbuja.setAttribute('aria-expanded', 'true');
    if (!lista.dataset.iniciado) {
      lista.dataset.iniciado = '1';
      agregarMensaje(SALUDO[ROL] || SALUDO.invitado, 'asistente');
      pintarSugerencias(SUGERENCIAS_INICIALES[ROL] || SUGERENCIAS_INICIALES.invitado);
    }
    entrada.focus();
  }

  function cerrarPanel() {
    panel.hidden = true;
    burbuja.setAttribute('aria-expanded', 'false');
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

  function pintarSugerencias(sugerencias) {
    cajaSugerencias.innerHTML = '';
    if (!sugerencias || !sugerencias.length) return;
    sugerencias.slice(0, 4).forEach(function (texto) {
      var boton = document.createElement('button');
      boton.type = 'button';
      boton.className = 'asistente-sugerencia';
      boton.textContent = texto;
      boton.addEventListener('click', function () {
        entrada.value = texto;
        enviarMensaje();
      });
      cajaSugerencias.appendChild(boton);
    });
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
    cajaSugerencias.innerHTML = '';
    bloquear(true);
    mostrarEscribiendo();

    fetch(CHAT_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'X-CSRFToken': CSRF
      },
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
          pintarSugerencias(datos.sugerencias);
          historial.push({ autor: 'usuario', contenido: texto });
          historial.push({ autor: 'asistente', contenido: datos.respuesta });
          historial = historial.slice(-MAX_HISTORIAL);
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

  function abrirSoporte() {
    if (!WHATSAPP) {
      agregarMensaje(
        'Escribenos por WhatsApp para casos que requieran revision (pagos, edicion de tu comercio, errores concretos). La atencion puede tardar un poco.',
        'asistente'
      );
      return;
    }
    var texto = 'Hola, necesito ayuda con Localis.';
    var separador = WHATSAPP.indexOf('?') === -1 ? '?' : '&';
    window.open(WHATSAPP + separador + 'text=' + encodeURIComponent(texto), '_blank', 'noopener');
    agregarMensaje(
      'Te abrimos WhatsApp con el soporte. La atencion puede tardar un poco; gracias por la paciencia.',
      'asistente'
    );
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
