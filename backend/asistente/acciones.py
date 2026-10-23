"""Acciones confirmadas del asistente (SOLO comerciante, SOLO lo reversible).

Reglas de seguridad (disenadas para que el agente NO pueda romper nada):

1. **Whitelist cerrada**: solo las 3 acciones de ``ACCIONES``. Cualquier otro
   nombre se rechaza.
2. **El modelo NUNCA decide**: la deteccion es deterministica (patrones en
   espanol) y la resolucion del producto la hace el SERVIDOR consultando la BD
   del PROPIO comercio. Si no hay un unico objetivo claro, NO se propone nada.
3. **Solo operaciones reversibles / no destructivas**:
   - ``producto_desactivar`` / ``producto_activar`` -> cambia ``activo`` (oculta o
     muestra; nunca borra).
   - ``producto_reintentar_imagen`` -> marca la imagen como pendiente para que el
     pipeline de fondo la vuelva a buscar.
4. **Confirmacion en dos pasos**: proponer NO cambia nada; el cambio ocurre solo
   al confirmar, con un ``nonce`` de un solo uso guardado en la sesion del
   servidor (lo gestiona el blueprint, no este modulo).
5. **Revalidacion al ejecutar**: se comprueba otra vez que el producto exista y
   pertenezca al comercio de la sesion, dentro de la MISMA transaccion.
6. **Auditoria**: cada accion aplicada se registra en ``logs_auditoria``.
7. **Interruptor**: ``LOCALIS_ASISTENTE_ACCIONES=0`` desactiva todo.
"""

from __future__ import annotations

import logging
import os
import re
import secrets

_log = logging.getLogger('localis.asistente.acciones')

# Verbo, mensaje de propuesta y confirmacion, por accion (whitelist CERRADA).
ACCIONES = {
    'producto_desactivar': {
        'verbo': 'Ocultar de la tienda el producto',
        'mensaje': (
            'Puedo **ocultar** el producto «{nombre}» de tu tienda (no lo borro; '
            'se puede volver a mostrar cuando quieras). Confirma para aplicarlo.'
        ),
        'ok': 'Listo: oculté «{nombre}» de tu tienda.',
    },
    'producto_activar': {
        'verbo': 'Volver a mostrar el producto',
        'mensaje': (
            'Puedo **volver a mostrar** el producto «{nombre}» en tu tienda. '
            'Confirma para aplicarlo.'
        ),
        'ok': 'Listo: «{nombre}» vuelve a estar visible.',
    },
    'producto_reintentar_imagen': {
        'verbo': 'Reintentar la foto del producto',
        'mensaje': (
            'Puedo **reintentar la búsqueda automática de foto** para «{nombre}». '
            'Se hace en segundo plano. Confirma para aplicarlo.'
        ),
        'ok': 'Listo: reintentaré la foto de «{nombre}» en segundo plano.',
    },
}

# Deteccion deterministica. Exige la palabra "producto/articulo" para que la
# intencion sea explicita (evita falsos positivos). El nombre queda en el ultimo
# grupo de captura.
_DETECTORES = (
    (
        re.compile(
            r'\b(?:desactiva|desactivar|oculta|ocultar|esconde|esconder)\b'
            r'.{0,50}?\b(?:producto|articulo|art[ií]culo)\b\s*[:\-]?\s*(.+)$',
            re.I | re.S,
        ),
        'producto_desactivar',
    ),
    (
        re.compile(
            r'\b(?:activa|activar|muestra|mostrar|publica|publicar|'
            r'vuelve a mostrar|volver a mostrar)\b'
            r'.{0,50}?\b(?:producto|articulo|art[ií]culo)\b\s*[:\-]?\s*(.+)$',
            re.I | re.S,
        ),
        'producto_activar',
    ),
    (
        re.compile(
            r'\b(?:reintenta|reintentar|arregla|arreglar|actualiza|actualizar|'
            r'busca|buscar)\b'
            r'.{0,40}?\b(?:foto|imagen)\b'
            r'.{0,40}?\b(?:producto|articulo|art[ií]culo)\b\s*[:\-]?\s*(.+)$',
            re.I | re.S,
        ),
        'producto_reintentar_imagen',
    ),
)

_RUIDO = re.compile(r'\b(por favor|gracias|ya|ahora|mismo)\b', re.I)


def habilitado():
    valor = str(os.environ.get('LOCALIS_ASISTENTE_ACCIONES', '1')).strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _minuscula(texto):
    return str(texto or '').strip().lower()


def _limpiar_nombre(texto):
    limpio = str(texto or '').strip().strip('.:;,!?"\'«»')
    limpio = _RUIDO.sub('', limpio).strip()
    limpio = re.sub(r'\s{2,}', ' ', limpio)
    return limpio[:80]


def detectar(texto):
    """Devuelve ``(accion, nombre_objetivo)`` o ``None``. Nunca lanza."""
    if not texto:
        return None
    try:
        for patron, accion in _DETECTORES:
            coincidencia = patron.search(str(texto))
            if coincidencia:
                nombre = _limpiar_nombre(coincidencia.group(1))
                if len(nombre) >= 3:
                    return accion, nombre
    except Exception as error:
        _log.warning('deteccion de accion fallo: %s', error)
    return None


