"""Helpers de las vistas de comercio (panel del comerciante).

Extraidos de ``main.py`` (Bloque 4) para reutilizarlos desde el blueprint de
comercio y desde ``main.py``. Son defensivos: ante un fallo de BD o de datos
secundarios devuelven un valor por defecto en lugar de propagar el error.
"""

from __future__ import annotations
import logging
logger = logging.getLogger('localis.vistas_comercio')

import sqlite3
import traceback

from flask import flash, redirect, session, url_for

from config import pagos_habilitados
from backend.db import get_db_connection
from backend.plans import obtener_beneficios_plan
from backend.session_comercio import vincular_comercio_en_sesion
from backend.stores import (
    obtener_comercio_por_usuario,
    obtener_productos_comercio,
    obtener_tasa_dolar,
)
from backend.subscriptions import (
    calcular_cotizacion_cambio_plan,
    comercio_puede_gestionar_inventario,
)
from backend.utils import url_maps_comercio
from backend.vistas_imagenes import (
    normalizar_imagenes_comercio,
    procesar_imagen_subida,
)


def procesar_logo_comercio(file_storage, prefijo):
    """Logo de comercio -> bucket Supabase comercios/ (hibrido)."""
    return procesar_imagen_subida(
        file_storage,
        prefijo=prefijo,
        carpeta='comercios',
    )


def productos_desde_filas(productos_db, tasa_actual):
    productos = []
    for p in productos_db:
        try:
            precio_usd = float(p['precio_usd'] or 0)
        except (TypeError, ValueError):
            precio_usd = 0.0
        productos.append({
            'id': p.get('id'),
            'nombre': p.get('nombre') or '',
            'descripcion': (p.get('descripcion') or '').strip(),
            'precio_usd': precio_usd,
            'precio_bs': round(precio_usd * tasa_actual, 2),
            'codigo_barras': '' if p.get('codigo_barras') is None else p.get('codigo_barras'),
            'imagen_url': p.get('imagen_url') or '',
            'imagen_estado': (p.get('imagen_estado') or 'pendiente'),
        })
    # Categoria runtime para el panel (chips / filtrado), sin tocar la BD.
    try:
        from backend.categorias_producto import anotar_categorias_productos
        anotar_categorias_productos(productos)
    except Exception:
        pass
    return productos


def planes_beneficios_para_comercio(comercio, tasa_actual):
    planes_beneficios = {}
    for codigo in ('basica', 'pro', 'business'):
        info = obtener_beneficios_plan(codigo)
        cotizacion = calcular_cotizacion_cambio_plan(comercio, codigo, tasa=tasa_actual)
        if cotizacion:
            info['monto_bs'] = cotizacion['monto_bs']
            info['monto_usd'] = cotizacion['monto_usd']
            info['tasa'] = cotizacion['tasa']
            info['tipo_cambio'] = cotizacion['tipo_cambio']
            info['requiere_pago'] = cotizacion['requiere_pago']
            info['mensaje_cambio'] = cotizacion.get('mensaje')
        planes_beneficios[codigo] = info
    return planes_beneficios


def cargar_datos_comercio_usuario(usuario_id):
    """Carga comercio y productos del usuario autenticado (defensivo).

    Si la consulta principal (con JOINs) falla, reintenta un SELECT simple de
    ``comercios`` para que el panel siempre pueda renderizar (nunca 500/503).
    """
    try:
        tasa_actual = obtener_tasa_dolar() or 1.0
    except Exception:
        tasa_actual = 1.0

    comercio_raw = None
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT c.*, cat.nombre as categoria
                FROM comercios c
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                WHERE c.usuario_id = ?
                ''',
                (usuario_id,),
            )
            comercio_db = cursor.fetchone()
            if comercio_db:
                comercio_raw = dict(comercio_db)
    except Exception:
        # Se registra el rastro real (antes se silenciaba -> 503 opaco).
        logger.error('[Localis] panel_comercio consulta principal fallo:')
        traceback.print_exc()
        try:
            with get_db_connection(row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT * FROM comercios WHERE usuario_id = ? ORDER BY id ASC LIMIT 1',
                    (usuario_id,),
                )
                fila = cursor.fetchone()
                comercio_raw = dict(fila) if fila else None
        except Exception:
            traceback.print_exc()
            comercio_raw = None

    if not comercio_raw:
        try:
            with get_db_connection(row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id, nombre FROM categorias')
                categorias = [dict(c) for c in cursor.fetchall()]
        except Exception:
            traceback.print_exc()
            categorias = []
        return None, None, tasa_actual, categorias

    comercio_id = comercio_raw['id']
    try:
        vincular_comercio_en_sesion(comercio_id)
    except Exception:
        pass

    try:
        productos_db = obtener_productos_comercio(comercio_id)
    except Exception:
        traceback.print_exc()
        productos_db = []

    comercio = normalizar_imagenes_comercio(comercio_raw)
    try:
        comercio['maps_link'] = url_maps_comercio(comercio)
    except Exception:
        comercio['maps_link'] = None
    productos = productos_desde_filas(productos_db, tasa_actual)
    return comercio, productos, tasa_actual, None


def requiere_comercio():
    """Resuelve el comercio del usuario autenticado (primer comercio asociado)."""
    usuario_id = session.get('usuario_id')
    comercio = obtener_comercio_por_usuario(usuario_id)
    if not comercio:
        flash('Debes registrar un comercio primero.', 'error')
        return None, redirect(url_for('comercio.panel_comercio'))
    vincular_comercio_en_sesion(comercio['id'])
    return comercio, None


def bloquear_gestion_inventario(comercio, redirect_url='comercio.panel_comercio'):
    # --- RESTAURAR_PAGOS_INICIO ---
    # En la fase gratuita el inventario nunca se bloquea por plan/suscripcion.
    if not pagos_habilitados():
        return None
    # --- RESTAURAR_PAGOS_FIN ---
    ok, mensaje = comercio_puede_gestionar_inventario(comercio['id'])
    if ok:
        return None
    flash(mensaje, 'error')
    return redirect(url_for('comercio.comercio_planes', abrir_pago='pro'))
