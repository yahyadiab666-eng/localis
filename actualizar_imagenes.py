"""Actualización masiva de imágenes con procesamiento paralelo."""

import sqlite3

from backend.db import get_db_connection
from backend.image_batch import procesar_imagenes_productos_en_lote
from database import DATABASE_URL


def actualizar_imagenes_global(forzar_todos=True):
    if not DATABASE_URL:
        print('❌ DATABASE_URL no configurada.')
        return

    print('📁 Base de datos: PostgreSQL (DATABASE_URL)')

    with get_db_connection(row_factory=sqlite3.Row) as conn:
        cursor = conn.cursor()
        if forzar_todos:
            cursor.execute(
                'SELECT id, comercio_id, nombre, codigo_barras, descripcion, imagen_url FROM productos'
            )
        else:
            cursor.execute(
                """
                SELECT id, comercio_id, nombre, codigo_barras, descripcion, imagen_url
                FROM productos
                WHERE imagen_url IS NULL
                   OR imagen_url = ''
                   OR imagen_url = '__PENDING__'
                   OR imagen_url LIKE '%default-product%'
                """
            )
        filas = cursor.fetchall()

    if not filas:
        print('No hay productos para procesar.')
        return

    print(f'🔍 Procesando {len(filas)} producto(s) en paralelo...\n')

    por_comercio = {}
    for fila in filas:
        cid = fila['comercio_id']
        por_comercio.setdefault(cid, []).append(dict(fila))

    total = 0
    for comercio_id, productos in por_comercio.items():
        n = procesar_imagenes_productos_en_lote(comercio_id, productos)
        total += n
        print(f'  Comercio {comercio_id}: {n} imágenes actualizadas')

    print(f'\n✅ Proceso finalizado. {total} imágenes procesadas en total.')


if __name__ == '__main__':
    actualizar_imagenes_global(forzar_todos=True)
