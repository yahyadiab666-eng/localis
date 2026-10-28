"""Blueprint de autenticacion y cuenta (/login, /logout, /perfil).

Bloque 4c: login con Google, callback OAuth, perfil y cierre de sesion. Los
endpoints pasan a llamarse ``auth.<nombre>``; las referencias ``url_for`` se
actualizan en el mismo cambio. El proveedor OAuth vive en
``backend/oauth_google.py``.
"""

from __future__ import annotations
import logging
logger = logging.getLogger('localis.blueprints.auth')

import os
import traceback

from flask import (
    Blueprint,
    Response,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from backend.auth import obtener_o_crear_usuario_google, obtener_usuario_por_id
from backend.oauth_google import google_configurado, obtener_google
from backend.rate_limit import limite
from backend.session_comercio import (
    es_usuario_comerciante,
    limpiar_contexto_comercio,
    vincular_comercio_en_sesion,
)
from backend.stores import listar_comercios_por_usuario, obtener_comercio_por_usuario
from backend.web_seguridad import login_requerido

bp = Blueprint('auth', __name__)


@bp.route('/login')
def login():
    if session.get('usuario_id'):
        # Fase 2: admin -> /admin; comerciante con negocio -> /comercio;
        # cliente sin negocio -> catalogo publico (no al panel de comercio).
        if session.get('es_admin'):
            return redirect(url_for('admin.panel_admin'))
        if es_usuario_comerciante():
            return redirect(url_for('comercio.panel_comercio'))
        return redirect(url_for('index'))
    return render_template('login.html')


@bp.route('/registro')
def registro_legacy():
    """Ruta legacy: el registro de comercio pasa por login Google + /comercio/crear."""
    return redirect(url_for('auth.login'))


def _oauth_debug_activo():
    """Permite ver el traceback OAuth en la respuesta (LOCALIS_OAUTH_DEBUG=0 lo oculta)."""
    valor = str(os.getenv('LOCALIS_OAUTH_DEBUG', '1')).strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _oauth_error(etapa, error):
    """Registra y (por defecto) devuelve el traceback exacto en texto plano.

    Sustituye la pagina generica de Error 500 por el detalle real de Python para
    diagnosticar el callback de Google. Es aislado: solo se usa en estas rutas.
    """
    traza = traceback.format_exc()
    logger.error(f'[Localis OAuth] fallo en {etapa}: {type(error).__name__}: {error}')
    logger.info(traza)
    logging.getLogger('localis.oauth').error(
        'OAuth %s: %s', etapa, error, exc_info=True
    )
    if _oauth_debug_activo():
        try:
            from database import diagnosticar_postgresql

            diag_bd = diagnosticar_postgresql()
        except Exception as error_diag:
            diag_bd = {'error': f'{type(error_diag).__name__}: {error_diag}'}
        cuerpo = (
            f'Localis · Error 500 en autenticación Google\n'
            f'Etapa: {etapa}\n'
            f'Tipo: {type(error).__name__}\n'
            f'Mensaje: {error}\n\n'
            f'===== TRACEBACK COMPLETO =====\n{traza}\n'
            f'===== ESTADO DE BASE DE DATOS =====\n{diag_bd}\n'
            '===== FIN =====\n'
            'Para ocultar este detalle y volver a un mensaje amable: LOCALIS_OAUTH_DEBUG=0\n'
        )
        return Response(cuerpo, status=500, mimetype='text/plain; charset=utf-8')
    flash('No se pudo iniciar sesión con Google. Intenta de nuevo.', 'error')
    return redirect(url_for('auth.login'))


def _oauth_redirect_uri():
    """Redirect URI correcto detras del proxy de Render (https).

    Sin esto, ``url_for(_external=True)`` puede devolver ``http://`` detras del
    proxy TLS, y la cookie de sesion ``Secure`` (que guarda el ``state``) no se
    envia en el callback -> ``MismatchingStateError``. Aislado al flujo OAuth.
    """
    uri = url_for('auth.google_callback', _external=True)
    proto = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower()
    if proto == 'https' and uri.startswith('http://'):
        uri = 'https://' + uri[len('http://'):]
    return uri


@bp.route('/login/google')
@limite('30 per minute')
def login_google():
    google = obtener_google()
    if not google_configurado():
        logger.error('[Localis OAuth] GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET ausentes.')
        flash('Inicio de sesión con Google no está configurado.', 'error')
        return redirect(url_for('auth.login'))
    if google is None:
        logger.info('[Localis OAuth] cliente OAuth no inicializado.')
        flash('No se pudo inicializar el cliente OAuth de Google.', 'error')
        return redirect(url_for('auth.login'))
    try:
        redirect_uri = _oauth_redirect_uri()
        logger.info(f'[Localis OAuth] authorize_redirect uri={redirect_uri}')
        return google.authorize_redirect(redirect_uri)
    except Exception as error:
        return _oauth_error('authorize_redirect', error)


@bp.route('/login/google/callback')
def google_callback():
    google = obtener_google()
    if not google_configurado():
        logger.info('[Localis OAuth] callback sin credenciales configuradas.')
        flash('Inicio de sesión con Google no está configurado.', 'error')
        return redirect(url_for('auth.login'))
    if google is None:
        logger.info('[Localis OAuth] callback sin cliente OAuth.')
        flash('No se pudo inicializar el cliente OAuth de Google.', 'error')
        return redirect(url_for('auth.login'))

    # 1) Intercambio del codigo por token (errores de red/estado/token).
    try:
        token = google.authorize_access_token()
    except Exception as error:
        return _oauth_error('authorize_access_token', error)

    # 2) Perfil del usuario (con respaldo por si el token no trae ``userinfo``).
    try:
        google_info = token.get('userinfo') if token else None
        if not google_info:
            try:
                google_info = google.userinfo(token=token) if token else None
            except Exception as error_perfil:
                print(
                    f'[Localis OAuth] userinfo fallback fallo: '
                    f'{type(error_perfil).__name__}: {error_perfil}',
                    flush=True,
                )
                traceback.print_exc()
                google_info = None
        if not google_info:
            flash('No se recibió información de perfil desde Google.', 'error')
            return redirect(url_for('auth.login'))
    except Exception as error:
        return _oauth_error('userinfo', error)

    # 3) Alta/actualizacion del usuario y sesion.
    try:
        exito, usuario_o_error = obtener_o_crear_usuario_google(google_info)

        if not exito or not isinstance(usuario_o_error, dict) or not usuario_o_error.get('id'):
            # Se muestra el motivo real (p. ej. error de Supabase) en lugar de la
            # pagina generica: ayuda a diagnosticar la causa exacta.
            return _oauth_error('usuario', RuntimeError(str(usuario_o_error)))

        session['usuario_id'] = usuario_o_error['id']
        session['username'] = usuario_o_error.get('nombre') or 'Usuario'
        session['correo'] = usuario_o_error.get('correo') or ''
        session['foto_url'] = usuario_o_error.get('foto_url', '') or ''
        session['rol'] = usuario_o_error.get('rol', 'user')
        session['es_admin'] = usuario_o_error.get('rol') == 'admin'

        # --- FASE 1: persistencia real de la sesion -------------------------
        # Marca la sesion como permanente para que la cookie respete
        # PERMANENT_SESSION_LIFETIME (14 dias) y no muera al cerrar el navegador/app.
        session.permanent = True
        session.modified = True

        flash(f"¡Bienvenido, {session['username']}!", 'exito')

        # --- FASE 2: redireccion por capacidad ------------------------------
        # 1) Admin -> panel de administracion.
        if session['es_admin']:
            return redirect(url_for('admin.panel_admin'))

        # 2) Comerciante CON negocio registrado -> panel de comercio.
        comercio = None
        try:
            comercio = obtener_comercio_por_usuario(usuario_o_error['id'])
        except Exception as error_comercio:
            print(
                f'[Localis OAuth] comercio no resuelto: '
                f'{type(error_comercio).__name__}: {error_comercio}',
                flush=True,
            )
            traceback.print_exc()

        if comercio:
            # Al vincular el comercio se marca es_comerciante = True.
            vincular_comercio_en_sesion(comercio['id'])
            return redirect(url_for('comercio.panel_comercio'))

        # 3) Cliente SIN negocio -> catalogo publico (no forzar el alta de tienda).
        session['es_comerciante'] = False
        session.modified = True
        return redirect(url_for('index'))
    except Exception as error:
        return _oauth_error('usuario/sesion', error)


@bp.route('/perfil')
@login_requerido
def perfil():
    """Vista de cuenta del usuario autenticado.

    Separada por completo del panel de comercios: solo lee el perfil del
    usuario (sin contrasena) y, si es comerciante, sus tiendas vinculadas.
    """
    usuario_id = session.get('usuario_id')
    usuario = None
    try:
        usuario = obtener_usuario_por_id(usuario_id)
    except Exception as error:
        logger.error(f'[Localis] perfil usuario fallo: {type(error).__name__}: {error}')
        traceback.print_exc()

    if not usuario:
        # La sesion apunta a un usuario que ya no existe: se limpia y se
        # redirige al login (mismo criterio que ``login_requerido``).
        session.clear()
        flash('Tu sesión expiró. Inicia sesión nuevamente.', 'error')
        return redirect(url_for('auth.login'))

    soy_comerciante = False
    comercios = []
    try:
        soy_comerciante = es_usuario_comerciante(usuario_id)
        if soy_comerciante:
            comercios = listar_comercios_por_usuario(usuario_id) or []
    except Exception as error:
        logger.error(f'[Localis] perfil comercios fallo: {type(error).__name__}: {error}')
        traceback.print_exc()
        comercios = []

    # Oferta del banner B2B (precios/medidas): la puede alquilar cualquier
    # usuario, tenga o no una tienda. Solo constantes: no toca la BD.
    try:
        from backend.banners_b2b import opciones_banner

        banner_oferta = opciones_banner()
    except Exception:
        banner_oferta = {}

    return render_template(
        'perfil.html',
        usuario=usuario,
        comercios=comercios,
        banner_oferta=banner_oferta,
        es_admin=bool(session.get('es_admin')),
        es_comerciante=soy_comerciante,
        has_store=bool(soy_comerciante),
        rol=(
            'admin'
            if session.get('es_admin')
            else ('comerciante' if soy_comerciante else (usuario or {}).get('rol') or 'user')
        ),
    )


@bp.route('/logout', methods=['GET', 'POST'])
def logout():
    """Cierra la sesion del usuario.

    La interfaz usa POST + token CSRF (todas las barras envian un formulario),
    que es el flujo seguro y con confirmacion. Se acepta tambien GET para no
    romper enlaces/llamadas heredados ni devolver un 405 inesperado a un
    usuario con sesion activa. No toca ninguna ruta de pagos ni de comercio.
    """
    limpiar_contexto_comercio()
    session.clear()
    flash('Sesión cerrada correctamente.', 'info')
    return redirect(url_for('index'))
