"""Blueprint del panel de administracion (/admin/*).

Lote 1 del Bloque 4c: panel principal y listado de usuarios. Los endpoints
pasan a llamarse ``admin.panel_admin`` / ``admin.admin_usuarios``; las
referencias ``url_for`` se actualizan en el mismo cambio.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from config import DEFAULT_BANNER_URL, WHATSAPP_SOPORTE, WHATSAPP_SOPORTE_URL
from backend.admin import (
    obtener_bandeja_tecnica,
    obtener_banner_principal,
    obtener_categorias_admin,
    obtener_todos_comercios_admin,
    obtener_usuarios_admin,
)
from backend.plans import PLANES
from backend.stores import obtener_config, obtener_tasa_dolar
from backend.web_seguridad import admin_requerido
from utils.images import url_hero_inicio

bp = Blueprint('admin', __name__, url_prefix='/admin')


@bp.route('')
@admin_requerido
def panel_admin():
    estado_filtro = request.args.get('estado_ticket', None)
    busqueda = request.args.get('q', '').strip()
    tickets = obtener_bandeja_tecnica(estado_filtro=estado_filtro)
    tasa_actual = obtener_tasa_dolar()
    comercios = obtener_todos_comercios_admin(busqueda=busqueda or None)
    banner_url = url_hero_inicio(obtener_banner_principal()) or DEFAULT_BANNER_URL
    whatsapp = obtener_config('whatsapp_soporte', WHATSAPP_SOPORTE)

    return render_template(
        'admin.html',
        tasa=tasa_actual,
        comercios=comercios,
        tickets=tickets,
        banner_url=banner_url,
        whatsapp=whatsapp,
        whatsapp_url=WHATSAPP_SOPORTE_URL,
        planes=PLANES,
        categorias=obtener_categorias_admin(),
        q=busqueda,
    )


@bp.route('/usuarios')
@admin_requerido
def admin_usuarios():
    """Listado de todos los usuarios registrados (auditoria de perfiles)."""
    busqueda = request.args.get('q', '').strip()
    usuarios = obtener_usuarios_admin(busqueda=busqueda or None)
    return render_template(
        'admin_usuarios.html',
        usuarios=usuarios,
        q=busqueda,
    )
