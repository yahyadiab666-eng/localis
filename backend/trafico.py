"""Métricas de tráfico persistidas por día (contadores agregados).

A diferencia de ``backend.metricas`` (Prometheus, en memoria y se pierde al
reiniciar), aquí los conteos **sobreviven al reinicio**: se acumulan en memoria
durante el día y se vuelcan a la tabla ``metricas_diarias`` con un ``UPSERT``
cada pocos segundos. Con 1 worker (``WEB_CONCURRENCY=1``) el acumulador en
memoria es consistente.

Qué se cuenta (cada clave es una fila por fecha):

  - ``peticiones``       → todas las peticiones HTTP.
  - ``peticiones_app``   → tráfico "real" (excluye estáticos, salud, métricas).
  - ``visitas_unicas``   → huellas (IP+UA) únicas del día, sin bots. Aproximado:
                            el conjunto vive en memoria y se reinicia con el
                            worker y al cambiar de día.
  - ``pwa_instalaciones`` → evento ``appinstalled`` (beacon del cliente).
  - ``pwa_aperturas``    → peticiones con ``source=pwa`` (aperturas de la PWA).

Reglas de robustez (igual que el resto del proyecto):
  - Nunca lanza: la analítica jamás debe tumbar una petición.
  - El volcado a la BD ocurre fuera del lock, para no añadir latencia al
    ``after_request``; si falla, los deltas se reintegran y no se pierden.
  - Se apaga con ``LOCALIS_TRAFICO=0``.

Los días se calculan en UTC (el servidor corre en UTC). El reporte lo indica.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger('localis.trafico')

_LOG = '[Localis Trafico]'

# Prefijos de ruta que NO cuentan como tráfico "de app" (ruido de infraestructura).
_RAIZ_EXCLUIDA = (
    '/static/',
    '/health',
    '/metrics',
    '/sw.js',
    '/favicon',
    '/robots.txt',
)

# Fragmentos de User-Agent que delatan bots/monitores (no cuentan como únicos).
_BOT_TOKENS = (
    'bot',
    'crawl',
    'spider',
    'slurp',
    'facebookexternalhit',
    'whatsapp',
    'telegrambot',
    'preview',
    'monitor',
    'uptimerobot',
    'pingdom',
    'curl/',
    'wget',
    'python-requests',
    'python-urllib',
    'go-http-client',
)

_MAX_UNICOS = int(os.environ.get('LOCALIS_TRAFICO_MAX_UNICOS', '50000') or 50000)
_INTERVALO = int(os.environ.get('LOCALIS_TRAFICO_FLUSH_SEC', '45') or 45)

_lock = threading.Lock()
_pendientes = {}  # (fecha_iso, clave) -> delta pendiente de volcar
_unicos = set()   # huellas únicas del día en curso
_dia_actual = None
_ultimo_volcado = 0.0
_iniciado = False
_inicio_lock = threading.Lock()


def habilitado():
    """True si ``LOCALIS_TRAFICO`` no está desactivado (activado por defecto)."""
    valor = (os.environ.get('LOCALIS_TRAFICO') or '1').strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _hoy():
    return datetime.now(timezone.utc).date()


def _huella(ip, user_agent):
    base = str(ip or '') + '|' + str(user_agent or '')[:60]
    return hashlib.sha1(base.encode('utf-8', 'ignore')).hexdigest()


def _es_bot(user_agent):
    ua = (user_agent or '').lower()
    return any(token in ua for token in _BOT_TOKENS)


def _sumar(fecha, clave, delta):
    """Suma un delta pendiente (debe llamarse con ``_lock`` tomado)."""
    llave = (fecha, clave)
    _pendientes[llave] = _pendientes.get(llave, 0) + int(delta)


def _sincronizar_dia():
    """Reinicia los únicos al cambiar de día (llamada con lock tomado)."""
    global _dia_actual
    hoy = _hoy()
    if _dia_actual != hoy:
        _dia_actual = hoy
        _unicos.clear()


def registrar_peticion(ruta, status=None, user_agent='', remote_addr='', query=''):
    """Registra una petición HTTP. Nunca lanza (no bloquea la respuesta)."""
    if not habilitado():
        return
    try:
        ruta = ruta or '/'
        with _lock:
            _sincronizar_dia()
            fecha = _dia_actual.isoformat()
            _sumar(fecha, 'peticiones', 1)
            if not ruta.startswith(_RAIZ_EXCLUIDA):
                _sumar(fecha, 'peticiones_app', 1)
                if not _es_bot(user_agent):
                    huella = _huella(remote_addr, user_agent)
                    if huella not in _unicos and len(_unicos) < _MAX_UNICOS:
                        _unicos.add(huella)
                        _sumar(fecha, 'visitas_unicas', 1)
                if 'source=pwa' in (query or ''):
                    _sumar(fecha, 'pwa_aperturas', 1)
    except Exception:
        # La analítica nunca rompe una petición.
        pass


def registrar_evento(clave, delta=1):
    """Registra un evento puntual (p. ej. ``pwa_instalaciones``). Nunca lanza."""
    if not habilitado() or not clave:
        return
    try:
        with _lock:
            _sincronizar_dia()
            _sumar(_dia_actual.isoformat(), str(clave), delta)
    except Exception:
        pass


def volcar():
    """Persiste los deltas pendientes en la BD. Nunca lanza.

    Devuelve el número de claves volcadas. Si la BD falla, los deltas se
    reintegran para reintentarlos en el siguiente ciclo.
    """
    if not habilitado():
        return 0
    global _ultimo_volcado
    with _lock:
        if not _pendientes:
            return 0
        filas = list(_pendientes.items())
        _pendientes.clear()
    if _escribir(filas):
        _ultimo_volcado = time.time()
        return len(filas)
    # Falló la escritura: devolver los deltas para no perder el conteo.
    with _lock:
        for (fecha, clave), valor in filas:
            _pendientes[(fecha, clave)] = _pendientes.get((fecha, clave), 0) + valor
    return 0


def _escribir(filas):
    """Escribe los deltas con UPSERT. Devuelve True si la transacción se guardó."""
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            for (fecha, clave), valor in filas:
                if valor <= 0:
                    continue
                cursor.execute(
                    """
                    INSERT INTO metricas_diarias (fecha, clave, valor)
                    VALUES (?, ?, ?)
                    ON CONFLICT (fecha, clave) DO UPDATE
                        SET valor = metricas_diarias.valor + EXCLUDED.valor,
                            actualizado = CURRENT_TIMESTAMP
                    """,
                    (fecha, clave, int(valor)),
                )
            conexion.commit()
        return True
    except Exception as error:
        logger.error(
            f'{_LOG} no se pudo volcar ({type(error).__name__}: {error})'
        )
        return False


def _bucle():
    while True:
        time.sleep(max(10, _INTERVALO))
        try:
            volcar()
        except Exception as error:
            logger.error(f'{_LOG} ciclo de volcado falló: {type(error).__name__}: {error}')


def iniciar_flush_periodico():
    """Arranca el volcado periódico (hilo daemon). Idempotente. Nunca lanza."""
    global _iniciado
    try:
        if not habilitado():
            logger.info(f'{_LOG} desactivado (LOCALIS_TRAFICO=0)')
            return None
        with _inicio_lock:
            if _iniciado:
                return None
            _iniciado = True
            hilo = threading.Thread(target=_bucle, name='localis-trafico', daemon=True)
            hilo.start()
            logger.info(f'{_LOG} volcado periódico activo (cada {_INTERVALO}s)')
            return hilo
    except Exception as error:
        logger.error(f'{_LOG} no se pudo iniciar el volcado: {error}')
        return None


# ==========================================================
# Lectura para el reporte
# ==========================================================

def _campo(fila, indice, nombre):
    """Lee una columna de una fila dict o tupla (según el cursor de la BD)."""
    if isinstance(fila, dict):
        return fila.get(nombre)
    return fila[indice]


def resumen_trafico(dias=1):
    """Resumen agregado del tráfico. Nunca lanza.

    Devuelve ``disponible`` (False si no hay tabla), ``por_dia`` (lista con la
    fecha y sus claves), ``totales_periodo`` y ``totales_historicos`` (todas las
    fechas). Las fechas se calculan en UTC.
    """
    vacio = {
        'disponible': False,
        'dias': int(dias),
        'por_dia': [],
        'totales_periodo': {},
        'totales_historicos': {},
    }
    try:
        from datetime import timedelta

        desde = (_hoy() - timedelta(days=max(1, int(dias or 1)) - 1)).isoformat()
    except Exception:
        return vacio

    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT fecha, clave, valor FROM metricas_diarias '
                'WHERE fecha >= ? ORDER BY fecha ASC, clave ASC',
                (desde,),
            )
            filas = list(cursor.fetchall())

            cursor.execute(
                'SELECT clave, SUM(valor) AS total FROM metricas_diarias GROUP BY clave'
            )
            historicos = {}
            for fila in cursor.fetchall():
                historicos[_campo(fila, 0, 'clave')] = int(
                    _campo(fila, 1, 'total') or 0
                )
    except Exception as error:
        logger.error(f'{_LOG} resumen no disponible: {type(error).__name__}: {error}')
        return vacio

    por_dia = []
    indice = {}
    totales = {}
    for fila in filas:
        fecha = _campo(fila, 0, 'fecha')
        clave = _campo(fila, 1, 'clave')
        valor = int(_campo(fila, 2, 'valor') or 0)
        if not isinstance(fecha, str):
            fecha = fecha.isoformat() if hasattr(fecha, 'isoformat') else str(fecha)
        if fecha not in indice:
            indice[fecha] = {'fecha': fecha, 'claves': {}}
            por_dia.append(indice[fecha])
        indice[fecha]['claves'][clave] = valor
        totales[clave] = totales.get(clave, 0) + valor

    return {
        'disponible': True,
        'dias': int(dias),
        'por_dia': por_dia,
        'totales_periodo': totales,
        'totales_historicos': historicos,
    }
