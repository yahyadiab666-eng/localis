"""Gestión transaccional de imágenes manuales vs. automáticas por producto.

Modelo
======
- **Automáticas** (catálogo público / API): viven en ``imagenes_automaticas``,
  un registro **permanente** por clave de producto (EAN o nombre normalizado).
  Nunca se borran automáticamente: si el comerciante sube una manual, la
  automática permanece cacheada para reutilizarse sin gastar otra consulta.
- **Manuales** (subidas por el comerciante): viven en
  ``productos.imagen_manual_url``. Al reemplazarlas se borra la anterior; al
  quitarlas se borra el archivo y el producto **revierte** a la automática.

- ``productos.imagen_url`` es la vista **activa** (manual si existe, si no la
  automática), que consumen las plantillas y el catálogo público.

Todas las funciones de base de datos son defensivas: si la BD no está
disponible, degradan sin lanzar. La búsqueda por API se ejecuta **una sola vez**
por clave y su resultado (positivo o negativo) queda registrado.
"""

from __future__ import annotations

import os

_LOG = '[Localis Imágenes]'

# Categorías donde se permite la búsqueda automática (Hardware, Technology,
# Appliances, Health) y alimentos como respaldo.
CATEGORIAS_AUTOMATICAS = frozenset(
    {'ferreteria', 'tecnologia', 'hogar', 'salud', 'alimentos'}
)

# Carpetas/prefijos que pertenecen a una subida manual (única cosa borrable).
_CARPETAS_MANUALES = {
    'productos': ('manual_',),
    'comercios': ('logo_',),
    'banners': ('banner_',),
}
_MARCA_STORAGE = '/storage/v1/object/public/'
_PREFIJO_LOCAL = '/static/uploads/'


# ---------------------------------------------------------------------------
# Clave de producto y elegibilidad
# ---------------------------------------------------------------------------
def _texto_plano(valor):
    import re
    import unicodedata

    texto = unicodedata.normalize('NFKD', str(valor or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9]+', ' ', texto)).strip()


def normalizar_clave(codigo_barras=None, nombre=None, descripcion=None):
    """Clave estable del registro automático (``ean:...`` o ``nom:...``)."""
    try:
        from backend.utils import normalizar_codigo_barras

        ean = normalizar_codigo_barras(codigo_barras)
    except Exception:
        ean = str(codigo_barras or '').strip() or None
    if ean:
        return f'ean:{ean}'
    base = _texto_plano(nombre)
    if not base:
        base = _texto_plano(descripcion)
    return f'nom:{base}' if base else None


def categoria_permitida(categoria):
    """True si la categoría admite búsqueda automática (global)."""
    clave = _texto_plano(categoria)
    if clave in CATEGORIAS_AUTOMATICAS:
        return True
    # Texto libre ("Cuidado Personal", "Electrodomésticos", "Víveres").
    try:
        from backend.categorias_producto import clasificar_categoria

        return clasificar_categoria(categoria_hint=categoria) in CATEGORIAS_AUTOMATICAS
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Registro automático (permanente)
# ---------------------------------------------------------------------------
def _fila_dict(fila):
    if fila is None:
        return None
    if isinstance(fila, dict):
        return dict(fila)
    try:
        if hasattr(fila, 'keys'):
            return {clave: fila[clave] for clave in fila.keys()}
    except Exception:
        pass
    return {}


def _conexion():
    """Conexión con filas tipo diccionario (``sqlite3.Row``)."""
    import sqlite3

    from backend.db import get_db_connection

    return get_db_connection(row_factory=sqlite3.Row)


def obtener_automatica(clave):
    """Fila del registro automático para ``clave``, o ``None``."""
    if not clave:
        return None
    try:
        from backend.db import get_db_connection

        with _conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT url_imagen, fuente, termino_busqueda, encontrada,
                       fecha_actualizacion
                FROM imagenes_automaticas
                WHERE clave = ?
                """,
                (str(clave),),
            )
            fila = cursor.fetchone()
            return _fila_dict(fila) if fila is not None else None
    except Exception as error:
        print(f'{_LOG} obtener_automatica fallo clave={clave}: {type(error).__name__}')
        return None


def registrar_automatica(
    *,
    clave=None,
    url=None,
    fuente=None,
    termino=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    producto_id=None,
    comercio_id=None,
    encontrada=None,
):
    """UPSERT permanente de una imagen automática. **Nunca borra**.

    Un resultado previo no se degrada: si ya había URL y el nuevo intento no
    encontró nada, se conserva la URL antigua.
    """
    clave = clave or normalizar_clave(codigo_barras, nombre, descripcion)
    if not clave:
        return False
    hallada = 1 if (url and encontrada is None) or encontrada is True else int(bool(encontrada))
    try:
        from backend.db import get_db_connection

        with _conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO imagenes_automaticas (
                    clave, codigo_barras, nombre_normalizado, producto_id,
                    comercio_id, url_imagen, fuente, termino_busqueda,
                    encontrada, fecha_actualizacion
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (clave) DO UPDATE SET
                    url_imagen = COALESCE(EXCLUDED.url_imagen, imagenes_automaticas.url_imagen),
                    fuente = COALESCE(EXCLUDED.fuente, imagenes_automaticas.fuente),
                    termino_busqueda = COALESCE(
                        EXCLUDED.termino_busqueda, imagenes_automaticas.termino_busqueda
                    ),
                    encontrada = CASE
                        WHEN COALESCE(imagenes_automaticas.encontrada, 0)
                             >= COALESCE(EXCLUDED.encontrada, 0)
                        THEN COALESCE(imagenes_automaticas.encontrada, 0)
                        ELSE COALESCE(EXCLUDED.encontrada, 0) END,
                    producto_id = COALESCE(EXCLUDED.producto_id, imagenes_automaticas.producto_id),
                    comercio_id = COALESCE(EXCLUDED.comercio_id, imagenes_automaticas.comercio_id),
                    fecha_actualizacion = CURRENT_TIMESTAMP
                """,
                (
                    str(clave),
                    codigo_barras,
                    _texto_plano(nombre) or None,
                    int(producto_id) if producto_id else None,
                    int(comercio_id) if comercio_id else None,
                    url,
                    fuente,
                    termino,
                    hallada,
                ),
            )
            conexion.commit()
        return True
    except Exception as error:
        print(f'{_LOG} registrar_automatica fallo clave={clave}: {type(error).__name__}: {error}')
        return False


