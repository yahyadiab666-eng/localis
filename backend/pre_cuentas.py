"""Pre-cuentas (tiendas sin dueño), transferencia y reasignación de propiedad.

Flujo pensado para el panel admin:

1. **Pre-cuenta** (``crear_pre_cuenta_admin``): se crea una tienda **sin correo**
   ni dueño (``usuario_id NULL``, ``owner_email NULL``, ``estado_tienda =
   'pre_cuenta'``, oculta). Sirve para armar el comercio y su catálogo antes de
   que el comerciante se registre.

2. **Transferencia** (``transferir_propiedad_comercio``): convierte una
   pre-cuenta en **cuenta normal**, exactamente como si un usuario hubiese
   creado la tienda desde cero: se le asigna el **plan gratis predeterminado**,
   la prueba del plan (duración por defecto, sin días extra), se publica y se
   genera su slug. Aquí se agrega el **correo del dueño**.

3. **Reasignación** (``reasignar_propietario_comercio``): cambia el dueño de una
   tienda **ya normal** sin tocar plan, fechas ni visibilidad.

En transferencia y reasignación, si el correo indicado todavía **no tiene
usuario registrado**, se guarda en ``comercios.owner_email`` con
``usuario_id NULL``. Cuando esa persona inicie sesión por primera vez con ese
Gmail, ``reclamar_comercios_por_email`` (invocada en el login) enlaza la tienda
automáticamente.

Todo es aditivo y defensivo: no altera PKs, relaciones ni ``estado_pago`` de
otros comercios. Nunca lanza: devuelve tuplas/valores seguros.
"""
import logging
logger = logging.getLogger('localis.pre_cuentas')

import base64
import hashlib
import hmac
import os
import re

from backend.db import get_db_connection
from backend.plans import PLAN_GRATIS_CODIGO, obtener_plan_por_codigo

LOG_PREFIX = '[Localis PreCuentas]'

_PATRON_CORREO = re.compile(r'^[\w\.-]+@[\w\.-]+\.\w+$')


def correo_valido(correo):
    """Validación de correo (misma regla que el panel admin)."""
    return bool(correo) and _PATRON_CORREO.match(str(correo).strip()) is not None


def _valor_fila(fila, clave, indice=0):
    if fila is None:
        return None
    if isinstance(fila, dict):
        return fila.get(clave)
    return fila[indice]


def _auditar(cursor, admin_id, accion, detalles):
    try:
        cursor.execute(
            """
            INSERT INTO logs_auditoria (usuario_id, accion, detalles)
            VALUES (?, ?, ?)
            """,
            (admin_id, accion, detalles),
        )
    except Exception as error:
        logger.error(f'{LOG_PREFIX} aviso auditoría: {error}')


def _plan_gratis():
    """Datos del plan gratis predeterminado (id, límite y duración en días)."""
    plan = obtener_plan_por_codigo(PLAN_GRATIS_CODIGO) or {}
    return {
        'id': plan.get('id'),
        'limite': plan.get('limite_productos') or 50,
        'dias': plan.get('dias_duracion') or 30,
        'codigo': PLAN_GRATIS_CODIGO,
    }


def _buscar_usuario_por_correo(cursor, correo):
    """Id del usuario con ese correo (case-insensitive) o ``None``."""
    cursor.execute(
        'SELECT id FROM usuarios '
        'WHERE LOWER(TRIM(correo)) = LOWER(TRIM(?)) '
        'ORDER BY id ASC LIMIT 1',
        (correo,),
    )
    return _valor_fila(cursor.fetchone(), 'id', 0)


def _ascender_a_comerciante(cursor, usuario_id):
    if not usuario_id:
        return
    try:
        cursor.execute(
            "UPDATE usuarios SET rol = 'comerciante' "
            "WHERE id = ? AND LOWER(TRIM(COALESCE(rol, ''))) <> 'admin'",
            (int(usuario_id),),
        )
    except Exception as error:
        logger.error(f'{LOG_PREFIX} rol usuario {usuario_id}: {error}')


