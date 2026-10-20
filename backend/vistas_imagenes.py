"""Helpers de imagen usados por las vistas (sync a Storage y registro manual).

Extraidos de ``main.py`` (Bloque 4) para reutilizarlos desde las rutas de
``main`` y desde los blueprints. Nunca lanzan.
"""

from __future__ import annotations

from backend.imagenes_producto import eliminar_manual, marcar_manual
from backend.image_lookup import programar_descubrimiento_producto
from backend.supabase_storage import (
    intentar_subir_imagen,
    programar_sincronizacion_storage,
)


def sincronizar_foto_local_si_aplica(producto_id, imagen_url, carpeta='productos'):
    """Si la foto quedo en disco, intenta copiarla al bucket sin bloquear."""
    if not producto_id:
        return
    if imagen_url:
        try:
            programar_sincronizacion_storage(producto_id, imagen_url, carpeta=carpeta)
        except Exception as error:
            print(f'[Localis Imagen] sync diferida no programada: {error}')
        return
    try:
        programar_descubrimiento_producto(producto_id)
    except Exception as error:
        print(f'[Localis Imagen] descubrimiento no programado: {error}')


def registrar_imagen_manual(producto_id, imagen_url, fuente='manual'):
    """Registra la subida manual y borra la manual anterior del producto."""
    if not producto_id or not imagen_url:
        return
    try:
        marcar_manual(producto_id, imagen_url, fuente)
    except Exception as error:
        print(f'[Localis Imagen] no se registró la imagen manual: {error}')


def quitar_imagen_manual(producto_id):
    """Descarta la manual (la borra) y revierte a la imagen automatica."""
    if not producto_id:
        return
    try:
        eliminar_manual(producto_id)
    except Exception as error:
        print(f'[Localis Imagen] no se pudo quitar la imagen manual: {error}')


def procesar_imagen_subida(
    file_storage, prefijo, carpeta='comercios', max_dimension=800
):
    """Intenta Storage; si falla no lanza. Retorna (url | None, aviso | None)."""
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None, None
    return intentar_subir_imagen(
        file_storage,
        prefijo=prefijo,
        carpeta=carpeta,
        max_dimension=max_dimension,
    )
