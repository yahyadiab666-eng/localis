import os
import sqlite3

from backend.db import get_db_connection

# Correo asignado como Administrador del sistema
ADMIN_EMAIL = "ydiab.t@gmail.com"

def obtener_o_crear_usuario_google(google_info):
    if not google_info:
        return False, "No se recibió información de perfil desde Google."

    email = google_info.get("email")
    nombre = google_info.get("name") or google_info.get("given_name") or "Usuario"
    foto_url = google_info.get("picture", "")

    if not email:
        return False, "No se pudo obtener el correo de la cuenta de Google."

    # Determinar si el correo que se autentica es el del administrador
    rol_determinado = "admin" if email.lower() == ADMIN_EMAIL.lower() else "comerciante"

    try:
        conexion = get_db_connection(row_factory=sqlite3.Row)
        cursor = conexion.cursor()

        # 1. Verificar si el usuario ya existe
        cursor.execute("SELECT * FROM usuarios WHERE correo = ?", (email,))
        usuario = cursor.fetchone()

        if usuario:
            # Actualizamos foto_url y aseguramos que si es tu correo mantenga el rol 'admin'
            cursor.execute(
                "UPDATE usuarios SET foto_url = ?, rol = ? WHERE id = ?",
                (foto_url, rol_determinado if email.lower() == ADMIN_EMAIL.lower() else usuario["rol"], usuario["id"]),
            )
            conexion.commit()

            cursor.execute("SELECT * FROM usuarios WHERE id = ?", (usuario["id"],))
            usuario_actualizado = dict(cursor.fetchone())
            conexion.close()
            return True, usuario_actualizado

        # 2. Si es un usuario nuevo, asignamos el rol que le corresponde
        cursor.execute(
            """
            INSERT INTO usuarios (nombre, correo, foto_url, rol)
            VALUES (?, ?, ?, ?)
            """,
            (nombre, email, foto_url, rol_determinado),
        )

        conexion.commit()
        nuevo_id = cursor.lastrowid

        cursor.execute("SELECT * FROM usuarios WHERE id = ?", (nuevo_id,))
        nuevo_usuario = dict(cursor.fetchone())
        conexion.close()

        return True, nuevo_usuario

    except Exception as e:
        return False, f"Error al procesar usuario con Google: {str(e)}"