def _usuario_tiene_tienda(cursor, usuario_id, excluir_comercio_id=None):
    """True si el usuario ya posee alguna tienda (regla: 1 tienda por usuario)."""
    if not usuario_id:
        return False
    try:
        if excluir_comercio_id:
            cursor.execute(
                'SELECT 1 FROM comercios WHERE usuario_id = ? AND id <> ? LIMIT 1',
                (int(usuario_id), int(excluir_comercio_id)),
            )
        else:
            cursor.execute(
                'SELECT 1 FROM comercios WHERE usuario_id = ? LIMIT 1',
                (int(usuario_id),),
            )
        return cursor.fetchone() is not None
    except Exception as error:
        logger.error(f'{LOG_PREFIX} no se pudo verificar tiendas de {usuario_id}: {error}')
        return False


def _recalcular_rol_usuario(cursor, usuario_id):
    """Alinea ``usuarios.rol`` con la realidad (comerciante si tiene tienda).

    No degrada ni toca al admin. Se usa al reasignar para no dejar un rol
    ``comerciante`` obsoleto en quien perdió su única tienda.
    """
    if not usuario_id:
        return
    try:
        tiene = _usuario_tiene_tienda(cursor, usuario_id)
        nuevo = 'comerciante' if tiene else 'user'
        cursor.execute(
            "UPDATE usuarios SET rol = ? "
            "WHERE id = ? AND LOWER(TRIM(COALESCE(rol, ''))) <> 'admin'",
            (nuevo, int(usuario_id)),
        )
    except Exception as error:
        logger.error(f'{LOG_PREFIX} recálculo rol {usuario_id}: {error}')


def _asegurar_slug(comercio_id, nombre=None):
    """Genera el slug público si falta (idempotente; nunca lanza)."""
    try:
        from backend.stores import asegurar_slug_comercio

        asegurar_slug_comercio(comercio_id, nombre)
    except Exception as error:
        logger.error(f'{LOG_PREFIX} slug comercio {comercio_id}: {error}')


def crear_pre_cuenta_admin(admin_id, datos):
    """Crea un comercio en estado ``pre_cuenta`` (**sin correo ni dueño**).

    ``datos`` admite: nombre (obligatorio), descripcion, telefono, direccion,
    ciudad, zona, categoria_id. El correo del dueño se agrega después, al
    transferir la propiedad.

    Retorna ``(exito, mensaje, comercio_id)``. Nunca lanza.
    """
    datos = datos or {}
    nombre = (datos.get('nombre') or '').strip()
    if not nombre:
        return False, 'El nombre del comercio es obligatorio.', None

    plan = _plan_gratis()
    plan_id = plan['id']
    if not plan_id:
        return False, 'El plan gratuito no está configurado en el sistema.', None

    categoria_id = datos.get('categoria_id')

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            if categoria_id not in (None, '', 0, '0'):
                cursor.execute(
                    'SELECT 1 FROM categorias WHERE id = ?', (int(categoria_id),)
                )
                if not cursor.fetchone():
                    return False, 'La categoría seleccionada no es válida.', None
                categoria_id = int(categoria_id)
            else:
                categoria_id = None

            cursor.execute(
                """
                INSERT INTO comercios (
                    usuario_id, nombre, descripcion, telefono, direccion,
                    ciudad, zona, categoria_id, plan_id, plan_tipo,
                    limite_productos, estado_pago, estado_tienda, owner_email,
                    visible
                )
                VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'gratis',
                        'pre_cuenta', NULL, 0)
                RETURNING id
                """,
                (
                    nombre,
                    datos.get('descripcion'),
                    datos.get('telefono'),
                    datos.get('direccion'),
                    datos.get('ciudad'),
                    datos.get('zona'),
                    categoria_id,
                    plan_id,
                    plan['codigo'],
                    plan['limite'],
                ),
            )
            comercio_id = _valor_fila(cursor.fetchone(), 'id', 0)
            if not comercio_id:
                conexion.rollback()
                return False, 'No se pudo crear la pre-cuenta.', None

            try:
                from backend.codigos_publicos import asignar_codigo_comercio

                asignar_codigo_comercio(cursor, comercio_id)
            except Exception as error:
                logger.error(f'{LOG_PREFIX} código comercio {comercio_id}: {error}')

            _auditar(
                cursor,
                admin_id,
                'Alta de pre-cuenta (admin)',
                f'Comercio {comercio_id} pre-creado (sin dueño). '
                'El correo se asigna al transferir la propiedad.',
            )
            conexion.commit()

        _asegurar_slug(comercio_id, nombre)
        return (
            True,
            'Pre-cuenta creada. Al transferir la propiedad se agrega el correo '
            'del dueño y arranca el plan gratis.',
            comercio_id,
        )
    except Exception as error:
        return False, f'Error al crear la pre-cuenta: {error}', None


