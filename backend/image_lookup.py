"""Resolución dinámica de imágenes por código de barras, nombre+descripción y bucket Supabase."""

import os
import sqlite3

import requests

from backend.db import get_db_connection
from backend.supabase_client import SUPABASE_URL, url_publica_bucket
from backend.utils import (
    normalizar_clave_imagen_catalogo,
    normalizar_codigo_barras,
    normalizar_nombre_producto,
    texto_campo_imagen,
    url_imagen_producto_default,
    url_imagen_supabase_valida,
)

# PostgreSQL: normalización de código de barras en consultas SQL.
EXPR_CODIGO_BARRAS = (
    "regexp_replace("
    "regexp_replace(TRIM(BOTH FROM CAST(codigo_barras AS TEXT)), '\\s+', '', 'g'), "
    "'\\.0+$', '', 'g')"
)
EXPR_NOMBRE = (
    "regexp_replace(LOWER(TRIM(BOTH FROM CAST(nombre AS TEXT))), '\\s+', ' ', 'g')"
)

_FILTRO_IMAGEN_REAL = """
    imagen_url IS NOT NULL
    AND TRIM(BOTH FROM CAST(imagen_url AS TEXT)) <> ''
    AND imagen_url NOT ILIKE '%%default-product%%'
    AND imagen_url NOT ILIKE '%%placeholder%%'
    AND TRIM(BOTH FROM CAST(imagen_url AS TEXT)) <> '__PENDING__'
    AND CAST(imagen_url AS TEXT) ILIKE 'https://%%/storage/v1/object/public/%%'
"""

_EXTENSIONES_BUCKET = ('webp', 'jpg', 'jpeg', 'png')
_CACHE_EXISTENCIA = {}


def _existe_en_bucket(url: str) -> bool:
    """Verificación remota HEAD: solo al persistir (import/create/update)."""
    if not url:
        return False
    if url in _CACHE_EXISTENCIA:
        return _CACHE_EXISTENCIA[url]
    try:
        respuesta = requests.head(url, timeout=4, allow_redirects=True)
        ok = respuesta.status_code == 200
    except requests.RequestException:
        ok = False
    _CACHE_EXISTENCIA[url] = ok
    return ok


def _url_usable_o_none(valor):
    return url_imagen_supabase_valida(valor)


def _urls_candidatas_bucket(stem):
    """URLs públicas posibles en productos/{stem}.{ext} (sin descargar al servidor)."""
    if not stem or not SUPABASE_URL:
        return []
    stem_limpio = str(stem).strip().strip('/')
    if not stem_limpio:
        return []
    return [
        url_publica_bucket('productos', f'{stem_limpio}.{extension}')
        for extension in _EXTENSIONES_BUCKET
    ]


def _url_bucket_dinamica(stem, extension='webp'):
    """Enlace directo al bucket para catálogo (sin HEAD)."""
    if not stem or not SUPABASE_URL:
        return None
    stem_limpio = str(stem).strip().strip('/')
    if not stem_limpio:
        return None
    return url_publica_bucket('productos', f'{stem_limpio}.{extension}')


def _primera_url_bucket_existente(stem):
    """Primera URL verificada en bucket (solo escritura)."""
    for url in _urls_candidatas_bucket(stem):
        if _existe_en_bucket(url):
            return url
    return None


def _url_desde_referencia_archivo(referencia):
    """Convierte nombre de archivo del CSV a URL pública del bucket."""
    from backend.utils import es_imagen_generica

    if not referencia or not SUPABASE_URL:
        return None
    bruto = str(referencia).strip().replace('\\', '/')
    nombre = os.path.basename(bruto)
    if not nombre or es_imagen_generica(nombre):
        return None
    if nombre.startswith('https://') and url_imagen_supabase_valida(nombre):
        return nombre
    return url_publica_bucket('productos', nombre)


def buscar_imagen_supabase_por_codigo(codigo_barras, verificar=False):
    """Resuelve productos/{codigo}.{ext} en el bucket Supabase."""
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo:
        return None
    if verificar:
        return _primera_url_bucket_existente(codigo)
    return _url_bucket_dinamica(codigo)


def buscar_imagen_supabase_por_clave(nombre, descripcion=None, verificar=False):
    """Resuelve productos/{nombre-descripcion}.{ext} en el bucket Supabase."""
    clave = normalizar_clave_imagen_catalogo(nombre, descripcion)
    if not clave:
        return None
    if verificar:
        return _primera_url_bucket_existente(clave)
    return _url_bucket_dinamica(clave)


def _mapa_desde_filas(filas, clave_norm):
    resultado = {}
    for fila in filas or []:
        fila = dict(fila)
        clave = fila.get(clave_norm)
        url = _url_usable_o_none(fila.get('imagen_url'))
        if clave and url and clave not in resultado:
            resultado[clave] = url
    return resultado


