"""
Imágenes de producto: subida manual (Storage/local) y pipeline profesional diferido.

- Foto del comerciante: cero llamadas a APIs de pago.
- Sin foto: un hilo daemon ejecuta el pipeline profesional
  (EAN → búsqueda web + "venezuela" → rembg → Supabase Storage) y actualiza
  ``productos.imagen_url``. El request HTTP nunca espera esa red.
"""

from __future__ import annotations

import os
import sqlite3
import threading

from backend.db import get_db_connection
from backend.utils import (
    imagen_url_almacenada,
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    url_imagen_api_oficial_valida,
    url_imagen_local_valida,
    url_imagen_subida_storage_valida,
)

PLACEHOLDER_PRODUCTO = '/static/img/placeholder-producto.svg'

_LOG_CSV = '[Localis CSV]'
_LOG_IMAGEN = '[Localis Imagen]'
_MAX_CSV_API = int(
    os.getenv('LOCALIS_IMG_CSV_MAX', os.getenv('LOCALIS_CSV_API_MAX', '2000'))
)
_descubrimiento_en_vuelo = set()
_asociacion_en_vuelo = set()
_descubrimiento_lock = threading.Lock()


def _registrar_error_imagen(contexto, error):
    print(f'{_LOG_IMAGEN} ERROR {contexto}: {type(error).__name__}: {error}')


def es_imagen_manual(valor):
    return bool(
        url_imagen_subida_storage_valida(valor) or url_imagen_local_valida(valor)
    )


def _url_mostrable_persistida(valor):
    try:
        from utils.images import es_placeholder_producto

        if es_placeholder_producto(valor):
            from backend.utils import texto_campo_imagen

            return texto_campo_imagen(valor, default=None)
    except Exception:
        pass
    return (
        imagen_url_almacenada(valor)
        or url_imagen_api_oficial_valida(valor)
    )


def imagen_url_para_catalogo(imagen_url=None, codigo_barras=None):
    try:
        from utils.images import url_imagen_producto

        return url_imagen_producto(
            imagen_url=imagen_url,
            codigo_barras=codigo_barras,
        )
    except Exception as error:
        _registrar_error_imagen('imagen_url_para_catalogo', error)
        return PLACEHOLDER_PRODUCTO


def persistir_imagen_producto_hibrida(
    file_storage=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    comercio_id=None,
    imagen_url_form=None,
    existente=None,
):
    """
    Foto del dispositivo → Storage o /static/uploads. Costo de API = 0.
    Sin archivo: conserva Storage/local existente. No consulta APIs.
    """
    del codigo_barras, nombre, descripcion
    aviso = None
    hubo_archivo = bool(file_storage and getattr(file_storage, 'filename', ''))
    if hubo_archivo:
        try:
            from backend.supabase_storage import intentar_subir_imagen

            url_subida, aviso = intentar_subir_imagen(
                file_storage,
                prefijo=f'manual_{comercio_id or "prod"}',
                carpeta='productos',
                max_dimension=720,
            )
        except Exception as error:
            _registrar_error_imagen('hibrido subida producto', error)
            from backend.supabase_storage import AVISO_HIBRIDO_USUARIO

            url_subida = None
            aviso = AVISO_HIBRIDO_USUARIO
        persistida = imagen_url_para_persistir(url_subida)
        if persistida:
            return persistida, aviso
        respaldo = imagen_url_para_persistir(imagen_url_form) or (
            imagen_url_para_persistir(existente)
        )
        return respaldo, aviso

    persistida_form = imagen_url_para_persistir(imagen_url_form)
    if persistida_form:
        return persistida_form, aviso
    if es_imagen_manual(existente):
        return imagen_url_para_persistir(existente), aviso
    return None, aviso


def imagen_urls_para_catalogo(productos, con_maestro=True):
    """Lectura: no llama APIs. Enriquece con la URL persistida (y opcionalmente maestro)."""
    if not productos:
        return productos
    try:
        codigos_faltantes = set()
        for prod in productos:
            mostrable = _url_mostrable_persistida(prod.get('imagen_url'))
            if mostrable:
                prod['imagen_url'] = mostrable
            else:
                codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
                if codigo:
                    codigos_faltantes.add(codigo)

        if codigos_faltantes and con_maestro:
            from backend.catalogo_maestro import mapa_imagenes_maestro
            mapa_maestro = mapa_imagenes_maestro(list(codigos_faltantes))
            for prod in productos:
                if not prod.get('imagen_url'):
                    codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
                    if codigo and codigo in mapa_maestro:
                        prod['imagen_url'] = mapa_maestro[codigo]
                    else:
                        prod['imagen_url'] = PLACEHOLDER_PRODUCTO
        else:
            for prod in productos:
                if not prod.get('imagen_url'):
                    prod['imagen_url'] = PLACEHOLDER_PRODUCTO

        return productos
    except Exception as error:
        _registrar_error_imagen('imagen_urls_para_catalogo', error)
        return productos


