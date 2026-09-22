"""Analítica de interacción por comercio (contador de clics e interés real).

Cada vez que un visitante entra a la tienda o interactúa con un producto o con
los botones de contacto (WhatsApp, Maps, copiar), se registra un evento ligado
al ``comercio_id``. El comerciante ve después un resumen agregado.

Reglas de robustez:
  - Nunca lanza hacia la ruta HTTP (la analítica jamás debe romper una página).
  - Solo acepta tipos de evento de una lista blanca.
  - Deduplica ráfagas (mismo comercio/tipo/producto/IP en < 2 s) para no inflar
    el conteo si el navegador dispara el evento dos veces.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

_LOG = '[Localis Analitica]'

# Tipos de evento permitidos (clave interna -> etiqueta para el panel).
# Métrica principal: "Visitas a la tienda". No hay contador duplicado de
# "Ir a la tienda": entrar a la tienda ya cuenta como visita.
TIPOS_INTERACCION = {
    'visita_tienda': 'Visitas a la tienda',
    'clic_producto': 'Clics en productos',
    'clic_whatsapp': 'Clics en WhatsApp',
    'clic_maps': 'Clics en Google Maps',
    'clic_copiar': 'Copias del número',
}

# Eventos heredados que se consolidan en la métrica principal (no se pierden
# filas históricas ni se muestran contadores duplicados).
TIPOS_LEGADO = {
    'clic_tienda': 'visita_tienda',
}

_DEDUP_SEGUNDOS = 2.0
_dedup = {}
_dedup_lock = threading.Lock()


def tipo_valido(tipo):
    return str(tipo or '').strip().lower() in TIPOS_INTERACCION


def normalizar_tipo(tipo):
    """Tipo canónico del evento, consolidando los heredados, o ``None``."""
    clave = str(tipo or '').strip().lower()
    clave = TIPOS_LEGADO.get(clave, clave)
    return clave if clave in TIPOS_INTERACCION else None


def _dedup_permitido(clave):
    ahora = time.monotonic()
    with _dedup_lock:
        ultimo = _dedup.get(clave)
        _dedup[clave] = ahora
        # Poda perezosa para no crecer sin límite.
        if len(_dedup) > 5000:
            limite = ahora - 60
            for k in [k for k, t in _dedup.items() if t < limite]:
                _dedup.pop(k, None)
    return ultimo is None or (ahora - ultimo) >= _DEDUP_SEGUNDOS


def producto_pertenece_a_comercio(producto_id, comercio_id):
    """True si el producto existe y pertenece al comercio (evita cruces)."""
    if not producto_id:
        return False
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT 1 FROM productos WHERE id = ? AND comercio_id = ? LIMIT 1',
                (int(producto_id), int(comercio_id)),
            )
            return cursor.fetchone() is not None
    except Exception:
        return False


def registrar_interaccion(comercio_id, tipo, producto_id=None, origen=None, huella=None):
    """Registra un evento de interés. Retorna True si se guardó. Nunca lanza."""
    try:
        comercio_id = int(comercio_id or 0)
    except (TypeError, ValueError):
        return False
    if comercio_id <= 0:
        return False

    tipo = normalizar_tipo(tipo)
    if not tipo:
        return False

    if producto_id is not None:
        try:
            producto_id = int(producto_id)
        except (TypeError, ValueError):
            producto_id = None
        if producto_id is not None and not producto_pertenece_a_comercio(
            producto_id, comercio_id
        ):
            producto_id = None

    if not _dedup_permitido((huella or 'anon', comercio_id, tipo, producto_id)):
        return False

    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO interacciones_comercio
                    (comercio_id, producto_id, tipo, origen)
                VALUES (?, ?, ?, ?)
                """,
                (
                    comercio_id,
                    producto_id,
                    tipo,
                    (str(origen)[:120] if origen else None),
                ),
            )
            conexion.commit()
        return True
    except Exception as error:
        print(f'{_LOG} no se pudo registrar {tipo} comercio={comercio_id}: '
              f'{type(error).__name__}: {error}')
        return False


def _filas(cursor):
    resultado = []
    for fila in cursor.fetchall():
        resultado.append(list(fila.values()) if isinstance(fila, dict) else list(fila))
    return resultado


def resumen_interacciones(comercio_id, dias=30):
    """Métricas agregadas del comercio para el panel. Nunca lanza.

    Devuelve ``total``, ``por_tipo`` (con etiquetas), ``productos_top`` y
    ``dias``. Si la tabla aún no existe, devuelve ceros.
    """
    try:
        comercio_id = int(comercio_id or 0)
    except (TypeError, ValueError):
        comercio_id = 0

    vacio = {
        'total': 0,
        'por_tipo': {},
        'productos_top': [],
        'dias': int(dias),
        'disponible': False,
    }
    if comercio_id <= 0:
        return vacio

    corte = datetime.utcnow() - timedelta(days=max(1, int(dias or 30)))
    try:
        from backend.db import get_db_connection

        # Se consolidan los eventos heredados ('clic_tienda') dentro de la
        # métrica principal ('visita_tienda') y se ignoran tipos desconocidos,
        # de modo que el total siempre coincida con el desglose mostrado.
        tipos_consulta = tuple(TIPOS_INTERACCION) + tuple(TIPOS_LEGADO)
        marcadores = ', '.join('?' for _ in tipos_consulta)
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                f"""
                SELECT CASE WHEN tipo = 'clic_tienda' THEN 'visita_tienda'
                            ELSE tipo END AS tipo,
                       COUNT(*) AS n
                FROM interacciones_comercio
                WHERE comercio_id = ? AND fecha >= ?
                  AND tipo IN ({marcadores})
                GROUP BY 1
                """,
                (comercio_id, corte, *tipos_consulta),
            )
            por_tipo = {}
            total = 0
            for tipo, n in _filas(cursor):
                clave = str(tipo or '')
                por_tipo[clave] = int(n or 0)
                total += int(n or 0)

            cursor.execute(
                """
                SELECT i.producto_id, p.nombre, COUNT(*) AS n
                FROM interacciones_comercio i
                LEFT JOIN productos p ON p.id = i.producto_id
                WHERE i.comercio_id = ? AND i.fecha >= ?
                  AND i.producto_id IS NOT NULL
                GROUP BY i.producto_id, p.nombre
                ORDER BY n DESC
                LIMIT 5
                """,
                (comercio_id, corte),
            )
            productos_top = [
                {'producto_id': fila[0], 'nombre': fila[1] or 'Producto', 'total': int(fila[2] or 0)}
                for fila in _filas(cursor)
            ]
    except Exception as error:
        print(f'{_LOG} resumen no disponible comercio={comercio_id}: '
              f'{type(error).__name__}: {error}')
        return vacio

    detalle = []
    for clave, etiqueta in TIPOS_INTERACCION.items():
        detalle.append({'tipo': clave, 'etiqueta': etiqueta, 'total': por_tipo.get(clave, 0)})

    return {
        'total': total,
        'por_tipo': por_tipo,
        'detalle': detalle,
        'productos_top': productos_top,
        'dias': int(dias),
        'disponible': True,
    }