def mapa_imagenes_por_codigos(codigos):
    """Primera imagen real por código de barras normalizado en todo el catálogo."""
    normalizados = []
    vistos = set()
    for codigo in codigos or []:
        limpio = normalizar_codigo_barras(codigo)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)
    if not normalizados:
        return {}

    placeholders = ','.join(['?'] * len(normalizados))
    sql = f"""
        SELECT {EXPR_CODIGO_BARRAS} AS codigo_norm, imagen_url
        FROM productos
        WHERE codigo_barras IS NOT NULL
          AND TRIM(BOTH FROM CAST(codigo_barras AS TEXT)) <> ''
          AND {EXPR_CODIGO_BARRAS} IN ({placeholders})
          AND {_FILTRO_IMAGEN_REAL}
        ORDER BY id DESC
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql, tuple(normalizados))
            return _mapa_desde_filas(cursor.fetchall(), 'codigo_norm')
    except Exception as error:
        print(f'Error al buscar imágenes por código de barras: {error}')
        return {}


def mapa_imagenes_por_nombres(nombres):
    """Respaldo por nombre cuando no hay código de barras."""
    normalizados = []
    vistos = set()
    for nombre in nombres or []:
        limpio = normalizar_nombre_producto(nombre)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)
    if not normalizados:
        return {}

    placeholders = ','.join(['?'] * len(normalizados))
    sql = f"""
        SELECT {EXPR_NOMBRE} AS nombre_norm, imagen_url
        FROM productos
        WHERE nombre IS NOT NULL
          AND TRIM(BOTH FROM CAST(nombre AS TEXT)) <> ''
          AND {EXPR_NOMBRE} IN ({placeholders})
          AND {_FILTRO_IMAGEN_REAL}
        ORDER BY id DESC
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql, tuple(normalizados))
            return _mapa_desde_filas(cursor.fetchall(), 'nombre_norm')
    except Exception as error:
        print(f'Error al buscar imágenes por nombre: {error}')
        return {}


