import logging
logger = logging.getLogger('localis.auth')
import sqlite3

import psycopg2

from backend.db import get_db_connection
from config import es_correo_admin


def _fila_a_dict(fila):
    if fila is None:
        return None
    return dict(fila)


# Columnas del perfil público del usuario. La contraseña NUNCA se incluye.
CAMPOS_PERFIL_USUARIO = 'id, nombre, correo, foto_url, rol'


def obtener_usuario_por_id(usuario_id):
    """Devuelve el perfil del usuario (sin contraseña) o ``None``.

    Se usa en la vista ``/perfil`` para mostrar la cuenta del usuario de
    forma limpia y separada del panel de comercios. Es de solo lectura y
    excluye estrictamente la columna ``contrasena``.
    """
    if not usuario_id:
        return None
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                f'SELECT {CAMPOS_PERFIL_USUARIO} FROM usuarios WHERE id = ?',
                (usuario_id,),
            )
            return _fila_a_dict(cursor.fetchone())
    except psycopg2.Error:
        raise
    except Exception as error:
        logger.error(f'Error al obtener usuario {usuario_id}: {error}')
        return None


def _resolver_rol_tras_reclamo(cursor, usuario_id, email, rol_actual, es_admin):
    """Reclama pre-cuentas del correo y devuelve el rol resultante.

    - Admin: siempre ``admin``.
    - Si se reclamó alguna tienda pre-creada: ``comerciante``.
    - Si no: se conserva ``rol_actual`` (por defecto ``user``).

    Nunca lanza: ante cualquier fallo devuelve el rol actual (o ``admin``).
    """
    if es_admin:
        return "admin"
    try:
        from backend.pre_cuentas import reclamar_comercios_por_email

        reclamados = reclamar_comercios_por_email(cursor, usuario_id, email)
    except Exception as error:
        logger.error(f'[Localis Auth] reclamo de pre-cuenta falló: {error}')
        return rol_actual
    if reclamados:
        return "comerciante"
    return rol_actual


def obtener_o_crear_usuario_google(google_info):
    if not google_info:
        return False, "No se recibió información de perfil desde Google."

    email = google_info.get("email")
    nombre = google_info.get("name") or google_info.get("given_name") or "Usuario"
    foto_url = google_info.get("picture", "")

    if not email:
        return False, "No se pudo obtener el correo de la cuenta de Google."

    es_admin = es_correo_admin(email)
    # Rol por defecto de toda alta nueva: comprador. Se asciende a
    # ``comerciante`` solo al vincular/crear una tienda (ver _resolver_rol...).
    rol_base = "admin" if es_admin else "user"

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            cursor.execute("SELECT * FROM usuarios WHERE correo = ?", (email,))
            usuario = cursor.fetchone()

            if usuario:
                rol_actual = "admin" if es_admin else usuario["rol"]
                rol_actual = _resolver_rol_tras_reclamo(
                    cursor, usuario["id"], email, rol_actual, es_admin
                )
                cursor.execute(
                    """
                    UPDATE usuarios
                    SET foto_url = ?, rol = ?
                    WHERE id = ?
                    RETURNING *
                    """,
                    (foto_url, rol_actual, usuario["id"]),
                )
                usuario_actualizado = _fila_a_dict(cursor.fetchone())
                if not usuario_actualizado:
                    return False, "No se pudo actualizar el usuario autenticado."
                return True, usuario_actualizado

            cursor.execute(
                """
                INSERT INTO usuarios (nombre, correo, foto_url, rol)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (correo) DO UPDATE SET
                    nombre = EXCLUDED.nombre,
                    foto_url = EXCLUDED.foto_url,
                    rol = CASE
                        WHEN ? THEN EXCLUDED.rol
                        ELSE usuarios.rol
                    END
                RETURNING *
                """,
                (nombre, email, foto_url, rol_base, es_admin),
            )
            nuevo_usuario = _fila_a_dict(cursor.fetchone())
            if not nuevo_usuario or not nuevo_usuario.get("id"):
                return False, "No se pudo crear el usuario en la base de datos."

            # Código público visible (USR-0001): independiente de la PK.
            try:
                from backend.codigos_publicos import asignar_codigo_usuario

                codigo = asignar_codigo_usuario(cursor, nuevo_usuario["id"])
                if codigo:
                    nuevo_usuario["codigo_publico"] = codigo
            except Exception as error:
                logger.error(f'[Localis Codigos] usuario {nuevo_usuario["id"]}: {error}')

            # Reclama pre-cuentas creadas con este correo; si hay alguna,
            # persiste el ascenso a comerciante (sin tocar al admin).
            if not es_admin:
                rol_final = _resolver_rol_tras_reclamo(
                    cursor,
                    nuevo_usuario["id"],
                    email,
                    (nuevo_usuario.get("rol") or rol_base),
                    es_admin,
                )
                actual = (nuevo_usuario.get("rol") or "").strip().lower()
                if (rol_final or "").strip().lower() != actual:
                    cursor.execute(
                        "UPDATE usuarios SET rol = ? WHERE id = ? RETURNING *",
                        (rol_final, nuevo_usuario["id"]),
                    )
                    actualizado = _fila_a_dict(cursor.fetchone())
                    if actualizado:
                        nuevo_usuario = actualizado
            return True, nuevo_usuario

    except psycopg2.Error as error:
        return False, (
            "No se pudo conectar con la base de datos durante el inicio de sesión. "
            f"Intenta de nuevo. ({error.pgcode or 'BD'})"
        )
    except Exception as e:
        return False, f"Error al procesar usuario con Google: {str(e)}"
