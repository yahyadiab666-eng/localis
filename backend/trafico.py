"""Métricas de tráfico persistidas por día (contadores agregados).

A diferencia de ``backend.metricas`` (Prometheus, en memoria y se pierde al
reiniciar), aquí los conteos **sobreviven al reinicio**: se acumulan en memoria
durante el día y se vuelcan a la tabla ``metricas_diarias`` con un ``UPSERT``.
Con 1 worker (``WEB_CONCURRENCY=1``) el acumulador en memoria es consistente.

El volcado lo dispara **el propio request** (``after_request`` →
``volcar_oportunista``) como máximo cada ``LOCALIS_TRAFICO_FLUSH_SEC`` segundos.
No se usa un hilo de fondo a propósito: en producción la creación de hilos puede
fallar (límite del contenedor) y el volcado quedaría muerto sin avisar.

Qué se cuenta (cada clave es una fila por fecha):

  - ``peticiones``       → todas las peticiones HTTP.
  - ``peticiones_app``   → tráfico de la app (excluye estáticos, salud, métricas).
  - ``peticiones_bots``  → de las anteriores, las de bots/crawlers (heurística UA).
  - ``visitas_unicas``   → huellas (IP+UA) únicas del día, sin bots. Aproximado:
                            el conjunto vive en memoria y se reinicia con el
                            worker y al cambiar de día.
  - ``pwa_instalaciones`` → evento ``appinstalled`` (beacon del cliente).
  - ``pwa_aperturas``    → peticiones con ``source=pwa`` (aperturas de la PWA).

Reglas de robustez (igual que el resto del proyecto):
  - Nunca lanza: la analítica jamás debe tumbar una petición.
  - El volcado a la BD usa una conexión propia y acotada (``statement_timeout``),
    de modo que un pooler que no responde no lo bloquea para siempre; si falla,
    los deltas se reintegran y no se pierden.
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
_ultimo_intento_volcado = 0.0
_volcado_intentos = 0
_ultimo_error_volcado = ''
_probe_hilos = None


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
                if _es_bot(user_agent):
                    _sumar(fecha, 'peticiones_bots', 1)
                else:
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
    global _ultimo_volcado, _volcado_intentos
    if not habilitado():
        return 0
    _volcado_intentos += 1
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
    """Escribe los deltas con UPSERT en una conexión PROPIA y acotada.

    Abre una conexión dedicada (NO la del pool compartido) con
    ``statement_timeout`` y ``lock_timeout``: si el pooler de Supabase acepta la
    conexión y luego no responde, la consulta se aborta sola en 10 s en vez de
    dejar el volcado bloqueado para siempre (causa observada en producción).

    Se usan marcadores ``%s`` (PostgreSQL nativo) porque aquí NO pasa por el
    adaptador ``?`` -> ``%s``. Devuelve True si la transacción se guardó.
    """
    global _ultimo_error_volcado
    try:
        import psycopg2

        from database import DATABASE_URL

        if not DATABASE_URL:
            _ultimo_error_volcado = 'DATABASE_URL no configurada'
            return False

        conexion = psycopg2.connect(
            DATABASE_URL,
            connect_timeout=10,
            options='-c statement_timeout=10000 -c lock_timeout=5000',
        )
        try:
            with conexion:
                cursor = conexion.cursor()
                for (fecha, clave), valor in filas:
                    if valor <= 0:
                        continue
                    cursor.execute(
                        """
                        INSERT INTO metricas_diarias (fecha, clave, valor)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (fecha, clave) DO UPDATE
                            SET valor = metricas_diarias.valor + EXCLUDED.valor,
                                actualizado = CURRENT_TIMESTAMP
                        """,
                        (fecha, clave, int(valor)),
                    )
        finally:
            try:
                conexion.close()
            except Exception:
                pass
        return True
    except Exception as error:
        _ultimo_error_volcado = f'{type(error).__name__}: {error}'
        logger.error(
            f'{_LOG} no se pudo volcar ({type(error).__name__}: {error})'
        )
        return False


def volcar_oportunista():
    """Volcado disparado desde el request (SIN depender de hilos).

    En producción la creación de un hilo de fondo puede fallar (límite de hilos
    del contenedor): por eso el volcado NO debe depender de un hilo. Se invoca
    desde ``after_request`` y ejecuta ``volcar()`` a lo sumo una vez cada
    ``_INTERVALO`` segundos. Nunca lanza.
    """
    global _ultimo_intento_volcado
    try:
        if not habilitado():
            return
        ahora = time.time()
        with _lock:
            if not _pendientes:
                return
            if ahora - _ultimo_intento_volcado < _INTERVALO:
                return
            _ultimo_intento_volcado = ahora
        volcar()
    except Exception:
        pass


def _probar_creacion_hilos():
    """Comprueba UNA vez si el proceso puede crear hilos. Devuelve True/str."""
    global _probe_hilos
    if _probe_hilos is not None:
        return _probe_hilos
    try:
        hilo = threading.Thread(target=lambda: None, name='localis-probe')
        hilo.start()
        hilo.join(2)
        _probe_hilos = True
    except Exception as error:
        _probe_hilos = f'{type(error).__name__}: {error}'
    return _probe_hilos


def estado():
    """Diagnóstico del acumulador en memoria (para /health). Nunca lanza."""
    puede_hilos = _probar_creacion_hilos()
    try:
        with _lock:
            return {
                'habilitado': habilitado(),
                'hilos_activos': threading.active_count(),
                'hilos_nombres': [h.name for h in threading.enumerate()],
                'puede_crear_hilos': puede_hilos,
                'dia': _dia_actual.isoformat() if _dia_actual else None,
                'pendientes_claves': len(_pendientes),
                'unicos_dia': len(_unicos),
                'volcado_intentos': _volcado_intentos,
                'ultimo_volcado': _ultimo_volcado,
                'ultimo_intento_volcado': _ultimo_intento_volcado,
                'ultimo_error_volcado': _ultimo_error_volcado,
                'intervalo_seg': _INTERVALO,
            }
    except Exception as error:
        return {'error': f'{type(error).__name__}: {error}'}


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
