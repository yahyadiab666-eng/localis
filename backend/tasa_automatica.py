"""Actualización automática diaria de la tasa de cambio (web OFICIAL del BCV).

La app calcula los precios en bolívares con ``configuracion_sistema.tasa_dolar``.
Si nadie la edita a mano queda desactualizada. Este módulo la **actualiza sola,
una vez al día**, tomando el valor **oficial** de ``https://www.bcv.org.ve/``
(scraping propio en :mod:`backend.tasa_bcv`; nunca APIs de terceros tipo
dolarapi.com) y **redondeado a 2 decimales**.

Cómo se garantiza el "cada día":

* Un hilo de fondo revisa periódicamente si la tasa guardada es de hoy.
* Además, la primera lectura de la tasa del día dispara la sincronización **en
  segundo plano** (no bloquea la petición).

Principios:

* **Nunca rompe nada**: si el BCV no responde, se conserva el valor anterior.
* **Sin martilleo**: tras un intento hay un periodo de enfriamiento antes de
  reintentar el mismo día.
* **Apagable** con ``LOCALIS_TASA_AUTO_BCV=0`` (vuelve al modo manual del admin).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date

from backend.runtime_cache import invalidate
from backend.tasa_bcv import obtener_tasa_bcv

_LOG = logging.getLogger('localis.tasa_automatica')

CLAVE_TASA = 'tasa_dolar'
CLAVE_FECHA = 'tasa_dolar_actualizada_en'
CLAVE_FUENTE = 'tasa_dolar_fuente'

# Estado del proceso (un worker por convención: WEB_CONCURRENCY=1).
_estado = {
    'dia': None,             # última fecha sincronizada con éxito
    'ok': False,             # ¿la última sincronización funcionó?
    'en_curso': False,       # sincronización en marcha
    'ultimo_intento': None,  # monotonic() del último intento
    'ultimo': None,          # texto guardado (2 decimales)
}
_lock = threading.Lock()


def _entero_env(nombre, defecto):
    try:
        return int(float(os.getenv(nombre, str(defecto)) or defecto))
    except (TypeError, ValueError):
        return defecto


# Reintento dentro del mismo día (evita martillear si el BCV está caído).
_COOLDOWN_SEG = max(60, _entero_env('LOCALIS_TASA_AUTO_REINTENTO_SEG', 1800))
# Cada cuánto revisa el hilo de fondo si cambió el día.
_INTERVALO_BUCLE_SEG = max(60, _entero_env('LOCALIS_TASA_AUTO_INTERVALO_SEG', 900))


def tasa_automatica_habilitada():
    """True salvo que ``LOCALIS_TASA_AUTO_BCV`` la desactive (modo manual)."""
    valor = str(os.getenv('LOCALIS_TASA_AUTO_BCV', '1')).strip().lower()
    return valor in ('1', 'true', 'yes', 'on')


def _texto_tasa(tasa):
    """Formatea la tasa con **2 decimales** (formato estable para la BD)."""
    return f'{round(float(tasa), 2):.2f}'


def _valor_fila(fila):
    if fila is None:
        return None
    if isinstance(fila, dict):
        return fila.get('valor')
    return fila[0]


def _leer_config(clave):
    from backend.db import get_db_connection

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT valor FROM configuracion_sistema WHERE clave = ?', (clave,)
            )
            return _valor_fila(cursor.fetchone())
    except Exception as error:
        _LOG.debug('no se pudo leer %s: %s', clave, error)
        return None


def _tasa_actual_db():
    valor = _leer_config(CLAVE_TASA)
    try:
        return float(valor) if valor else None
    except (TypeError, ValueError):
        return None


def _guardar_tasa(tasa, fuente=None):
    """Persiste la tasa (2 decimales) y sus metadatos. Lanza si falla la BD."""
    from backend.db import get_db_connection

    texto = _texto_tasa(tasa)
    filas = (
        (CLAVE_TASA, texto),
        (CLAVE_FECHA, date.today().isoformat()),
        (CLAVE_FUENTE, str(fuente or 'bcv')),
    )
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        for clave, valor in filas:
            cursor.execute(
                'INSERT INTO configuracion_sistema (clave, valor) VALUES (?, ?) '
                'ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor',
                (clave, valor),
            )
        conexion.commit()
    # Los lectores usan caché con TTL; se invalida para que vean el valor nuevo ya.
    invalidate('config:')
    return texto


def sincronizar_tasa_diaria(forzar=False):
    """Trae la tasa **oficial del BCV**, la redondea a 2 decimales y la guarda.

    Es idempotente y **nunca lanza**. Devuelve un dict con ``ok``, ``motivo``,
    ``tasa`` y ``fuente``. Con ``forzar=True`` ignora la comprobación del día
    (la usa el botón manual del admin y el hilo de fondo).
    """
    if not tasa_automatica_habilitada():
        return {
            'ok': False,
            'motivo': 'deshabilitada',
            'tasa': _tasa_actual_db(),
            'fuente': None,
        }

    hoy = date.today()
    with _lock:
        if not forzar and _estado['ok'] and _estado['dia'] == hoy:
            return {
                'ok': True,
                'motivo': 'ya_al_dia',
                'tasa': _tasa_actual_db(),
                'fuente': _leer_config(CLAVE_FUENTE),
            }
        if _estado['en_curso']:
            return {'ok': False, 'motivo': 'en_curso', 'tasa': _tasa_actual_db()}
        _estado['en_curso'] = True

    try:
        resultado = obtener_tasa_bcv(forzar_actualizacion=True) or {}
        tasa = resultado.get('tasa')
        if not resultado.get('ok') or not tasa:
            _LOG.warning(
                'tasa del BCV no disponible (%s); se conserva la anterior',
                resultado.get('error'),
            )
            return {'ok': False, 'motivo': 'bcv_no_disponible', 'tasa': _tasa_actual_db()}

        fuente = resultado.get('fuente') or 'bcv'
        texto = _guardar_tasa(tasa, fuente=fuente)
        with _lock:
            _estado.update({'dia': hoy, 'ok': True, 'ultimo': texto})
        _LOG.info('tasa guardada: %s Bs (fuente=%s)', texto, fuente)
        return {'ok': True, 'motivo': 'actualizada', 'tasa': float(texto), 'fuente': fuente}
    except Exception as error:
        _LOG.error('no se pudo guardar la tasa: %s: %s', type(error).__name__, error)
        return {'ok': False, 'motivo': f'{type(error).__name__}: {error}', 'tasa': _tasa_actual_db()}
    finally:
        with _lock:
            _estado['en_curso'] = False
            _estado['ultimo_intento'] = time.monotonic()


def asegurar_tasa_del_dia():
    """Lanza la sincronización en **segundo plano** si aún no se hizo hoy.

    Pensada para el camino de petición: no bloquea ni hace red. Es barata
    (comprobación de fecha en memoria) y respeta el enfriamiento entre intentos.
    """
    if not tasa_automatica_habilitada():
        return
    hoy = date.today()
    with _lock:
        if _estado['ok'] and _estado['dia'] == hoy:
            return
        if _estado['en_curso']:
            return
        ultimo = _estado['ultimo_intento']
        if ultimo and (time.monotonic() - ultimo) < _COOLDOWN_SEG:
            return
    hilo = threading.Thread(
        target=lambda: sincronizar_tasa_diaria(forzar=True),
        name='localis-tasa-auto',
        daemon=True,
    )
    hilo.start()


def _bucle_diario():
    """Hilo de fondo: revisa el cambio de día y sincroniza sin depender de nadie."""
    while True:
        try:
            asegurar_tasa_del_dia()
        except Exception as error:  # pragma: no cover - defensivo
            _LOG.error('bucle de tasa: %s: %s', type(error).__name__, error)
        time.sleep(_INTERVALO_BUCLE_SEG)


def programar_hilo_diario():
    """Arranca el hilo de fondo una sola vez por proceso (idempotente)."""
    if not tasa_automatica_habilitada():
        return False
    if str(os.getenv('LOCALIS_TASA_AUTO_HILO', '1')).strip().lower() in ('0', 'false', 'no', 'off'):
        return False
    # En debug/reloader se evita duplicar hilos por recarga.
    if os.getenv('FLASK_DEBUG'):
        return False
    if getattr(programar_hilo_diario, '_lanzado', False):
        return True
    programar_hilo_diario._lanzado = True
    threading.Thread(target=_bucle_diario, name='localis-tasa-diario', daemon=True).start()
    return True


def estado_tasa_automatica():
    """Diagnóstico del proceso (no hace red)."""
    with _lock:
        return {
            'habilitada': tasa_automatica_habilitada(),
            'dia_sincronizado': _estado['dia'].isoformat() if _estado['dia'] else None,
            'ok': _estado['ok'],
            'en_curso': _estado['en_curso'],
            'ultimo': _estado['ultimo'],
            'tasa_bd': _tasa_actual_db(),
            'actualizada_en': _leer_config(CLAVE_FECHA),
            'fuente': _leer_config(CLAVE_FUENTE),
        }
