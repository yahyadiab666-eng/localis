"""Ubicaciones (sedes) de un comercio: lista con nombre propio y Google Maps.

Sustituye la captura manual de ``direccion`` / ``ciudad`` / ``zona``: el
comerciante declara un **nombre** por sede y pega el **enlace de Google Maps**;
el sistema deriva de ahí lo que antes se escribía a mano (dirección de
referencia y coordenadas) mediante :func:`backend.utils.derivar_datos_maps`.

Reglas:
- Mínimo una sede y máximo :data:`MAX_UBICACIONES` en los formularios.
- La primera de la lista se considera la **principal**: sus datos alimentan
  las columnas históricas ``comercios.direccion`` / ``maps_url`` para no romper
  buscador, SEO ni las vistas antiguas.
"""

import logging
import threading

from backend.geo_ubicacion import (
    enriquecer_ubicaciones_geo,
    geocodificacion_habilitada,
)
from backend.utils import derivar_datos_maps, url_maps_comercio

logger = logging.getLogger('localis.ubicaciones')

# Tope de sedes por comercio (los formularios lo reflejan en el repeater).
MAX_UBICACIONES = 10

LOG_PREFIX = '[Localis Ubicaciones]'

# Geocodificación en segundo plano: NO se hace esperar al comerciante. Cada
# comercio se procesa una sola vez a la vez.
_geo_en_curso = set()
_geo_lock = threading.Lock()


def _valor(fila, clave, indice=0):
    """Extrae un valor de una fila dict o tupla sin lanzar."""
    try:
        if isinstance(fila, dict):
            return fila.get(clave)
        return fila[indice]
    except Exception:
        return None


def normalizar_ubicaciones(ubicaciones, geocodificar=False):
    """Valida y normaliza la lista de sedes recibida desde un formulario.

    Acepta una lista de dicts con ``nombre`` y ``maps_url``. Ignora las filas
    totalmente vacías (permite plantillas con campos opcionales).

    Con ``geocodificar=True`` se completa ``ciudad``/``zona``/``direccion``
    resolviendo el enlace de Google Maps (red). Por defecto es ``False`` para
    que esta función siga siendo **pura** y se pueda probar sin red.

    Retorna ``(True, lista)`` o ``(False, mensaje)``. Nunca lanza.
    """
    lista = []
    for entrada in (ubicaciones or []):
        if not isinstance(entrada, dict):
            continue
        nombre = str(entrada.get('nombre') or '').strip()
        maps_url = str(entrada.get('maps_url') or '').strip()
        if not nombre and not maps_url:
            continue  # fila vacía: se omite
        if not nombre:
            return False, 'Cada ubicación necesita un nombre.'
        if not maps_url:
            return False, f'La ubicación "{nombre}" necesita su enlace de Google Maps.'

        datos = derivar_datos_maps(maps_url)
        if not datos.get('valido'):
            return False, (
                f'El enlace de Google Maps de "{nombre}" no parece válido. '
                'Pega el enlace que copia la app de Google Maps.'
            )

        lista.append({
            'nombre': nombre,
            'maps_url': maps_url,
            'direccion': datos.get('direccion') or nombre,
            'ciudad': entrada.get('ciudad') or None,
            'zona': entrada.get('zona') or None,
            'coordinates_maps': datos.get('coordinates_maps'),
            'orden': len(lista),
            'es_principal': 1 if not lista else 0,
        })

    if len(lista) > MAX_UBICACIONES:
        return False, f'Solo se permiten hasta {MAX_UBICACIONES} ubicaciones.'

    if geocodificar and lista:
        # Rellena ciudad/zona/dirección desde el enlace de Maps (red, defensivo).
        lista = enriquecer_ubicaciones_geo(lista)

    return True, lista


def ubicaciones_desde_form(form):
    """Construye la lista de sedes desde listas paralelas del formulario.

    Lee ``ubicacion_nombre[]`` y ``ubicacion_maps[]`` en orden. Tolerante a
    listas de distinto largo (rellena con vacío). Nunca lanza.
    """
    lista = []
    try:
        nombres = form.getlist('ubicacion_nombre')
        urls = form.getlist('ubicacion_maps')
    except Exception:
        return lista
    for indice, nombre in enumerate(nombres):
        url = urls[indice] if indice < len(urls) else ''
        lista.append({'nombre': nombre, 'maps_url': url})
    return lista