def _buscar_productos(comercio_id, nombre):
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT id, nombre, COALESCE(activo, 1) AS activo, '
                "COALESCE(imagen_estado, 'pendiente') AS imagen_estado "
                'FROM productos WHERE comercio_id = ? AND LOWER(nombre) LIKE ? '
                'ORDER BY id DESC LIMIT 6',
                (int(comercio_id), f'%{_minuscula(nombre)}%'),
            )
            return [dict(f) if not isinstance(f, dict) else f for f in cursor.fetchall()]
    except Exception as error:
        _log.warning('busqueda de producto fallo: %s', error)
        return []


def _elegir_producto(productos, nombre):
    """Un solo objetivo inequivoco: coincidencia exacta o un unico resultado."""
    if not productos:
        return None
    objetivo = _minuscula(nombre)
    exactos = [p for p in productos if _minuscula(p.get('nombre')) == objetivo]
    if len(exactos) == 1:
        return exactos[0]
    if len(productos) == 1:
        return productos[0]
    return None  # ambiguo: no se propone (evita tocar el producto equivocado)


def proponer(comercio_id, texto):
    """Propuesta de accion (NO cambia nada) o ``None``. Nunca lanza."""
    if not habilitado() or not comercio_id:
        return None
    try:
        deteccion = detectar(texto)
        if not deteccion:
            return None
        accion, nombre = deteccion
        producto = _elegir_producto(_buscar_productos(comercio_id, nombre), nombre)
        if not producto:
            return None
        definicion = ACCIONES[accion]
        return {
            'id': secrets.token_urlsafe(16),
            'accion': accion,
            'resumen': f'{definicion["verbo"]} «{producto.get("nombre")}»',
            'mensaje': definicion['mensaje'].format(nombre=producto.get('nombre')),
            'parametros': {'producto_id': int(producto.get('id'))},
            'producto': {
                'id': int(producto.get('id')),
                'nombre': producto.get('nombre'),
                'activo': int(producto.get('activo') or 0),
                'imagen_estado': producto.get('imagen_estado') or 'pendiente',
            },
        }
    except Exception as error:
        _log.warning('propuesta de accion fallo: %s', error)
        return None


def _auditar(usuario_id, accion, detalle):
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'INSERT INTO logs_auditoria (usuario_id, accion, detalles) '
                'VALUES (?, ?, ?)',
                (usuario_id, f'asistente:{accion}', detalle[:500]),
            )
            conexion.commit()
    except Exception as error:
        _log.warning('no se pudo auditar la accion: %s', error)


def ejecutar(usuario_id, comercio_id, accion, producto_id):
    """Aplica la accion (con revalidacion estricta). Devuelve ``(ok, mensaje)``."""
    if not habilitado():
        return False, 'Las acciones no estan disponibles en este momento.'
    if accion not in ACCIONES:
        return False, 'Esa accion no esta permitida.'
    if not usuario_id or not comercio_id:
        return False, 'Necesitas iniciar sesion como comerciante.'
    try:
        pid = int(producto_id)
    except (TypeError, ValueError):
        return False, 'Producto invalido.'
    if pid <= 0:
        return False, 'Producto invalido.'

    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            # Revalidacion: el producto debe ser del comercio de la sesion.
            cursor.execute(
                'SELECT nombre FROM productos WHERE id = ? AND comercio_id = ?',
                (pid, int(comercio_id)),
            )
            fila = cursor.fetchone()
            if not fila:
                return False, 'Ese producto no existe o no es de tu tienda.'
            nombre = fila.get('nombre') if isinstance(fila, dict) else fila[0]

            if accion == 'producto_activar':
                cursor.execute(
                    'UPDATE productos SET activo = 1 WHERE id = ? AND comercio_id = ?',
                    (pid, int(comercio_id)),
                )
            elif accion == 'producto_desactivar':
                cursor.execute(
                    'UPDATE productos SET activo = 0 WHERE id = ? AND comercio_id = ?',
                    (pid, int(comercio_id)),
                )
            elif accion == 'producto_reintentar_imagen':
                cursor.execute(
                    "UPDATE productos SET imagen_estado = 'pendiente', "
                    'imagen_intentos = 0, imagen_ultimo_intento = NULL '
                    'WHERE id = ? AND comercio_id = ?',
                    (pid, int(comercio_id)),
                )
            else:
                return False, 'Esa accion no esta permitida.'
            cambiadas = int(getattr(cursor, 'rowcount', 0) or 0)
            conexion.commit()
    except Exception as error:
        _log.warning('ejecucion de accion fallo: %s', error)
        return False, 'No pude aplicar el cambio. Intenta de nuevo o escribenos por WhatsApp.'

    if cambiadas <= 0:
        return False, 'No se aplico ningun cambio.'
    _auditar(usuario_id, accion, f'producto={pid} nombre={nombre}')
    return True, ACCIONES[accion]['ok'].format(nombre=nombre or 'el producto')