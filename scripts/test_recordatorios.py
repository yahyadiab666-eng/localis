"""Recordatorio de vencimiento (requiere BD; se omite sin ella).

Crea una tienda sandbox por vencer, espía el envío de correo, verifica que se
envía el recordatorio y se marca (una sola vez), y limpia al final.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

import backend.recordatorios as rec  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    from database import DATABASE_URL, init_db

    if not DATABASE_URL:
        print('  SKIP  DATABASE_URL no configurada: prueba de recordatorios omitida.')
        return 0

    _ok(bool(init_db()), 'init_db() OK (columna recordatorio_vencimiento_en)')

    from backend.db import get_db_connection

    marca = f'__rec_{uuid.uuid4().hex[:8]}'
    correo = f'{marca}@example.com'
    enviados = []
    orig_enviar = rec.enviar_correo
    orig_config = rec.correo_configurado
    orig_hora = rec._HORA_UTC
    rec.enviar_correo = lambda dest, asunto, cuerpo: (enviados.append((dest, asunto, cuerpo)), (True, 'ok'))[1]
    rec.correo_configurado = lambda: True
    rec._HORA_UTC = 0  # permite ejecutar a cualquier hora en la prueba

    cid = None
    try:
        with get_db_connection() as c:
            cur = c.cursor()
            cur.execute(
                """
                INSERT INTO comercios
                    (nombre, owner_email, visible, estado_tienda, fecha_vencimiento)
                VALUES (?, ?, 1, 'activa', ?) RETURNING id
                """,
                (f'Tienda {marca}', correo, datetime.now(timezone.utc) + timedelta(days=3)),
            )
            fila = cur.fetchone()
            cid = fila[0] if not isinstance(fila, dict) else fila.get('id')
            c.commit()

        print('=== Se envía el recordatorio a la tienda por vencer ===')
        n = rec.enviar_recordatorios_vencimiento()
        # Puede haber varias tiendas por vencer; comprobamos la nuestra.
        mias = [e for e in enviados if e[0] == correo]
        _ok(len(mias) == 1, f'1 correo a la tienda sandbox -> {len(mias)}')
        _ok(bool(mias) and 'vence' in mias[0][1].lower(), 'asunto menciona vencimiento')
        _ok(bool(mias) and '/comercio/planes' in mias[0][2], 'incluye enlace de pago')

        print('\n=== Marca puesta: no repite de inmediato ===')
        with get_db_connection() as c:
            cur = c.cursor()
            cur.execute('SELECT recordatorio_vencimiento_en FROM comercios WHERE id = ?', (cid,))
            fila = cur.fetchone()
            valor = fila[0] if not isinstance(fila, dict) else list(fila.values())[0]
        _ok(valor is not None, 'recordatorio_vencimiento_en quedó marcado')
        enviados.clear()
        rec.enviar_recordatorios_vencimiento()
        _ok(all(e[0] != correo for e in enviados), 'no repite el recordatorio (throttle)')
    finally:
        rec.enviar_correo = orig_enviar
        rec.correo_configurado = orig_config
        rec._HORA_UTC = orig_hora
        if cid:
            with get_db_connection() as c:
                cur = c.cursor()
                cur.execute('DELETE FROM comercios WHERE id = ?', (cid,))
                c.commit()

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK recordatorios: envía a la tienda por vencer, con enlace de pago y sin repetir')
    return 0


if __name__ == '__main__':
    sys.exit(main())