def datos_ubicacion_principal(ubicaciones):
    """Datos para las columnas históricas de ``comercios`` desde la sede principal.

    La primera sede de la lista es la principal. Si no hay ninguna, todos los
    valores quedan en ``None`` (columnas preservadas pero vacías).
    """
    principal = (ubicaciones or [None])[0] if ubicaciones else None
    if not principal:
        return {
            'direccion': None,
            'ciudad': None,
            'zona': None,
            'maps_url': None,
            'ubicacion_maps_url': None,
        }
    maps_url = principal.get('maps_url') or None
    return {
        'direccion': principal.get('direccion') or principal.get('nombre') or None,
        # Ciudad/zona se rellenan automáticamente desde el enlace de Maps, para
        # que la búsqueda por ubicación funcione como antes en los comercios
        # nuevos (que ya no escriben ciudad/zona a mano).
        'ciudad': principal.get('ciudad') or None,
        'zona': principal.get('zona') or None,
        'maps_url': maps_url,
        'ubicacion_maps_url': maps_url,
    }


def guardar_ubicaciones(cursor, comercio_id, ubicaciones):
    """Reemplaza las sedes de un comercio (borra e inserta) dentro de una tx.

    Lanza si falla: el llamador decide si abortar la transacción completa.
    """
    if not comercio_id:
        return
    cursor.execute('DELETE FROM sucursales WHERE comercio_id = ?', (int(comercio_id),))
    for sede in (ubicaciones or []):
        cursor.execute(
            """
            INSERT INTO sucursales (
                comercio_id, direccion, coordinates_maps,
                nombre, maps_url, orden, es_principal, ciudad, zona
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(comercio_id),
                sede.get('direccion') or sede.get('nombre') or 'Ubicación',
                sede.get('coordinates_maps'),
                sede.get('nombre'),
                sede.get('maps_url'),
                int(sede.get('orden') or 0),
                int(sede.get('es_principal') or 0),
                sede.get('ciudad'),
                sede.get('zona'),
            ),
        )


def cargar_ubicaciones(cursor, comercio_id):
    """Lista las sedes de un comercio ordenadas (principal primero). Nunca lanza."""
    if not comercio_id:
        return []
    cursor_propio = cursor is None
    conexion = None
    try:
        if cursor_propio:
            from backend.db import get_db_connection

            conexion = get_db_connection()
            cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT id, comercio_id, nombre, direccion, maps_url,
                   coordinates_maps, orden, es_principal, ciudad, zona
            FROM sucursales
            WHERE comercio_id = ?
            ORDER BY es_principal DESC, orden ASC, id ASC
            """,
            (int(comercio_id),),
        )
        filas = cursor.fetchall() or []
        sedes = []
        for fila in filas:
            sede = {
                'id': _valor(fila, 'id', 0),
                'comercio_id': _valor(fila, 'comercio_id', 1),
                'nombre': _valor(fila, 'nombre', 2),
                'direccion': _valor(fila, 'direccion', 3),
                'maps_url': _valor(fila, 'maps_url', 4),
                'coordinates_maps': _valor(fila, 'coordinates_maps', 5),
                'orden': _valor(fila, 'orden', 6),
                'es_principal': _valor(fila, 'es_principal', 7),
                'ciudad': _valor(fila, 'ciudad', 8),
                'zona': _valor(fila, 'zona', 9),
            }
            sedes.append(sede)
        return sedes
    except Exception as error:
        logger.error(f'{LOG_PREFIX} cargar comercio {comercio_id}: {error}')
        return []
    finally:
        if cursor_propio and conexion is not None:
            try:
                conexion.close()
            except Exception:
                pass


