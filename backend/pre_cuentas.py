"""Pre-cuentas (tiendas sin dueño) y traspaso de propiedad.

Permite al administrador **pre-crear** un comercio (y su catálogo) sin exigir
que el comerciante se haya registrado con Google. La tienda queda en
``estado_tienda = 'pre_cuenta'`` con un ``owner_email`` de referencia y
``usuario_id`` nulo.

Al ingresar por primera vez con ese Gmail, ``reclamar_comercios_por_email``
(enlazada desde ``obtener_o_crear_usuario_google``) vincula el perfil a la tienda
y la marca ``'activa'``, sin tocar el flujo OAuth existente.

Todo es aditivo y defensivo: usa las columnas nuevas ``estado_tienda`` y
``owner_email`` (con default/nulos) y no altera PKs, relaciones ni
``estado_pago``. Nunca lanza: devuelve tuplas/valores seguros.
"""

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
        print(f'{LOG_PREFIX} aviso auditoría: {error}', flush=True)


def _plan_gratis():
    plan = obtener_plan_por_codigo(PLAN_GRATIS_CODIGO) or {}
    return plan.get('id'), (plan.get('limite_productos') or 50)


def crear_pre_cuenta_admin(admin_id, datos):
    """Crea un comercio en estado ``pre_cuenta`` (sin dueño).

    ``datos`` admite: nombre (obligatorio), owner_email (opcional), descripcion,
    telefono, direccion, ciudad, zona, categoria_id.

    Retorna ``(exito, mensaje, comercio_id)``. Nunca lanza.
    """
    datos = datos or {}
    nombre = (datos.get('nombre') or '').strip()
    owner_email = (datos.get('owner_email') or '').strip().lower()
    if not nombre:
        return False, 'El nombre del comercio es obligatorio.', None
    if owner_email and not correo_valido(owner_email):
        return False, 'El correo del dueño no es válido.', None

    plan_id, limite = _plan_gratis()
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
                        'pre_cuenta', ?, 0)
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
                    PLAN_GRATIS_CODIGO,
                    limite,
                    owner_email or None,
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
                print(f'{LOG_PREFIX} código comercio {comercio_id}: {error}', flush=True)

            _auditar(
                cursor,
                admin_id,
                'Alta de pre-cuenta (admin)',
                f'Comercio {comercio_id} pre-creado para '
                f'{owner_email or "sin correo (reclamable manualmente)"}.',
            )
            conexion.commit()
        return (
            True,
            'Pre-cuenta creada. Se activará al iniciar sesión con ese correo.',
            comercio_id,
        )
    except Exception as error:
        return False, f'Error al crear la pre-cuenta: {error}', None


def transferir_propiedad_comercio(admin_id, comercio_id, nuevo_email):
    """Consolida la propiedad: fija ``owner_email`` y activa la tienda.

    Si ya existe un usuario con ese correo, se enlaza de inmediato y su rol pasa
    a ``'comerciante'``. Si no, la tienda queda reclamable y se activará en el
    primer inicio de sesión con ese Gmail.

    Retorna ``(exito, mensaje, vinculado)``. Nunca lanza.
    """
    nuevo_email = (nuevo_email or '').strip().lower()
    if not correo_valido(nuevo_email):
        return False, 'El correo indicado no es válido.', False

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            cursor.execute(
                'SELECT id FROM comercios WHERE id = ?', (int(comercio_id),)
            )
            if not cursor.fetchone():
                return False, 'Comercio no encontrado.', False

            cursor.execute(
                'SELECT id FROM usuarios '
                'WHERE LOWER(TRIM(correo)) = ? ORDER BY id ASC LIMIT 1',
                (nuevo_email,),
            )
            usuario_id = _valor_fila(cursor.fetchone(), 'id', 0)

            cursor.execute(
                """
                UPDATE comercios
                SET owner_email = ?,
                    estado_tienda = 'activa',
                    usuario_id = COALESCE(usuario_id, ?)
                WHERE id = ?
                """,
                (nuevo_email, usuario_id or None, int(comercio_id)),
            )

            if usuario_id:
                try:
                    cursor.execute(
                        "UPDATE usuarios SET rol = 'comerciante' "
                        "WHERE id = ? AND LOWER(TRIM(COALESCE(rol, ''))) <> 'admin'",
                        (int(usuario_id),),
                    )
                except Exception as error:
                    print(f'{LOG_PREFIX} rol usuario {usuario_id}: {error}', flush=True)

            _auditar(
                cursor,
                admin_id,
                'Traspaso de propiedad (admin)',
                f'Comercio {int(comercio_id)} -> {nuevo_email} '
                f'({"vinculado" if usuario_id else "pendiente de primer login"}).',
            )
            conexion.commit()

        if usuario_id:
            return True, 'Propiedad transferida y tienda activada.', True
        return (
            True,
            'Correo registrado. La tienda se activará en el primer inicio de '
            'sesión con ese Gmail.',
            False,
        )
    except Exception as error:
        return False, f'Error al transferir la propiedad: {error}', False


def reclamar_comercios_por_email(cursor, usuario_id, email):
    """Vincula al usuario las tiendas pre-creadas con su correo.

    Se invoca dentro de la transacción de alta/login de Google. Solo reclama
    tiendas sin dueño (``usuario_id IS NULL``). Devuelve la lista de
    ``comercio_id`` reclamados (posiblemente vacía). Nunca lanza.
    """
    if not usuario_id or not email:
        return []
    try:
        cursor.execute('SAVEPOINT reclamar_precuenta')
    except Exception:
        pass
    try:
        cursor.execute(
            """
            UPDATE comercios
            SET usuario_id = ?,
                estado_tienda = 'activa'
            WHERE usuario_id IS NULL
              AND LOWER(TRIM(owner_email)) = LOWER(TRIM(?))
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
        print(f'{LOG_PREFIX} no se pudo reclamar por correo: {error}', flush=True)
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
