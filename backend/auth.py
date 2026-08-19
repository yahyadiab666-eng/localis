import sqlite3

import psycopg2

from backend.db import get_db_connection

# Correo asignado como Administrador del sistema
ADMIN_EMAIL = "ydiab.t@gmail.com"


def _fila_a_dict(fila):
    if fila is None:
        return None
    return dict(fila)


def obtener_o_crear_usuario_google(google_info):
    if not google_info:
        return False, "No se recibió información de perfil desde Google."

    email = google_info.get("email")
    nombre = google_info.get("name") or google_info.get("given_name") or "Usuario"
    foto_url = google_info.get("picture", "")

    if not email:
        return False, "No se pudo obtener el correo de la cuenta de Google."

    rol_determinado = "admin" if email.lower() == ADMIN_EMAIL.lower() else "comerciante"

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            cursor.execute("SELECT * FROM usuarios WHERE correo = ?", (email,))
            usuario = cursor.fetchone()

            if usuario:
                rol_actual = (
                    rol_determinado
                    if email.lower() == ADMIN_EMAIL.lower()
                    else usuario["rol"]
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
                        WHEN LOWER(usuarios.correo) = LOWER(?)
                        THEN EXCLUDED.rol
                        ELSE usuarios.rol
                    END
                RETURNING *
                """,
                (nombre, email, foto_url, rol_determinado, ADMIN_EMAIL),
            )
            nuevo_usuario = _fila_a_dict(cursor.fetchone())
            if not nuevo_usuario or not nuevo_usuario.get("id"):
                return False, "No se pudo crear el usuario en la base de datos."
            return True, nuevo_usuario

    except psycopg2.Error as error:
        return False, (
            "No se pudo conectar con la base de datos durante el inicio de sesión. "
            f"Intenta de nuevo. ({error.pgcode or 'BD'})"
        )
    except Exception as e:
        return False, f"Error al procesar usuario con Google: {str(e)}"