def _estado_actual_comercio(cursor, comercio_id):
    cursor.execute(
        """
        SELECT id, nombre,
               COALESCE(estado_tienda, 'activa') AS estado_tienda,
               usuario_id
        FROM comercios WHERE id = ?
        """,
        (int(comercio_id),),
    )
    fila = cursor.fetchone()
    if not fila:
        return None
    return {
        'id': _valor_fila(fila, 'id', 0),
        'nombre': _valor_fila(fila, 'nombre', 1),
        'estado_tienda': _valor_fila(fila, 'estado_tienda', 2),
        'usuario_id': _valor_fila(fila, 'usuario_id', 3),
    }


def transferir_propiedad_comercio(admin_id, comercio_id, nuevo_email):
    """Convierte una pre-cuenta en cuenta normal y le asigna el dueño.

    Inicializa la tienda **como si se hubiese creado desde cero**: plan gratis
    predeterminado, prueba con la duración del plan (sin días extra), publicada
    y con slug. El correo del dueño se guarda; si aún no tiene usuario
    registrado, queda reclamable y se enlaza en su primer login.

    Solo aplica a pre-cuentas o tiendas sin dueño (para no reiniciar el plan de
    un comercio ya activo). Para cambiar el dueño de una tienda activa usa
    ``reasignar_propietario_comercio``.

    Retorna ``(exito, mensaje, vinculado)``. Nunca lanza.
    """
    nuevo_email = (nuevo_email or '').strip().lower()
    if not correo_valido(nuevo_email):
        return False, 'El correo del dueño no es válido.', False

    plan = _plan_gratis()
    if not plan['id']:
        return False, 'El plan gratuito no está configurado en el sistema.', False

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            actual = _estado_actual_comercio(cursor, comercio_id)
            if not actual:
                return False, 'Comercio no encontrado.', False

            es_pre_cuenta = str(actual['estado_tienda'] or '').lower() == 'pre_cuenta'
            sin_dueno = actual['usuario_id'] in (None, '')
            if not es_pre_cuenta and not sin_dueno:
                return (
                    False,
                    'Esta tienda ya es un comercio activo con dueño. Usa '
                    '"Reasignar dueño" para cambiar la propiedad.',
                    False,
                )

            usuario_id = _buscar_usuario_por_correo(cursor, nuevo_email)
            if _usuario_tiene_tienda(cursor, usuario_id, excluir_comercio_id=comercio_id):
                return (
                    False,
                    'Ese correo ya tiene una tienda asignada. Un usuario solo '
                    'puede tener una tienda.',
                    False,
                )

            cursor.execute(
                """
                UPDATE comercios
                SET plan_id = ?,
                    plan_tipo = ?,
                    limite_productos = ?,
                    estado_pago = 'activo',
                    fecha_inicio_suscripcion = CURRENT_TIMESTAMP,
                    fecha_vencimiento = CURRENT_TIMESTAMP + (? * INTERVAL '1 day'),
                    plan_pendiente = NULL,
                    plan_id_pendiente = NULL,
                    estado_tienda = 'activa',
                    owner_email = ?,
                    usuario_id = ?,
                    visible = 1
                WHERE id = ?
                """,
                (
                    plan['id'],
                    plan['codigo'],
                    plan['limite'],
                    plan['dias'],
                    nuevo_email,
                    usuario_id or None,
                    int(comercio_id),
                ),
            )
            _ascender_a_comerciante(cursor, usuario_id)

            _auditar(
                cursor,
                admin_id,
                'Transferencia de propiedad (admin)',
                f'Comercio {int(comercio_id)} -> {nuevo_email} '
                f'({"vinculado" if usuario_id else "pendiente de primer login"}), '
                f'plan {plan["codigo"]}.',
            )
            conexion.commit()

        _asegurar_slug(comercio_id, actual.get('nombre'))
        try:
            from backend.correo import notificar_propietario_tienda

            notificar_propietario_tienda(actual.get('nombre'), nuevo_email, bool(usuario_id))
        except Exception as error:
            logger.error(f'{LOG_PREFIX} aviso notificación: {error}')

        if usuario_id:
            return (
                True,
                'Propiedad transferida. La tienda quedó como un comercio nuevo '
                'con plan gratis.',
                True,
            )
        return (
            True,
            'Correo guardado. La tienda quedó lista y se enlazará al dueño en '
            'su primer inicio de sesión con ese Gmail.',
            False,
        )
    except Exception as error:
        return False, f'Error al transferir la propiedad: {error}', False