def obtener_imagen_url_producto(producto_id):
    if not producto_id:
        return None
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_url, codigo_barras FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return None
            registro = dict(fila)
        from utils.images import url_publica_producto_desde_bd

        return (
            url_publica_producto_desde_bd(registro.get('imagen_url'))
            or PLACEHOLDER_PRODUCTO
        )
    except Exception as error:
        _registrar_error_imagen(f'obtener_imagen_url_producto({producto_id})', error)
        return PLACEHOLDER_PRODUCTO


def preparar_mapa_imagenes_importacion(productos, snapshot_imagenes=None):
    """CSV: solo fotos ya persistidas (manual/Storage). Sin APIs de pago."""
    mapa = dict(snapshot_imagenes or {})
    for prod in productos or []:
        persistida = imagen_url_para_persistir(prod.get('imagen_url'))
        codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
        if persistida and codigo and codigo not in mapa:
            mapa[codigo] = persistida
    return mapa


def asociar_imagenes_inventario(comercio_id):
    """Tras CSV: asigna fotos profesionales en segundo plano (pipeline local).

    Usa el pipeline de imágenes profesional (EAN → búsqueda web → rembg →
    Supabase Storage). Sin APIs de pago: cero suscripciones.
    """
    try:
        from services.professional_image_pipeline import procesar_inventario

        return procesar_inventario(comercio_id, limite=_MAX_CSV_API)
    except Exception as error:
        print(f'{_LOG_CSV} pipeline profesional no disponible: {type(error).__name__}: {error}')
        return 0


def programar_asociacion_imagenes_inventario(comercio_id):
    if comercio_id is None:
        return None
    with _descubrimiento_lock:
        if comercio_id in _asociacion_en_vuelo:
            print(f'{_LOG_CSV} pipeline ya en vuelo comercio={comercio_id}, omitido')
            return None
        _asociacion_en_vuelo.add(comercio_id)

    def _trabajo():
        try:
            print(f'{_LOG_CSV} pipeline profesional inicio comercio={comercio_id}')
            actualizados = asociar_imagenes_inventario(comercio_id)
            print(
                f'{_LOG_CSV} pipeline profesional fin comercio={comercio_id} '
                f'actualizados={actualizados}'
            )
        except Exception as error:
            print(
                f'{_LOG_CSV} aviso pipeline diferido comercio={comercio_id}: '
                f'{type(error).__name__}'
            )
        finally:
            with _descubrimiento_lock:
                _asociacion_en_vuelo.discard(comercio_id)

    hilo = threading.Thread(
        target=_trabajo,
        name=f'localis-csv-img-{comercio_id}',
        daemon=True,
    )
    hilo.start()
    return hilo


def programar_descubrimiento_producto(producto_id, categoria=None):
    """Tras el alta: ejecuta el pipeline profesional en segundo plano.

    Solo actúa si el producto no tiene foto manual/definitiva. No bloquea nunca
    la respuesta HTTP ni depende de APIs de pago.
    """
    if not producto_id:
        print(f'{_LOG_IMAGEN} descubrimiento omitido: producto_id vacío')
        return False
    pid = int(producto_id)
    with _descubrimiento_lock:
        if pid in _descubrimiento_en_vuelo:
            print(f'{_LOG_IMAGEN} descubrimiento producto={pid} ya en vuelo, omitido')
            return False
        _descubrimiento_en_vuelo.add(pid)

    print(f'{_LOG_IMAGEN} pipeline profesional programado producto={pid} categoria={categoria!r}')

    def _trabajo():
        try:
            from services.professional_image_pipeline import procesar_producto

            resultado = procesar_producto(pid, categoria=categoria)
            print(
                f'{_LOG_IMAGEN} pipeline producto={pid} ok={resultado.ok} '
                f'fuente={resultado.fuente!r} motivo={resultado.motivo!r} url={resultado.url!r}'
            )
        except Exception as error:
            _registrar_error_imagen(f'descubrimiento producto={pid}', error)
        finally:
            with _descubrimiento_lock:
                _descubrimiento_en_vuelo.discard(pid)

    threading.Thread(
        target=_trabajo,
        name=f'localis-foto-{pid}',
        daemon=True,
    ).start()
    return True