def adjuntar_ubicaciones(comercio):
    """Añade ``sucursales`` / ``ubicaciones`` al dict de un comercio.

    Idempotente y defensivo: si algo falla, devuelve el comercio sin tocar.
    """
    if not comercio:
        return comercio
    try:
        comercio_id = comercio.get('id') if isinstance(comercio, dict) else None
        if not comercio_id:
            return comercio
        sedes = cargar_ubicaciones(None, comercio_id)
        comercio['sucursales'] = sedes
        comercio['ubicaciones'] = sedes
        # Cada sede recibe su enlace de Maps resuelto (enlace guardado o nombre).
        for sede in sedes:
            if not sede.get('maps_link'):
                sede['maps_link'] = url_maps_comercio({
                    'maps_url': sede.get('maps_url'),
                    'direccion': sede.get('direccion'),
                    'ciudad': None,
                    'zona': None,
                })
        # Compatibilidad: si el comercio no tiene Maps guardado, se usa el de la
        # sede principal para el botón público "Cómo llegar".
        if not comercio.get('maps_link'):
            comercio['maps_link'] = url_maps_comercio(comercio)
    except Exception as error:
        logger.error(f'{LOG_PREFIX} adjuntar: {error}')
    return comercio


def _geocodificar_comercio(comercio_id):
    """Trabajo real: completa ciudad/zona/dirección de las sedes y las guarda."""
    from backend.db import get_db_connection

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            sedes = cargar_ubicaciones(cursor, comercio_id)
            if not sedes:
                return
            enriquecidas = enriquecer_ubicaciones_geo(sedes)
            if enriquecidas == sedes:
                return  # nada nuevo que guardar
            guardar_ubicaciones(cursor, comercio_id, enriquecidas)
            principal = datos_ubicacion_principal(enriquecidas)
            cursor.execute(
                """
                UPDATE comercios
                SET direccion = ?, ciudad = ?, zona = ?,
                    maps_url = ?, ubicacion_maps_url = ?
                WHERE id = ?
                """,
                (
                    principal['direccion'],
                    principal['ciudad'],
                    principal['zona'],
                    principal['maps_url'],
                    principal['ubicacion_maps_url'],
                    int(comercio_id),
                ),
            )
            conexion.commit()
        logger.info(f'{LOG_PREFIX} sedes geocodificadas (comercio {comercio_id})')
    except Exception as error:
        logger.error(f'{LOG_PREFIX} geocodificación comercio {comercio_id}: {error}')


def geocodificar_comercio_en_segundo_plano(comercio_id):
    """Completa la ubicación de las sedes **sin bloquear** la petición.

    El enlace de Maps se resuelve y geocodifica en un hilo aparte: el formulario
    responde al instante y ``ciudad``/``zona`` aparecen unos segundos después.
    Nunca lanza; si la geocodificación está apagada no hace nada.
    """
    if not comercio_id or not geocodificacion_habilitada():
        return
    try:
        with _geo_lock:
            if comercio_id in _geo_en_curso:
                return
            _geo_en_curso.add(comercio_id)
        hilo = threading.Thread(
            target=_geocodificar_en_hilo,
            args=(comercio_id,),
            name=f'localis-geo-{comercio_id}',
            daemon=True,
        )
        hilo.start()
    except Exception as error:
        with _geo_lock:
            _geo_en_curso.discard(comercio_id)
        logger.error(f'{LOG_PREFIX} no se pudo lanzar la geocodificación: {error}')


def _marcar_geocodificacion_terminada(comercio_id):
    """Libera la marca del comercio (la usa el envoltorio del hilo)."""
    with _geo_lock:
        _geo_en_curso.discard(comercio_id)


def _geocodificar_en_hilo(comercio_id):
    """Envuelve el trabajo para garantizar que SIEMPRE se libera la marca.

    La liberación va aquí (no dentro del trabajo): si fallara el import o algo
    previo al ``try``, el comercio quedaría marcado para siempre y nunca se
    geocodificaría.
    """
    try:
        _geocodificar_comercio(comercio_id)
    except Exception as error:
        logger.error(f'{LOG_PREFIX} hilo de geocodificación {comercio_id}: {error}')
    finally:
        _marcar_geocodificacion_terminada(comercio_id)


def ubicaciones_desde_comercio(comercio):
    """Sedes de un comercio ya cargado o, si faltan, se consultan. Nunca lanza."""
    if not comercio:
        return []
    if isinstance(comercio, dict) and comercio.get('sucursales'):
        return comercio.get('sucursales')
    if isinstance(comercio, dict):
        return cargar_ubicaciones(None, comercio.get('id'))
    return []
