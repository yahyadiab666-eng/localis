#!/usr/bin/env python3
"""Verificación de pre-cuentas, traspaso de propiedad, roles y códigos públicos.

Requiere ``DATABASE_URL`` real (escribe datos sandbox y los limpia al final). Si
no hay BD configurada, se **omite** con código 0 (no es un fallo).

Cubre:
- Rol por defecto de un usuario nuevo: 'user' (no 'comerciante').
- Generación de códigos públicos (COM-/PROD-) independientes de la PK.
- Una pre-cuenta (estado_tienda='pre_cuenta') no aparece en la vista pública.
- Reclamo por correo: enlaza el perfil, activa la tienda y asciende a comerciante.
- Traspaso de propiedad a un usuario existente.
- ``/api/me`` devuelve rol y ``has_store`` coherentes.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

_ERRORES = []


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


def _crear_usuario(cursor, nombre, correo, rol=None):
    if rol is None:
        cursor.execute(
            'INSERT INTO usuarios (nombre, correo) VALUES (?, ?) RETURNING id, rol',
            (nombre, correo),
        )
    else:
        cursor.execute(
            'INSERT INTO usuarios (nombre, correo, rol) VALUES (?, ?, ?) '
            'RETURNING id, rol',
            (nombre, correo, rol),
        )
    fila = cursor.fetchone()
    if isinstance(fila, dict):
        return fila.get('id'), fila.get('rol')
    return fila[0], fila[1]


def main() -> int:
    from database import DATABASE_URL, init_db

    if not DATABASE_URL:
        print('  SKIP  DATABASE_URL no configurada: prueba de BD omitida.')
        return 0

    # init_db es idempotente pero hace cientos de consultas de metadatos (lento
    # con alta latencia). Si el esquema ya está migrado, se puede omitir con
    # LOCALIS_TEST_SKIP_INIT_DB=1.
    skip_init = str(os.environ.get('LOCALIS_TEST_SKIP_INIT_DB', '')).strip().lower() in (
        '1', 'true', 'yes', 'on'
    )
    if not skip_init:
        init_db()

    from backend.db import get_db_connection
    from backend import pre_cuentas
    from backend.admin import crear_producto_admin
    from backend.stores import (
        buscar_y_filtrar_comercios,
        obtener_comercio_por_id,
    )

    token = f'zz_precuenta_{int(time.time())}'
    correo_demo = f'{token}@example.com'
    creados = {'usuarios': [], 'comercios': [], 'productos': []}

    try:
        print('\n=== Rol por defecto de usuario nuevo ===')
        with get_db_connection() as c:
            cur = c.cursor()
            uid_admin, _ = _crear_usuario(cur, f'{token} admin', f'{token}_admin@example.com', 'admin')
            creados['usuarios'].append(uid_admin)
            uid_user, rol_user = _crear_usuario(cur, f'{token} user', f'{token}_u@example.com')
            creados['usuarios'].append(uid_user)
            c.commit()
        _ok(
            (rol_user or '').strip().lower() == 'user',
            f'usuario nuevo sin rol -> {rol_user!r} (esperado user)',
        )

        print('\n=== Pre-cuenta: creación y ocultamiento público ===')
        exito, mensaje, comercio_id = pre_cuentas.crear_pre_cuenta_admin(
            uid_admin, {'nombre': f'Tienda {token}', 'owner_email': correo_demo}
        )
        _ok(exito and comercio_id, f'pre-cuenta creada: {mensaje}')
        creados['comercios'].append(comercio_id)

        with get_db_connection(row_factory=True) as c:
            cur = c.cursor()
            cur.execute(
                'SELECT codigo_publico, estado_tienda, usuario_id FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
            fila = dict(cur.fetchone())
            cur.execute('UPDATE comercios SET visible = 1 WHERE id = ?', (int(comercio_id),))
            c.commit()
        _ok(
            (fila.get('codigo_publico') or '').startswith('COM-'),
            f'código público de comercio: {fila.get("codigo_publico")!r}',
        )
        _ok(
            (fila.get('estado_tienda') or '') == 'pre_cuenta',
            f'estado_tienda={fila.get("estado_tienda")!r} (esperado pre_cuenta)',
        )
        _ok(fila.get('usuario_id') is None, 'pre-cuenta sin dueño (usuario_id NULL)')

        _ok(
            obtener_comercio_por_id(comercio_id, solo_visible=True) is None,
            'pre-cuenta NO visible en consulta pública (aun con visible=1)',
        )
        resultados = buscar_y_filtrar_comercios(palabra_clave=token) or []
        ids_publicos = {str(r.get('id')) for r in resultados}
        _ok(
            str(comercio_id) not in ids_publicos,
            'pre-cuenta ausente del listado público por búsqueda',
        )

        print('\n=== Reclamo por correo (primer login) ===')
        with get_db_connection() as c:
            cur = c.cursor()
            uid_demo, _ = _crear_usuario(cur, f'{token} dueno', correo_demo)
            creados['usuarios'].append(uid_demo)
            reclamados = pre_cuentas.reclamar_comercios_por_email(cur, uid_demo, correo_demo)
            cur.execute(
                'SELECT usuario_id, estado_tienda FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
            fila2 = cur.fetchone()
            usuario_id_final = (
                fila2.get('usuario_id') if isinstance(fila2, dict) else fila2[0]
            )
            estado_final = (
                fila2.get('estado_tienda') if isinstance(fila2, dict) else fila2[1]
            )
            c.commit()
        _ok(int(comercio_id) in [int(x) for x in reclamados], 'comercio reclamado por correo')
        _ok(int(usuario_id_final) == int(uid_demo), 'usuario_id enlazado a la tienda')
        _ok(estado_final == 'activa', f'estado_tienda tras reclamo={estado_final!r}')

        print('\n=== Traspaso de propiedad a usuario existente ===')
        exito_t, msg_t, vinculado = pre_cuentas.transferir_propiedad_comercio(
            uid_admin, comercio_id, correo_demo
        )
        _ok(exito_t and vinculado, f'traspaso vinculado: {msg_t}')
        with get_db_connection(row_factory=True) as c:
            cur = c.cursor()
            cur.execute('SELECT rol FROM usuarios WHERE id = ?', (int(uid_demo),))
            rol_demo = dict(cur.fetchone()).get('rol')
        _ok(
            (rol_demo or '').strip().lower() == 'comerciante',
            f'rol tras traspaso={rol_demo!r} (esperado comerciante)',
        )

        print('\n=== Código público de producto ===')
        exito_p, msg_p, producto_id = crear_producto_admin(
            uid_admin, comercio_id, f'Producto {token}', 'desc', 5.0
        )
        _ok(exito_p and producto_id, f'producto creado: {msg_p}')
        if producto_id:
            creados['productos'].append(producto_id)
            with get_db_connection(row_factory=True) as c:
                cur = c.cursor()
                cur.execute(
                    'SELECT codigo_publico FROM productos WHERE id = ?', (int(producto_id),)
                )
                cod = dict(cur.fetchone()).get('codigo_publico')
            _ok((cod or '').startswith('PROD-'), f'código público de producto: {cod!r}')

        print('\n=== /api/me (rol y has_store) ===')
        from main import app

        app.config['TESTING'] = True
        app.config['WTF_CSRF_ENABLED'] = False
        cliente = app.test_client()
        with cliente.session_transaction() as sess:
            sess['usuario_id'] = int(uid_demo)
            sess['correo'] = correo_demo
            sess['rol'] = 'comerciante'
        r = cliente.get('/api/me')
        _ok(r.status_code == 200, f'GET /api/me -> {r.status_code}')
        datos = r.get_json() or {}
        _ok(datos.get('rol') == 'comerciante', f"rol /api/me={datos.get('rol')!r}")
        _ok(datos.get('has_store') is True, 'has_store=True')
    except Exception:
        traceback.print_exc()
        _ok(False, 'la prueba no lanzó excepciones')
    finally:
        try:
            with get_db_connection() as c:
                cur = c.cursor()
                for pid in creados['productos']:
                    cur.execute('DELETE FROM productos WHERE id = ?', (int(pid),))
                for cid in creados['comercios']:
                    cur.execute('DELETE FROM productos WHERE comercio_id = ?', (int(cid),))
                    cur.execute('DELETE FROM comercios WHERE id = ?', (int(cid),))
                for uid in creados['usuarios']:
                    cur.execute('DELETE FROM comercios WHERE usuario_id = ?', (int(uid),))
                    cur.execute('DELETE FROM usuarios WHERE id = ?', (int(uid),))
                c.commit()
        except Exception as error:
            print(f'  aviso  limpieza: {error}')

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK pre-cuentas/roles/códigos: verificado')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