def reasignar_propietario_comercio(admin_id, comercio_id, nuevo_email):
    """Cambia el dueño de una tienda ya normal, **sin tocar plan ni fechas**.

    Si el correo aún no tiene usuario registrado, se guarda y la tienda queda
    reclamable (se enlaza en el primer login). Retorna
    ``(exito, mensaje, vinculado)``. Nunca lanza.
    """
    nuevo_email = (nuevo_email or '').strip().lower()
    if not correo_valido(nuevo_email):
        return False, 'El correo indicado no es válido.', False

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            actual = _estado_actual_comercio(cursor, comercio_id)
            if not actual:
                return False, 'Comercio no encontrado.', False
            if str(actual['estado_tienda'] or '').lower() == 'pre_cuenta':
                return (
                    False,
                    'Esta tienda es una pre-cuenta. Usa "Transferir propiedad" '
                    'para activarla y asignarle el plan gratis.',
                    False,
                )

            usuario_id = _buscar_usuario_por_correo(cursor, nuevo_email)
            if _usuario_tiene_tienda(cursor, usuario_id, excluir_comercio_id=comercio_id):
                return (
                    False,
                    'Ese correo ya tiene una tienda asignada. Un usuario solo '
                    'puede tener una tienda.',
                    False,
                )

            dueno_anterior = actual.get('usuario_id')
            correo_anterior = None
            if dueno_anterior:
                cursor.execute(
                    'SELECT correo FROM usuarios WHERE id = ?',
                    (int(dueno_anterior),),
                )
                correo_anterior = _valor_fila(cursor.fetchone(), 'correo', 0)
            cursor.execute(
                """
                UPDATE comercios
                SET owner_email = ?, usuario_id = ?
                WHERE id = ?
                """,
                (nuevo_email, usuario_id or None, int(comercio_id)),
            )
            _ascender_a_comerciante(cursor, usuario_id)
            # Si el dueño anterior perdió su única tienda, se alinea su rol.
            if dueno_anterior and int(dueno_anterior) != int(usuario_id or 0):
                _recalcular_rol_usuario(cursor, dueno_anterior)

            _auditar(
                cursor,
                admin_id,
                'Reasignación de dueño (admin)',
                f'Comercio {int(comercio_id)} -> {nuevo_email} '
                f'({"vinculado" if usuario_id else "pendiente de primer login"}).',
            )
            conexion.commit()

        _asegurar_slug(comercio_id, actual.get('nombre'))
        try:
            from backend.correo import (
                notificar_dueno_anterior,
                notificar_propietario_tienda,
            )

            notificar_propietario_tienda(actual.get('nombre'), nuevo_email, bool(usuario_id))
            if correo_anterior and correo_anterior.lower() != nuevo_email:
                notificar_dueno_anterior(actual.get('nombre'), correo_anterior)
        except Exception as error:
            logger.error(f'{LOG_PREFIX} aviso notificación: {error}')

        if usuario_id:
            return True, 'Dueño reasignado.', True
        return (
            True,
            'Correo guardado. El dueño se enlazará en su primer inicio de sesión '
            'con ese Gmail.',
            False,
        )
    except Exception as error:
        return False, f'Error al reasignar el dueño: {error}', False


