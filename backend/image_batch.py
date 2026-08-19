"""Procesamiento en lote / segundo plano de imágenes de productos."""

import threading

from backend.db import get_db_connection
from backend.images import procesar_imagenes_paralelo
from backend.utils import texto_campo_imagen, url_imagen_usable

IMAGEN_PENDIENTE = '__PENDING__'


def _actualizar_imagen_producto(producto_id, url):
    url = texto_campo_imagen(url, default=None)
    if not url_imagen_usable(url):
        return
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            'UPDATE productos SET imagen_url = ? WHERE id = ?',
            (url, producto_id),
        )
        conexion.commit()


def procesar_imagenes_productos_en_lote(comercio_id, productos_info):
    """
    Resuelve URLs de imágenes externas sin guardar en disco local.
    productos_info: lista de dicts con id, nombre, codigo_barras, descripcion, imagen_url
    """
    tareas = []
    for prod in productos_info:
        prod_id = prod['id']
        imagen_url = texto_campo_imagen(prod.get('imagen_url'), default='')

        if url_imagen_usable(imagen_url) and imagen_url.startswith('/static/'):
            continue
        if url_imagen_usable(imagen_url) and imagen_url.startswith(('http://', 'https://')):
            tareas.append({
                'tipo': 'url',
                'url': imagen_url,
                'producto_id': prod_id,
                'prefijo': f'prod_{comercio_id}',
            })
        else:
            tareas.append({
                'tipo': 'buscar',
                'producto_id': prod_id,
                'nombre': prod.get('nombre', ''),
                'codigo_barras': prod.get('codigo_barras'),
                'descripcion': prod.get('descripcion'),
                'prefijo': f'prod_{comercio_id}',
            })

    if not tareas:
        return 0

    resultados = procesar_imagenes_paralelo(tareas)
    actualizados = 0
    for res in resultados:
        if res.get('producto_id') and res.get('url'):
            _actualizar_imagen_producto(res['producto_id'], res['url'])
            actualizados += 1
    return actualizados


def encolar_procesamiento_imagenes(comercio_id, productos_info):
    """Lanza el procesamiento de imágenes en un hilo daemon (no bloquea la respuesta HTTP)."""

    def _worker():
        try:
            n = procesar_imagenes_productos_en_lote(comercio_id, productos_info)
            print(
                f'[Localis] Imágenes procesadas en lote para comercio {comercio_id}: {n}'
            )
        except Exception as e:
            print(f'[Localis] Error en lote de imágenes comercio {comercio_id}: {e}')

    hilo = threading.Thread(target=_worker, daemon=True)
    hilo.start()
    return hilo
