"""Procesamiento en lote / segundo plano de imágenes de productos."""

import threading

from backend.db import get_db_connection
from backend.images import procesar_imagenes_paralelo

IMAGEN_PENDIENTE = '__PENDING__'
DEFAULT_IMAGEN = '/static/images/default-product.webp'


def _actualizar_imagen_producto(producto_id, url):
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            'UPDATE productos SET imagen_url = ? WHERE id = ?',
            (url, producto_id),
        )
        conexion.commit()


def procesar_imagenes_productos_en_lote(comercio_id, productos_info, upload_folder):
    """
    Procesa imágenes en paralelo y actualiza SQLite.
    productos_info: lista de dicts con id, nombre, codigo_barras, descripcion, imagen_url
    """
    tareas = []
    for prod in productos_info:
        prod_id = prod['id']
        imagen_url = (prod.get('imagen_url') or '').strip()

        if imagen_url and imagen_url not in (IMAGEN_PENDIENTE, '', 'None'):
            if imagen_url.startswith('/static/') and 'default-product' not in imagen_url:
                continue
            tareas.append({
                'tipo': 'url',
                'url': imagen_url,
                'producto_id': prod_id,
                'upload_folder': upload_folder,
                'prefijo': f'prod_{comercio_id}',
            })
        else:
            tareas.append({
                'tipo': 'buscar',
                'producto_id': prod_id,
                'nombre': prod.get('nombre', ''),
                'codigo_barras': prod.get('codigo_barras'),
                'descripcion': prod.get('descripcion'),
                'upload_folder': upload_folder,
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


def encolar_procesamiento_imagenes(comercio_id, productos_info, upload_folder):
    """Lanza el procesamiento de imágenes en un hilo daemon (no bloquea la respuesta HTTP)."""

    def _worker():
        try:
            n = procesar_imagenes_productos_en_lote(
                comercio_id, productos_info, upload_folder
            )
            print(
                f'[Localis] Imágenes procesadas en lote para comercio {comercio_id}: {n}'
            )
        except Exception as e:
            print(f'[Localis] Error en lote de imágenes comercio {comercio_id}: {e}')

    hilo = threading.Thread(target=_worker, daemon=True)
    hilo.start()
    return hilo