# ---------------------------------------------------------------------------
# Resolución de la imagen activa
# ---------------------------------------------------------------------------
def resolver_activa(producto_id):
    """Recalcula ``productos.imagen_url`` (manual si existe, si no automática)."""
    try:
        from backend.db import get_db_connection

        with _conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT nombre, descripcion, codigo_barras,
                       imagen_manual_url, imagen_manual_fuente
                FROM productos WHERE id = ?
                """,
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if fila is None:
                return None
            datos = _fila_dict(fila)
            if not datos:
                return None

            manual = datos.get('imagen_manual_url')
            if manual:
                activa, fuente, estado = manual, (datos.get('imagen_manual_fuente') or 'manual'), 'real'
            else:
                clave = normalizar_clave(
                    datos.get('codigo_barras'), datos.get('nombre'), datos.get('descripcion')
                )
                # Se consulta el registro con el MISMO cursor: evita tomar una
                # segunda conexión del pool (menos presión bajo alta concurrencia).
                registro = {}
                if clave:
                    cursor.execute(
                        "SELECT url_imagen, fuente FROM imagenes_automaticas WHERE clave = ?",
                        (clave,),
                    )
                    fila_auto = cursor.fetchone()
                    if fila_auto is not None:
                        registro = _fila_dict(fila_auto) or {}
                url_auto = registro.get('url_imagen')
                if url_auto:
                    activa, fuente, estado = url_auto, (registro.get('fuente') or 'automatica'), 'real'
                else:
                    activa, fuente, estado = None, None, 'pendiente'

            cursor.execute(
                """
                UPDATE productos
                SET imagen_url = ?, imagen_fuente = ?, imagen_estado = ?
                WHERE id = ?
                """,
                (activa, fuente, estado, int(producto_id)),
            )
            conexion.commit()
        return {'activa': activa, 'manual': manual, 'fuente': fuente, 'estado': estado}
    except Exception as error:
        print(f'{_LOG} resolver_activa fallo producto={producto_id}: {type(error).__name__}')
        return None


def tiene_manual(producto_id):
    try:
        from backend.db import get_db_connection

        with _conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_manual_url FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if fila is None:
                return False
            datos = _fila_dict(fila)
            return bool(datos.get('imagen_manual_url'))
    except Exception:
        return False


def obtener_manual(producto_id):
    """URL de la imagen manual del producto, o ``None``."""
    try:
        from backend.db import get_db_connection

        with _conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_manual_url FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if fila is None:
                return None
            return _fila_dict(fila).get('imagen_manual_url')
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ciclo de vida de las imágenes manuales
# ---------------------------------------------------------------------------
def _partes_asset(url):
    texto = str(url or '').strip()
    if texto.startswith(_PREFIJO_LOCAL):
        partes = [p for p in texto[len(_PREFIJO_LOCAL):].split('?', 1)[0].split('/') if p]
        return (partes[-2], partes[-1]) if len(partes) >= 2 else (None, None)
    idx = texto.find(_MARCA_STORAGE)
    if idx >= 0:
        resto = texto[idx + len(_MARCA_STORAGE):].split('?', 1)[0]
        partes = [p for p in resto.split('/') if p]
        if len(partes) >= 3:
            return partes[-2], partes[-1]
        if len(partes) == 2:
            return partes[0], partes[1]
    return (None, None)


def _es_manual(carpeta, filename):
    prefijos = _CARPETAS_MANUALES.get(str(carpeta or '').lower())
    if not prefijos:
        return False
    nombre = str(filename or '').lower()
    return any(nombre.startswith(p) for p in prefijos)


def purgar_manual(url):
    """Borra un asset **manual** (Storage y copia local). Nunca lanza.

    Solo actúa sobre las carpetas/prefijos manuales; jamás toca imágenes
    automáticas, placeholders ni recursos compartidos.
    """
    carpeta, filename = _partes_asset(url)
    if not carpeta or not filename or not _es_manual(carpeta, filename):
        return False
    borrado = False
    # Copia local
    try:
        from config import RUTA_RAIZ
        from backend.uploads_locales import url_upload_local_valida
        from pathlib import Path

        publica = f'{_PREFIJO_LOCAL}{carpeta}/{filename}'
        if url_upload_local_valida(publica):
            destino = Path(RUTA_RAIZ) / 'static' / 'uploads' / carpeta / filename
            if destino.is_file():
                destino.unlink()
                borrado = True
                print(f'{_LOG} manual local borrado: {carpeta}/{filename}')
    except Exception as error:
        print(f'{_LOG} purga local omitida {carpeta}/{filename}: {type(error).__name__}')
    # Storage
    if str(url).startswith('http'):
        try:
            from backend.supabase_client import (
                SUPABASE_BUCKET_IMAGENES,
                obtener_cliente_storage,
            )

            cliente = obtener_cliente_storage()
            if cliente is not None:
                cliente.storage.from_(SUPABASE_BUCKET_IMAGENES).remove(
                    [f'{carpeta}/{filename}']
                )
                borrado = True
                print(f'{_LOG} manual en Storage borrado: {carpeta}/{filename}')
        except Exception as error:
            print(f'{_LOG} purga Storage omitida {carpeta}/{filename}: {type(error).__name__}')
    return borrado


def marcar_manual(producto_id, url, fuente='manual'):
    """Registra una subida manual y borra la manual anterior (si cambió)."""
    url = str(url or '').strip()
    if not url:
        return eliminar_manual(producto_id)
    anterior = None
    try:
        from backend.db import get_db_connection

        with _conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_manual_url FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if fila is not None:
                anterior = _fila_dict(fila).get('imagen_manual_url')
            cursor.execute(
                """
                UPDATE productos
                SET imagen_manual_url = ?, imagen_manual_fuente = ?
                WHERE id = ?
                """,
                (url, fuente, int(producto_id)),
            )
            conexion.commit()
    except Exception as error:
        print(f'{_LOG} marcar_manual fallo producto={producto_id}: {type(error).__name__}')
        return False

    if anterior and anterior != url:
        try:
            purgar_manual(anterior)
        except Exception as error:
            print(f'{_LOG} purga de manual anterior falló: {type(error).__name__}')
    return resolver_activa(producto_id)


def eliminar_manual(producto_id, *, purgar=True):
    """Quita la manual, la borra de storage y revierte a la automática."""
    anterior = None
    try:
        from backend.db import get_db_connection

        with _conexion() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_manual_url FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if fila is not None:
                anterior = _fila_dict(fila).get('imagen_manual_url')
            cursor.execute(
                """
                UPDATE productos
                SET imagen_manual_url = NULL, imagen_manual_fuente = NULL
                WHERE id = ?
                """,
                (int(producto_id),),
            )
            conexion.commit()
    except Exception as error:
        print(f'{_LOG} eliminar_manual fallo producto={producto_id}: {type(error).__name__}')
        return False

    if anterior and purgar:
        try:
            purgar_manual(anterior)
        except Exception as error:
            print(f'{_LOG} purga de manual descartada falló: {type(error).__name__}')
    return resolver_activa(producto_id)


# ---------------------------------------------------------------------------
# Orquestación API: una consulta por producto, con caché permanente
# ---------------------------------------------------------------------------
def buscar_o_cachear_automatica(
    producto_id,
    *,
    categoria=None,
    nombre=None,
    descripcion=None,
    codigo_barras=None,
    limite=8,
):
    """Devuelve la imagen automática para el producto (cache o API).

    - Si ya existe una fila en el registro, se usa (no gasta API).
    - Si no existe y la categoría está permitida y la API está configurada:
      Priority 1 = código de barras; Priority 2 = nombre + descripción.
    - El resultado (positivo o negativo) se registra de forma permanente.

    Retorna ``{'clave', 'url', 'fuente', 'termino', 'desde_cache', 'encontrada'}``.
    """
    resultado = {
        'clave': None,
        'url': None,
        'fuente': None,
        'termino': None,
        'desde_cache': False,
        'encontrada': False,
    }

    clave = normalizar_clave(codigo_barras, nombre, descripcion)
    resultado['clave'] = clave
    if not clave:
        return resultado

    cacheado = obtener_automatica(clave)
    if cacheado is not None:
        resultado['desde_cache'] = True
        resultado['url'] = cacheado.get('url_imagen')
        resultado['fuente'] = cacheado.get('fuente')
        resultado['termino'] = cacheado.get('termino_busqueda')
        resultado['encontrada'] = bool(cacheado.get('encontrada')) and bool(resultado['url'])
        return resultado

    if not categoria_permitida(categoria):
        resultado['fuente'] = 'categoria_no_permitida'
        return resultado

    try:
        from backend import serper_images as proveedor

        if not proveedor.habilitado():
            resultado['fuente'] = 'api_no_configurada'
            return resultado

        def _no_disponible():
            if proveedor.cuota_agotada():
                return 'cuota_agotada'
            if getattr(proveedor, 'api_invalida', lambda: False)():
                return 'api_invalida'
            return None

        motivo = _no_disponible()
        if motivo:
            # No se consulta ni se envenena el caché: queda pendiente.
            resultado['fuente'] = motivo
            return resultado
    except Exception:
        resultado['fuente'] = 'api_no_disponible'
        return resultado

    encontrado = None
    termino_usado = None
    if codigo_barras:
        hallazgos = proveedor.buscar_por_codigo(codigo_barras, limite=limite)
        if hallazgos:
            encontrado, termino_usado = hallazgos[0], str(codigo_barras)
    if encontrado is None and not _no_disponible():
        hallazgos = proveedor.buscar_por_nombre_descripcion(nombre, descripcion, limite=limite)
        if hallazgos:
            encontrado = hallazgos[0]
            termino_usado = ' '.join(
                p for p in [str(nombre or '').strip(), ' '.join(str(descripcion or '').split())[:80]] if p
            )

    motivo_final = _no_disponible()
    if encontrado is None and motivo_final:
        # Cuota/config en mitad de la consulta: pendiente, sin caché negativo.
        resultado['fuente'] = motivo_final
        return resultado

    if encontrado:
        registrar_automatica(
            clave=clave,
            url=encontrado.get('url'),
            fuente='serper',
            termino=termino_usado,
            codigo_barras=codigo_barras,
            nombre=nombre,
            producto_id=producto_id,
            encontrada=True,
        )
        resultado.update(
            {
                'url': encontrado.get('url'),
                'fuente': 'serper',
                'termino': termino_usado,
                'encontrada': True,
            }
        )
    else:
        # Caché negativo permanente: no se vuelve a gastar cuota por este producto.
        registrar_automatica(
            clave=clave,
            url=None,
            fuente='serper',
            termino=termino_usado,
            codigo_barras=codigo_barras,
            nombre=nombre,
            producto_id=producto_id,
            encontrada=False,
        )
        resultado['fuente'] = 'serper'
    return resultado