def reclamar_comercios_por_email(cursor, usuario_id, email):
    """Vincula al usuario las tiendas cuyo ``owner_email`` coincide.

    Se invoca dentro de la transacción de alta/login de Google. Solo enlaza
    tiendas sin dueño (``usuario_id IS NULL``); no activa ni reinicia planes
    (eso lo hace la transferencia). Devuelve la lista de ``comercio_id``
    reclamados (posiblemente vacía). Nunca lanza.
    """
    if not usuario_id or not email:
        return []
    # Regla 1 tienda por usuario: si ya posee una, no se enlaza otra.
    if _usuario_tiene_tienda(cursor, usuario_id):
        return []
    try:
        cursor.execute('SAVEPOINT reclamar_precuenta')
    except Exception:
        pass
    try:
        cursor.execute(
            """
            UPDATE comercios
            SET usuario_id = ?
            WHERE id = (
                SELECT id FROM comercios
                WHERE usuario_id IS NULL
                  AND LOWER(TRIM(owner_email)) = LOWER(TRIM(?))
                ORDER BY id ASC
                LIMIT 1
            )
            RETURNING id
            """,
            (int(usuario_id), email),
        )
        filas = cursor.fetchall() or []
        try:
            cursor.execute('RELEASE SAVEPOINT reclamar_precuenta')
        except Exception:
            pass
    except Exception as error:
        logger.error(f'{LOG_PREFIX} no se pudo reclamar por correo: {error}')
        try:
            cursor.execute('ROLLBACK TO SAVEPOINT reclamar_precuenta')
        except Exception:
            pass
        return []

    reclamados = []
    for fila in filas:
        comercio_id = _valor_fila(fila, 'id', 0)
        if comercio_id is not None:
            reclamados.append(comercio_id)
    return reclamados


# ===========================================================================
# Vista previa de pre-cuenta (compartir con el comercio, SOLO lectura)
# ===========================================================================
# La pre-cuenta está oculta al público hasta la transferencia, así que no hay
# forma de "mostrarla" con un enlace normal. Este token firmado permite generar
# una URL de vista previa que el admin comparte con el comercio. Es determinista
# (HMAC con secreto), no adivinable y no requiere columnas nuevas. Para revocar
# todas las vistas previas, cambia ``LOCALIS_PREVIEW_SECRET`` (o reordena el
# secreto de la app).

_PREFIJO_TOKEN_PREVIEW = 'vista-previa'


def _secreto_preview():
    return (
        os.environ.get('LOCALIS_PREVIEW_SECRET')
        or os.environ.get('LOCALIS_SECRET_KEY')
        or 'localis-preview-dev'
    ).strip()


def token_vista_previa(comercio_id):
    """Token firmado (URL-safe) para la vista previa de un comercio."""
    mensaje = f'{_PREFIJO_TOKEN_PREVIEW}:{int(comercio_id)}'.encode('utf-8')
    firma = hmac.new(_secreto_preview().encode('utf-8'), mensaje, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(firma).decode('ascii').rstrip('=')[:32]


def token_vista_previa_valido(comercio_id, token):
    """True si el token corresponde al comercio (comparación en tiempo constante)."""
    try:
        esperado = token_vista_previa(comercio_id)
        return hmac.compare_digest(str(token or ''), esperado)
    except (TypeError, ValueError):
        return False