def _cargar_mapas_catalogo_global():
    """Mapas código / nombre / clave nombre+descripción → imagen_url del catálogo PostgreSQL."""
    mapa_codigos = {}
    mapa_nombres = {}
    mapa_claves = {}
    sql = f"""
        SELECT codigo_barras, nombre, descripcion, imagen_url
        FROM productos
        WHERE {_FILTRO_IMAGEN_REAL}
        ORDER BY id DESC
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql)
            for fila in cursor.fetchall():
                registro = dict(fila)
                url = _url_usable_o_none(registro.get('imagen_url'))
                if not url:
                    continue
                codigo = normalizar_codigo_barras(registro.get('codigo_barras'))
                if codigo and codigo not in mapa_codigos:
                    mapa_codigos[codigo] = url
                nombre = normalizar_nombre_producto(registro.get('nombre'))
                if nombre and nombre not in mapa_nombres:
                    mapa_nombres[nombre] = url
                clave = normalizar_clave_imagen_catalogo(
                    registro.get('nombre'),
                    registro.get('descripcion'),
                )
                if clave and clave not in mapa_claves:
                    mapa_claves[clave] = url
    except Exception as error:
        print(f'Error al cargar mapas de imágenes del catálogo: {error}')
    return mapa_codigos, mapa_nombres, mapa_claves


def _resolver_url_catalogo(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    verificar_bucket=False,
):
    """
    Resuelve URL de imagen sin descargar archivos al servidor.
    1) URL Supabase ya guardada o referenciada en CSV
    2) Catálogo PostgreSQL / bucket por código de barras
    3) Catálogo PostgreSQL / bucket por nombre + descripción
    4) default-product.webp
    """
    directa = _url_usable_o_none(imagen_url)
    if directa:
        return directa

    referencia = texto_campo_imagen(imagen_url, default=None)
    if referencia:
        por_archivo = _url_desde_referencia_archivo(referencia)
        if por_archivo:
            if verificar_bucket:
                stem = os.path.splitext(os.path.basename(referencia))[0]
                verificada = _primera_url_bucket_existente(stem)
                if verificada:
                    return verificada
            else:
                return por_archivo

    codigo = normalizar_codigo_barras(codigo_barras) or normalizar_codigo_barras(referencia)
    if codigo:
        url_cat = mapa_imagenes_por_codigos([codigo]).get(codigo)
        if url_cat:
            return url_cat
        url_bucket = buscar_imagen_supabase_por_codigo(codigo, verificar=verificar_bucket)
        if url_bucket:
            return url_bucket

    clave = normalizar_clave_imagen_catalogo(nombre, descripcion)
    if clave:
        url_bucket = buscar_imagen_supabase_por_clave(
            nombre, descripcion, verificar=verificar_bucket
        )
        if url_bucket:
            return url_bucket

    nombre_norm = normalizar_nombre_producto(nombre)
    if nombre_norm:
        url_cat = mapa_imagenes_por_nombres([nombre_norm]).get(nombre_norm)
        if url_cat:
            return url_cat

    return url_imagen_producto_default()


def imagen_url_para_catalogo(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
):
    """Lectura para catálogos: enlace dinámico sin HEAD ni descargas."""
    return _resolver_url_catalogo(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
        verificar_bucket=False,
    )


def resolver_imagen_url_definitiva(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
):
    """Escritura: verifica existencia en bucket antes de persistir en BD."""
    return _resolver_url_catalogo(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
        verificar_bucket=True,
    )


def normalizar_imagen_registro(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
):
    return imagen_url_para_catalogo(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
    )


def obtener_imagen_url_producto(producto_id):
    """Resuelve imagen de un producto usando sus datos en PostgreSQL."""
    if not producto_id:
        return url_imagen_producto_default()
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                '''
                SELECT imagen_url, codigo_barras, nombre, descripcion
                FROM productos WHERE id = ?
                ''',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return url_imagen_producto_default()
            registro = dict(fila)
            return imagen_url_para_catalogo(
                registro.get('imagen_url'),
                codigo_barras=registro.get('codigo_barras'),
                nombre=registro.get('nombre'),
                descripcion=registro.get('descripcion'),
            )
    except Exception as error:
        print(f'Error al leer imagen del producto {producto_id}: {error}')
        return url_imagen_producto_default()


def resolver_imagen_producto(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    producto_id=None,
    buscar_web=False,
    excluir_url=None,
    persistir=False,
):
    del buscar_web

    if persistir:
        url = resolver_imagen_url_definitiva(
            imagen_url, codigo_barras, nombre=nombre, descripcion=descripcion
        )
    else:
        url = imagen_url_para_catalogo(
            imagen_url, codigo_barras, nombre=nombre, descripcion=descripcion
        )

    if url and url != excluir_url:
        return url

    if producto_id:
        url_bd = obtener_imagen_url_producto(producto_id)
        if url_bd and url_bd != excluir_url:
            return url_bd

    return url_imagen_producto_default()


def aplicar_respaldo_imagenes(productos, persistir=False):
    """Asigna imagen_url dinámica por código, nombre+descripción o default."""
    if not productos:
        return productos

    if persistir:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            for prod in productos:
                producto_id = prod.get('id')
                if not producto_id:
                    continue
                url_final = resolver_imagen_url_definitiva(
                    prod.get('imagen_url'),
                    prod.get('codigo_barras'),
                    nombre=prod.get('nombre'),
                    descripcion=prod.get('descripcion'),
                )
                cursor.execute(
                    'UPDATE productos SET imagen_url = ? WHERE id = ?',
                    (url_final, int(producto_id)),
                )
                prod['imagen_url'] = url_final
            conexion.commit()
        return productos

    mapa_codigos, mapa_nombres, mapa_claves = _cargar_mapas_catalogo_global()
    default_url = url_imagen_producto_default()

    for prod in productos:
        if _url_usable_o_none(prod.get('imagen_url')):
            prod['imagen_url'] = texto_campo_imagen(prod.get('imagen_url'))
            continue

        referencia = texto_campo_imagen(prod.get('imagen_url'), default=None)
        if referencia:
            por_archivo = _url_desde_referencia_archivo(referencia)
            if por_archivo:
                prod['imagen_url'] = por_archivo
                continue

        codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
        url = mapa_codigos.get(codigo) if codigo else None
        if not url and codigo:
            url = buscar_imagen_supabase_por_codigo(codigo, verificar=False)

        if not url:
            clave = normalizar_clave_imagen_catalogo(
                prod.get('nombre'),
                prod.get('descripcion'),
            )
            if clave:
                url = mapa_claves.get(clave)
                if not url:
                    url = buscar_imagen_supabase_por_clave(
                        prod.get('nombre'),
                        prod.get('descripcion'),
                        verificar=False,
                    )

        if not url:
            nombre = normalizar_nombre_producto(prod.get('nombre'))
            if nombre:
                url = mapa_nombres.get(nombre)

        prod['imagen_url'] = url or default_url

    return productos


def asociar_imagenes_inventario(comercio_id):
    """Tras CSV: persiste URL definitiva en PostgreSQL (HEAD solo al escribir)."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, nombre, codigo_barras, descripcion, imagen_url
                FROM productos
                WHERE comercio_id = ?
                """,
                (int(comercio_id),),
            )
            productos = [dict(fila) for fila in cursor.fetchall()]
    except Exception as error:
        print(f'Error al leer productos para asociar imágenes: {error}')
        return 0

    if not productos:
        return 0

    aplicar_respaldo_imagenes(productos, persistir=True)
    return len(productos)
