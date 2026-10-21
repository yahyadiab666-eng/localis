#!/usr/bin/env python3
"""Verificación de pre-cuentas, transferencia, reasignación, roles y códigos.

Requiere ``DATABASE_URL`` real (crea datos sandbox y los limpia al final). Si no
hay BD configurada, se **omite** con código 0 (no es un fallo). Para no esperar
``init_db`` (idempotente pero lento con alta latencia) se puede exportar
``LOCALIS_TEST_SKIP_INIT_DB=1`` si el esquema ya está migrado.

Cubre:
- Rol por defecto de un usuario nuevo: 'user'.
- Pre-cuenta **sin correo** (usuario_id y owner_email NULL), oculta al público.
- Transferencia: activa como comercio nuevo (plan gratis, prueba del plan,
  visible, slug) y guarda el correo del dueño (aunque no esté registrado).
- Reclamo por correo en el primer login (enlaza usuario_id).
- Reasignación: cambia el dueño SIN tocar plan ni fechas.
- Guardas: no transferir una tienda activa con dueño; no reasignar una pre-cuenta.
- Código público de producto (PROD-).
- ``/api/me`` con rol y has_store.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta
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


def _leer_comercio(comercio_id):
    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as c:
        cur = c.cursor()
        cur.execute(
            """
            SELECT nombre, estado_tienda, owner_email, usuario_id, visible,
                   plan_tipo, estado_pago, fecha_vencimiento, slug, codigo_publico
            FROM comercios WHERE id = ?
            """,
            (int(comercio_id),),
        )
        fila = cur.fetchone()
        return dict(fila) if fila else None


def main() -> int:
    from database import DATABASE_URL, init_db

    if not DATABASE_URL:
        print('  SKIP  DATABASE_URL no configurada: prueba de BD omitida.')
        return 0

    skip_init = str(os.environ.get('LOCALIS_TEST_SKIP_INIT_DB', '')).strip().lower() in (
        '1', 'true', 'yes', 'on'
    )
    if not skip_init:
        init_db()

    from backend.db import get_db_connection
    from backend import pre_cuentas
    from backend.admin import crear_producto_admin

    token = f'zz_precuenta_{int(time.time())}_{uuid.uuid4().hex[:8]}'
    correo_pend = f'{token}_pend@example.com'
    correo_demo = f'{token}_dueno@example.com'
    correo_otro = f'{token}_otro@example.com'
    creados = {'usuarios': [], 'comercios': [], 'productos': []}

    try:
        print('\n=== Rol por defecto de usuario nuevo ===')
        with get_db_connection() as c:
            cur = c.cursor()
            uid_admin, _ = _crear_usuario(
                cur, f'{token} admin', f'{token}_admin@example.com', 'admin'
            )
            creados['usuarios'].append(uid_admin)
            uid_user, rol_user = _crear_usuario(cur, f'{token} user', f'{token}_u@example.com')
            creados['usuarios'].append(uid_user)
            c.commit()
        _ok((rol_user or '').strip().lower() == 'user', f'usuario nuevo -> {rol_user!r}')

        print('\n=== Pre-cuenta SIN correo ===')
        exito, mensaje, cid = pre_cuentas.crear_pre_cuenta_admin(
            uid_admin, {'nombre': f'Tienda {token}'}
        )
        _ok(exito and cid, f'pre-cuenta creada: {mensaje}')
        creados['comercios'].append(cid)
        fila = _leer_comercio(cid)
        _ok(fila.get('estado_tienda') == 'pre_cuenta', f"estado={fila.get('estado_tienda')!r}")
        _ok(fila.get('owner_email') in (None, ''), 'sin correo (owner_email NULL)')
        _ok(fila.get('usuario_id') in (None, ''), 'sin dueño (usuario_id NULL)')
        _ok((fila.get('codigo_publico') or '').startswith('COM-'), f"código={fila.get('codigo_publico')!r}")

        print('\n=== Auditoría aislada: admin inexistente no aborta la operación ===')
        # Un admin_id que no existe viola la FK de logs_auditoria. Antes, Postgres
        # abortaba TODA la transacción y la pre-cuenta no se creaba (aunque se
        # devolvía "éxito"). Con el SAVEPOINT en _auditar, debe persistir.
        admin_inexistente = 2_000_000_000
        exito_ai, msg_ai, cid_ai = pre_cuentas.crear_pre_cuenta_admin(
            admin_inexistente, {'nombre': f'Tienda {token} audit'}
        )
        _ok(exito_ai and cid_ai, f'crea la pre-cuenta pese a auditoría inválida: {msg_ai}')
        if cid_ai:
            creados['comercios'].append(cid_ai)
            fila_ai = _leer_comercio(cid_ai)
            _ok(fila_ai is not None, 'la pre-cuenta persistió (transacción no abortada)')

        # Se fuerza visible=1 para probar que el filtro la oculta igualmente.
        with get_db_connection() as c:
            cur = c.cursor()
            cur.execute('UPDATE comercios SET visible = 1 WHERE id = ?', (int(cid),))
            c.commit()
        from backend.stores import obtener_comercio_por_id

        _ok(
            obtener_comercio_por_id(cid, solo_visible=True) is None,
            'pre-cuenta oculta al público (aunque visible=1)',
        )

        print('\n=== Transferencia a correo AÚN NO registrado ===')
        exito_t, msg_t, vinculado = pre_cuentas.transferir_propiedad_comercio(
            uid_admin, cid, correo_pend
        )
        _ok(exito_t and not vinculado, f'transferencia: {msg_t}')
        fila = _leer_comercio(cid)
        _ok(fila.get('estado_tienda') == 'activa', f"estado={fila.get('estado_tienda')!r}")
        _ok(fila.get('owner_email') == correo_pend, 'correo del dueño guardado')
        _ok(fila.get('usuario_id') in (None, ''), 'queda reclamable (usuario_id NULL)')
        _ok(fila.get('visible') == 1, 'publicada (visible=1)')
        _ok((fila.get('plan_tipo') or '') == 'gratis', f"plan={fila.get('plan_tipo')!r}")
        _ok((fila.get('estado_pago') or '') == 'activo', f"estado_pago={fila.get('estado_pago')!r}")
        _ok(bool(fila.get('slug')), f"slug generado: {fila.get('slug')!r}")
        venc = fila.get('fecha_vencimiento')
        ahora = datetime.utcnow()

        def _a_datetime(valor):
            if isinstance(valor, datetime):
                return valor.replace(tzinfo=None) if valor.tzinfo else valor
            if isinstance(valor, str):
                try:
                    return datetime.fromisoformat(valor.replace('Z', '+00:00')).replace(tzinfo=None)
                except ValueError:
                    return None
            return None

        venc_dt = _a_datetime(venc)
        delta_dias = (venc_dt - ahora).days if venc_dt else None
        print(f'  info  ahora={ahora} venc={venc} delta_dias={delta_dias}')
        _ok(
            delta_dias is not None and 25 <= delta_dias <= 35,
            f'vencimiento ~ +30 días: {venc}',
        )
        from backend.stores import obtener_comercio_por_id as _pub

        _ok(_pub(cid, solo_visible=True) is not None, 'ya visible al público tras transferir')

        print('\n=== Reclamo en primer login (usuario se registra después) ===')
        with get_db_connection() as c:
            cur = c.cursor()
            uid_pend, _ = _crear_usuario(cur, f'{token} pend', correo_pend)
            creados['usuarios'].append(uid_pend)
            reclamados = pre_cuentas.reclamar_comercios_por_email(cur, uid_pend, correo_pend)
            c.commit()
        _ok(int(cid) in [int(x) for x in reclamados], 'comercio reclamado')
        _ok(
            int(_leer_comercio(cid).get('usuario_id')) == int(uid_pend),
            'usuario_id enlazado tras login',
        )

        print('\n=== Reasignación (sin tocar plan ni fechas) ===')
        venc_antes = _leer_comercio(cid).get('fecha_vencimiento')
        with get_db_connection() as c:
            cur = c.cursor()
            uid_demo, _ = _crear_usuario(cur, f'{token} dueno', correo_demo)
            creados['usuarios'].append(uid_demo)
            c.commit()
        exito_r, msg_r, vinc_r = pre_cuentas.reasignar_propietario_comercio(
            uid_admin, cid, correo_demo
        )
        _ok(exito_r and vinc_r, f'reasignación: {msg_r}')
        fila = _leer_comercio(cid)
        _ok(int(fila.get('usuario_id')) == int(uid_demo), 'dueño reasignado')
        _ok(fila.get('owner_email') == correo_demo, 'correo actualizado')
        _ok(fila.get('fecha_vencimiento') == venc_antes, 'vencimiento SIN cambios')
        with get_db_connection(row_factory=True) as c:
            cur = c.cursor()
            cur.execute('SELECT rol FROM usuarios WHERE id = ?', (int(uid_demo),))
            rol_demo = dict(cur.fetchone()).get('rol')
        _ok((rol_demo or '').lower() == 'comerciante', f'rol nuevo dueño={rol_demo!r}')
        # El dueño anterior (uid_pend) perdió su única tienda -> rol 'user'.
        with get_db_connection(row_factory=True) as c:
            cur = c.cursor()
            cur.execute('SELECT rol FROM usuarios WHERE id = ?', (int(uid_pend),))
            rol_ant = dict(cur.fetchone()).get('rol')
        _ok((rol_ant or '').lower() == 'user', f'rol dueño anterior recalculado={rol_ant!r}')

        print('\n=== Guardas (1 tienda por usuario) ===')
        exito_g, msg_g, _ = pre_cuentas.transferir_propiedad_comercio(
            uid_admin, cid, correo_otro
        )
        _ok(not exito_g, f'transferir tienda activa rechazado: {msg_g}')
        exito_g2, msg_g2, cid2 = pre_cuentas.crear_pre_cuenta_admin(
            uid_admin, {'nombre': f'Tienda2 {token}'}
        )
        creados['comercios'].append(cid2)
        exito_g3, msg_g3, _ = pre_cuentas.reasignar_propietario_comercio(
            uid_admin, cid2, correo_demo
        )
        _ok(not exito_g3, f'reasignar pre-cuenta rechazado: {msg_g3}')
        # Transferir a un correo que YA tiene tienda (uid_demo posee cid).
        exito_g4, msg_g4, cid4 = pre_cuentas.crear_pre_cuenta_admin(
            uid_admin, {'nombre': f'Tienda4 {token}'}
        )
        creados['comercios'].append(cid4)
        exito_g5, msg_g5, _ = pre_cuentas.transferir_propiedad_comercio(
            uid_admin, cid4, correo_demo
        )
        _ok(not exito_g5, f'transferir a dueño con tienda rechazado: {msg_g5}')
        # Transferir a un correo NUEVO (queda reservado para la UI).
        exito_g6, msg_g6, _ = pre_cuentas.transferir_propiedad_comercio(
            uid_admin, cid2, correo_otro
        )
        _ok(exito_g6, f'transferencia a correo sin usuario: {msg_g6}')
        fila_res = _leer_comercio(cid2)
        _ok(fila_res.get('owner_email') == correo_otro, 'correo reservado guardado')
        _ok(fila_res.get('usuario_id') in (None, ''), 'reservado sin usuario aún')

        print('\n=== Slug único (dedupe) ===')
        _, _, cid_a = pre_cuentas.crear_pre_cuenta_admin(
            uid_admin, {'nombre': f'Duplicada {token}'}
        )
        _, _, cid_b = pre_cuentas.crear_pre_cuenta_admin(
            uid_admin, {'nombre': f'Duplicada {token}'}
        )
        creados['comercios'].extend([cid_a, cid_b])
        slug_a = _leer_comercio(cid_a).get('slug')
        slug_b = _leer_comercio(cid_b).get('slug')
        _ok(
            bool(slug_a) and bool(slug_b) and slug_a != slug_b,
            f'slugs distintos con mismo nombre: {slug_a} / {slug_b}',
        )

        print('\n=== Código público de producto ===')
        exito_p, msg_p, producto_id = crear_producto_admin(
            uid_admin, cid, f'Producto {token}', 'desc', 5.0
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
            _ok((cod or '').startswith('PROD-'), f'código producto: {cod!r}')

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

        print('\n=== Panel admin: render de la UI de pre-cuentas ===')
        with cliente.session_transaction() as sess:
            sess['usuario_id'] = int(uid_admin)
            sess['es_admin'] = True
            sess['rol'] = 'admin'
        r_admin = cliente.get('/admin')
        _ok(r_admin.status_code == 200, f'GET /admin -> {r_admin.status_code}')
        html = r_admin.get_data(as_text=True)
        _ok('Nueva pre-cuenta' in html, 'formulario "Nueva pre-cuenta" presente')
        _ok('badge-pre' in html or 'pre-cuenta' in html, 'badge/estado pre-cuenta presente')
        _ok(
            ('Transferir' in html) or ('Reasignar' in html),
            'acciones Transferir/Reasignar presentes',
        )
        _ok(correo_otro in html, 'correo reservado visible en el panel')
        _ok('pendiente de login' in html, 'badge "pendiente de login" presente')

        print('\n=== Ficha del comercio: render con acciones y código ===')
        r_ficha = cliente.get(f'/admin/comercio/{int(cid2)}')
        _ok(r_ficha.status_code == 200, f'GET /admin/comercio/{cid2} -> {r_ficha.status_code}')
        html_f = r_ficha.get_data(as_text=True)
        _ok(
            ('Transferir propiedad' in html_f) or ('Reasignar dueño' in html_f),
            'ficha muestra acción Transferir/Reasignar',
        )
        cod_res = _leer_comercio(cid2).get('codigo_publico') or ''
        _ok(cod_res and cod_res in html_f, f'ficha muestra codigo_publico {cod_res!r}')
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
    print('OK pre-cuentas/transferencia/reasignación/códigos: verificado')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
