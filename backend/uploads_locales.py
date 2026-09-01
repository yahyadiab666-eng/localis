"""
Respaldo local de fotos que el comerciante subió a mano.

Si Supabase Storage no está disponible (sin service_role, 403, red),
la foto comprimida se escribe en static/uploads/ y se persiste esa URL
para que el producto nazca con imagen visible. No se usa para el
catálogo automático (OpenFoodFacts).
"""

from __future__ import annotations

import re
from pathlib import Path

from config import RUTA_RAIZ

PREFIJO_PUBLICO = '/static/uploads/'
_CARPETA_RAIZ = Path(RUTA_RAIZ) / 'static' / 'uploads'
_NOMBRE_OK = re.compile(r'^[A-Za-z0-9._-]+$')
_EXTENSIONES = frozenset({'webp', 'jpg', 'jpeg', 'png'})
_LOG = '[Localis Upload local]'


def url_upload_local_valida(valor):
    """Solo /static/uploads/{carpeta}/{archivo} con nombre seguro."""
    from backend.utils import es_imagen_generica, texto_campo_imagen

    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto):
        return None
    texto = texto.replace('\\', '/')
    if not texto.startswith(PREFIJO_PUBLICO):
        return None
    if '..' in texto or texto.startswith('//'):
        return None
    resto = texto[len(PREFIJO_PUBLICO) :]
    partes = [p for p in resto.split('/') if p]
    if len(partes) != 2:
        return None
    carpeta, nombre = partes
    if not _NOMBRE_OK.match(carpeta) or not _NOMBRE_OK.match(nombre):
        return None
    if '.' not in nombre:
        return None
    ext = nombre.rsplit('.', 1)[-1].lower()
    if ext not in _EXTENSIONES:
        return None
    return texto


def _ruta_disco(url_publica):
    valida = url_upload_local_valida(url_publica)
    if not valida:
        return None
    relativo = valida[len('/static/') :].replace('\\', '/')
    destino = Path(RUTA_RAIZ) / 'static' / Path(*relativo.split('/'))
    try:
        destino.resolve().relative_to(_CARPETA_RAIZ.resolve())
    except (OSError, ValueError):
        return None
    return destino


def guardar_bytes_upload(data, filename, carpeta='productos'):
    """Escribe bytes ya comprimidos y retorna la URL pública local, o None."""
    if not data or not filename:
        return None
    carpeta_segura = re.sub(r'[^a-z0-9_-]', '', str(carpeta or 'productos').lower())
    carpeta_segura = carpeta_segura or 'productos'
    nombre = Path(str(filename)).name
    if not _NOMBRE_OK.match(nombre):
        return None
    if nombre.rsplit('.', 1)[-1].lower() not in _EXTENSIONES:
        return None
    destino_dir = _CARPETA_RAIZ / carpeta_segura
    try:
        destino_dir.mkdir(parents=True, exist_ok=True)
        ruta = destino_dir / nombre
        ruta.write_bytes(data)
    except OSError as error:
        print(f'{_LOG} no se pudo escribir {carpeta_segura}/{nombre}: {error}')
        return None
    url = f'{PREFIJO_PUBLICO}{carpeta_segura}/{nombre}'
    print(f'{_LOG} foto local {url} ({len(data)} bytes)')
    return url


def leer_bytes_upload(url_publica):
    """(bytes, filename, content_type) desde disco, o (None, None, None)."""
    ruta = _ruta_disco(url_publica)
    if ruta is None or not ruta.is_file():
        return None, None, None
    try:
        data = ruta.read_bytes()
    except OSError:
        return None, None, None
    if not data:
        return None, None, None
    nombre = ruta.name
    ext = nombre.rsplit('.', 1)[-1].lower()
    tipos = {
        'webp': 'image/webp',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
    }
    return data, nombre, tipos.get(ext, 'application/octet-stream')


def archivo_upload_existe(url_publica):
    ruta = _ruta_disco(url_publica)
    return bool(ruta and ruta.is_file() and ruta.stat().st_size > 0)
