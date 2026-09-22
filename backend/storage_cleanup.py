"""Limpieza de assets en Supabase Storage (borrado seguro de huérfanos).

Se invoca **automáticamente** cuando un comercio o un producto reemplaza o
elimina su imagen manual: el archivo anterior se purga del bucket (y también su
copia local en ``/static/uploads`` si existía), evitando Storage bloat.

Además ofrece una pasada de mantenimiento (``limpiar_huerfanos`` y CLI) que
borra objetos que ya no referencia ninguna fila.

Seguridad (nunca borra recursos compartidos):
  - SOLO son eliminables los assets *manuales* por prefijo de archivo:
      * ``productos/manual_*``  (foto subida por el comerciante)
      * ``comercios/logo_*``    (logo de tienda)
      * ``banners/banner_*``    (banner principal del admin)
  - Se excluyen placeholders, monogramas/tarjetas de marca (``marcas/``,
    ``genericos/``) e imágenes del catálogo maestro (``auto_*``), que se
    comparten entre varios productos.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import unquote

_LOG = '[Localis Limpieza]'

_MARCA_PUBLICA_STORAGE = '/storage/v1/object/public/'
_PREFIJO_LOCAL = '/static/uploads/'

# carpeta del bucket -> prefijos de archivo eliminables (vacío = compartida).
_PREFIJOS_ELIMINABLES = {
    'productos': ('manual_',),
    'comercios': ('logo_',),
    'banners': ('banner_',),
}
_CARPETAS_PROTEGIDAS = ('marcas', 'genericos')


def _normalizar_carpeta(carpeta):
    return str(carpeta or '').strip('/').lower()


def _es_eliminable(carpeta, filename):
    """True si el asset es manual y por tanto puede purgarse con seguridad."""
    carpeta = _normalizar_carpeta(carpeta)
    if carpeta in _CARPETAS_PROTEGIDAS:
        return False
    prefijos = _PREFIJOS_ELIMINABLES.get(carpeta)
    if not prefijos:
        return False
    nombre = str(filename or '').strip().lower()
    return bool(nombre) and any(nombre.startswith(p) for p in prefijos)


def ruta_storage_desde_url(url):
    """(carpeta, filename) de una URL local o pública de Storage, o None."""
    texto = str(url or '').strip()
    if not texto:
        return None

    if texto.startswith(_PREFIJO_LOCAL):
        partes = [
            unquote(p)
            for p in texto[len(_PREFIJO_LOCAL) :].split('?', 1)[0].split('/')
            if p
        ]
        if len(partes) >= 2:
            return partes[-2], partes[-1]
        return None

    indice = texto.find(_MARCA_PUBLICA_STORAGE)
    if indice >= 0:
        resto = texto[indice + len(_MARCA_PUBLICA_STORAGE) :].split('?', 1)[0]
        partes = [unquote(p) for p in resto.split('/') if p]
        if len(partes) >= 3:  # bucket/carpeta/archivo
            return partes[-2], partes[-1]
        if len(partes) == 2:  # carpeta/archivo
            return partes[0], partes[1]
    return None


def _eliminar_local(carpeta, filename):
    """Borra la copia local en /static/uploads si existe. Nunca lanza."""
    try:
        from config import RUTA_RAIZ
        from backend.uploads_locales import url_upload_local_valida

        url = f'{_PREFIJO_LOCAL}{carpeta}/{filename}'
        if not url_upload_local_valida(url):
            return False
        destino = Path(RUTA_RAIZ) / 'static' / 'uploads' / carpeta / filename
        try:
            destino.resolve().relative_to((Path(RUTA_RAIZ) / 'static' / 'uploads').resolve())
        except (OSError, ValueError):
            return False
        if destino.is_file():
            destino.unlink()
            print(f'{_LOG} local borrado: {carpeta}/{filename}')
            return True
    except Exception as error:
        print(f'{_LOG} local no borrado {carpeta}/{filename}: {type(error).__name__}: {error}')
    return False


def _eliminar_storage_http(rutas, bucket):
    import httpx

    from backend.supabase_client import (
        SUPABASE_URL,
        headers_storage_service_role,
    )
    from backend.supabase_storage import _url_objeto_storage

    if not SUPABASE_URL:
        return 0
    try:
        headers = headers_storage_service_role()
    except RuntimeError as error:
        print(f'{_LOG} sin service_role: {error}')
        return 0

    eliminados = 0
    with httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0)) as http:
        for ruta in rutas:
            try:
                respuesta = http.request(
                    'DELETE', _url_objeto_storage(ruta), headers=headers
                )
                if respuesta.status_code < 400:
                    eliminados += 1
            except Exception as error:
                print(f'{_LOG} DELETE {ruta} fallo: {type(error).__name__}: {error}')
    return eliminados


def eliminar_objetos_storage(rutas):
    """Purga objetos del bucket (SDK ``storage.from_(bucket).remove`` + respaldo)."""
    rutas = [str(r).strip('/') for r in (rutas or []) if str(r).strip('/')]
    if not rutas:
        return 0
    try:
        from backend.supabase_client import (
            SUPABASE_BUCKET_IMAGENES,
            obtener_cliente_storage,
        )

        cliente = obtener_cliente_storage()
        if cliente is not None:
            try:
                cliente.storage.from_(SUPABASE_BUCKET_IMAGENES).remove(rutas)
                print(f'{_LOG} Storage borrado (SDK): {len(rutas)} objeto(s)')
                return len(rutas)
            except Exception as error:
                print(
                    f'{_LOG} SDK remove fallo ({type(error).__name__}); uso HTTP directo'
                )
        return _eliminar_storage_http(rutas, SUPABASE_BUCKET_IMAGENES)
    except Exception as error:
        print(f'{_LOG} no se pudo purgar en Storage: {type(error).__name__}: {error}')
        return 0


def eliminar_asset(url):
    """Purga un asset manual (Storage + copia local). Nunca lanza.

    Devuelve True si era eliminable (aunque el archivo ya no existiera).
    """
    dato = ruta_storage_desde_url(url)
    if not dato:
        return False
    carpeta, filename = dato
    if not _es_eliminable(carpeta, filename):
        return False
    ruta = f'{carpeta}/{filename}'
    borrado_local = _eliminar_local(carpeta, filename)
    borrado_storage = eliminar_objetos_storage([ruta]) > 0
    if borrado_local or borrado_storage:
        print(f'{_LOG} asset purgado: {ruta}')
    return True


def limpiar_asset_anterior(anterior, nuevo=None):
    """Borra el asset anterior si es distinto del nuevo (reemplazo)."""
    anterior = str(anterior or '').strip()
    nuevo = str(nuevo or '').strip()
    if not anterior or anterior == nuevo:
        return False
    return eliminar_asset(anterior)


def _eliminar_url_generada(url):
    """Purga un asset fabricado concreto (monograma/tarjeta) de Storage o local.

    Solo actúa sobre carpetas generadas (``marcas/``, ``genericos/``); nunca
    borra los placeholders estáticos del repositorio.
    """
    texto = str(url or '').strip()
    dato = ruta_storage_desde_url(texto)
    if not dato:
        return False
    carpeta, filename = dato
    if carpeta not in ('marcas', 'genericos'):
        return False
    ruta = f'{carpeta}/{filename}'
    borrado = _eliminar_local(carpeta, filename)
    if texto.startswith('http'):
        borrado = eliminar_objetos_storage([ruta]) > 0 or borrado
    return borrado


def eliminar_assets_generados(limite=5000):
    """Elimina assets **fabricados** de la BD y de Storage (anti-invención).

    Vacía ``imagen_url`` de los productos cuyo asset sea un placeholder de
    categoría, un monograma o una tarjeta, y purga esos archivos generados.
    Devuelve ``{'revisados', 'limpiados', 'purgados'}``.
    """
    from backend.activos_verificados import es_asset_generado
    from backend.db import get_db_connection

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "SELECT id, imagen_url, imagen_fuente FROM productos "
                "WHERE imagen_url IS NOT NULL"
            )
            filas = [
                list(r.values()) if isinstance(r, dict) else list(r)
                for r in cursor.fetchall()
            ]
    except Exception as error:
        print(f'{_LOG} no se pudo auditar generados: {type(error).__name__}: {error}')
        return {'revisados': 0, 'limpiados': 0, 'purgados': 0}

    generados = [
        (int(pid), url, fuente)
        for pid, url, fuente in filas
        if es_asset_generado(url, fuente)
    ]
    if not generados:
        return {'revisados': len(filas), 'limpiados': 0, 'purgados': 0}

    limpiados = 0
    urls = set()
    for pid, url, _fuente in generados[: int(limite)]:
        try:
            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    """
                    UPDATE productos
                    SET imagen_url = NULL, imagen_fuente = NULL,
                        imagen_estado = 'pendiente'
                    WHERE id = ?
                    """,
                    (pid,),
                )
                conexion.commit()
                limpiados += cursor.rowcount
            if url:
                urls.add(str(url))
        except Exception as error:
            print(f'{_LOG} no se pudo limpiar producto={pid}: {type(error).__name__}')

    purgados = 0
    for url in urls:
        try:
            if _eliminar_url_generada(url):
                purgados += 1
        except Exception as error:
            print(f'{_LOG} no se pudo purgar {url[:80]}: {type(error).__name__}')

    if limpiados:
        print(f'{_LOG} assets fabricados eliminados: {limpiados} (archivos {purgados})')
    return {'revisados': len(filas), 'limpiados': limpiados, 'purgados': purgados}


def _referencias_bd():
    """Conjunto ``carpeta/filename`` referenciado por la BD (nunca borrar)."""
    from backend.db import get_db_connection

    refs = set()

    def _agregar(valor):
        dato = ruta_storage_desde_url(valor)
        if dato:
            refs.add(f'{dato[0]}/{dato[1]}')

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute('SELECT imagen_url FROM productos')
        for fila in cursor.fetchall():
            valor = fila[0] if not isinstance(fila, dict) else fila.get('imagen_url')
            _agregar(valor)
        cursor.execute('SELECT * FROM comercios')
        for fila in cursor.fetchall():
            registro = dict(fila) if not isinstance(fila, dict) else fila
            for campo in ('logo_url', 'banner_url', 'imagen_portada'):
                _agregar(registro.get(campo))
        cursor.execute("SELECT valor FROM configuracion_sistema WHERE clave = 'banner_principal'")
        fila = cursor.fetchone()
        if fila:
            _agregar(fila[0] if not isinstance(fila, dict) else fila.get('valor'))
    return refs


def _listar_storage(carpeta):
    from backend.supabase_client import (
        SUPABASE_BUCKET_IMAGENES,
        obtener_cliente_storage,
    )

    cliente = obtener_cliente_storage()
    if cliente is None:
        return []
    try:
        objetos = cliente.storage.from_(SUPABASE_BUCKET_IMAGENES).list(carpeta)
    except Exception as error:
        print(f'{_LOG} listado {carpeta} fallo: {type(error).__name__}: {error}')
        return []
    nombres = []
    for objeto in objetos or []:
        nombre = objeto.get('name') if isinstance(objeto, dict) else None
        if nombre:
            nombres.append(nombre)
    return nombres


def limpiar_huerfanos(max_por_carpeta=400):
    """Mantenimiento: borra assets manuales que ya no referencia la BD.

    Solo considera ``productos/manual_*``, ``comercios/logo_*`` y
    ``banners/banner_*``. Devuelve ``{'revisados', 'borrados'}``.
    """
    referencias = _referencias_bd()
    borrados = 0
    revisados = 0
    for carpeta in ('productos', 'comercios', 'banners'):
        for nombre in _listar_storage(carpeta)[:max_por_carpeta]:
            if not _es_eliminable(carpeta, nombre):
                continue
            revisados += 1
            clave = f'{carpeta}/{nombre}'
            if clave in referencias:
                continue
            borrado_storage = eliminar_objetos_storage([clave]) > 0
            borrado_local = _eliminar_local(carpeta, nombre)
            if borrado_storage or borrado_local:
                borrados += 1

    # Copias locales huérfanas (mismo criterio de prefijo/referencia).
    try:
        from config import RUTA_RAIZ

        raiz_local = Path(RUTA_RAIZ) / 'static' / 'uploads'
        for carpeta in ('productos', 'comercios'):
            directorio = raiz_local / carpeta
            if not directorio.is_dir():
                continue
            for archivo in list(directorio.iterdir())[:max_por_carpeta]:
                if not archivo.is_file() or not _es_eliminable(carpeta, archivo.name):
                    continue
                revisados += 1
                clave = f'{carpeta}/{archivo.name}'
                if clave in referencias:
                    continue
                try:
                    archivo.unlink()
                    borrados += 1
                    print(f'{_LOG} local huérfano borrado: {clave}')
                except OSError as error:
                    print(f'{_LOG} local huérfano no borrado {clave}: {error}')
    except Exception as error:
        print(f'{_LOG} barrido local fallo: {type(error).__name__}: {error}')

    return {'revisados': revisados, 'borrados': borrados}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from dotenv import load_dotenv

        from config import RUTA_RAIZ

        load_dotenv(Path(RUTA_RAIZ) / '.env', override=False)
    except Exception:
        pass
    resultado = limpiar_huerfanos()
    generados = eliminar_assets_generados()
    print(
        f"{_LOG} mantenimiento: revisados={resultado['revisados']} "
        f"huerfanos_borrados={resultado['borrados']} "
        f"fabricados_limpiados={generados['limpiados']}"
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
