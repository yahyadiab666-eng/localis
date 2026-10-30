#!/usr/bin/env python3
"""Diagnóstico de los IDs (SERIAL) y de los códigos públicos.

Por qué existe
--------------
En PostgreSQL una columna ``SERIAL`` es un **contador de secuencia**, no un
número de orden consecutivo:

- Los valores **no se devuelven** si la transacción hace rollback, si el INSERT
  falla (p. ej. un UNIQUE) o si la operación se reintenta
  (``ejecutar_con_reintentos_bd`` repite la transacción completa).
- Los valores **no se reutilizan** cuando se borra una fila. La importación en
  modo espejo (``LOCALIS_CSV_BAJAS=eliminar`` por defecto) y las bajas del panel
  admin borran productos, así que quedan huecos permanentes.

Por eso el número visible para las personas NO es el id: es ``codigo_publico``
(``PROD-00300``, ``COM-0215``, ``USR-0126``), con un contador propio
(``contadores_publicos``) que sí asigna valores contiguos. Ver
``backend/codigos_publicos.py``.

Qué reporta
-----------
- Por tabla: filas, min/max, huecos (cuántos números faltan) y el estado de su
  secuencia.
- **Aviso crítico** si una secuencia está *por detrás* de ``MAX(id)``: el
  siguiente INSERT chocaría con una PK existente.
- Códigos públicos: cuántos faltan (NULL) y si el contador quedó por detrás.

Uso
---
    python scripts/diagnostico_ids.py            # solo diagnóstico (dry-run)
    python scripts/diagnostico_ids.py --apply    # corrige lo crítico

``--apply`` SOLO reajusta contadores atrasados (``setval`` de la secuencia y del
contador público). **Nunca** renumera ids ni rellena huecos: hacerlo rompería
todas las referencias (productos.comercio_id, tickets.referencia_id, etc.).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

TABLAS = (
    'usuarios',
    'comercios',
    'productos',
    'soporte_y_reportes',
    'solicitudes_pago',
    'logs_auditoria',
)

# Tablas con código público visible: tabla -> clave del contador.
TABLAS_CON_CODIGO = {
    'usuarios': 'usuarios',
    'comercios': 'comercios',
    'productos': 'productos',
}

_ERRORES = []


def _ok(condicion, mensaje):
    estado = 'OK ' if condicion else 'AVISO'
    print(f'  {estado}  {mensaje}')
    if not condicion:
        _ERRORES.append(mensaje)
    return condicion


def _una(cursor, sql, params=()):
    cursor.execute(sql, params)
    fila = cursor.fetchone()
    if fila is None:
        return None
    return fila[0] if not isinstance(fila, dict) else next(iter(fila.values()))


def _secuencia(cursor, tabla):
    return _una(cursor, "SELECT pg_get_serial_sequence(?, 'id')", (tabla,))


def _estado_secuencia(cursor, secuencia):
    if not secuencia:
        return None, None
    cursor.execute(f'SELECT last_value, is_called FROM {secuencia}')
    fila = cursor.fetchone()
    if not fila:
        return None, None
    if isinstance(fila, dict):
        return fila.get('last_value'), fila.get('is_called')
    return fila[0], fila[1]


def _huecos(cursor, tabla):
    """(tramos con hueco, números faltantes, salto máximo)."""
    cursor.execute(
        f'''
        SELECT COUNT(*), COALESCE(SUM(h), 0), COALESCE(MAX(h), 0)
        FROM (
            SELECT id - lag(id) OVER (ORDER BY id) - 1 AS h
            FROM {tabla}
        ) t
        WHERE h > 0
        '''
    )
    fila = cursor.fetchone() or (0, 0, 0)
    return int(fila[0] or 0), int(fila[1] or 0), int(fila[2] or 0)


def _revisar_tabla(cursor, tabla, aplicar):
    print(f'\n=== {tabla} ===')
    total = int(_una(cursor, f'SELECT COUNT(*) FROM {tabla}') or 0)
    if not total:
        print('  (sin filas)')
        return
    minimo = _una(cursor, f'SELECT MIN(id) FROM {tabla}')
    maximo = int(_una(cursor, f'SELECT MAX(id) FROM {tabla}') or 0)
    tramos, faltantes, salto_max = _huecos(cursor, tabla)
    print(
        f'  filas={total} id_min={minimo} id_max={maximo} '
        f'huecos={tramos} numeros_faltantes={faltantes} salto_max={salto_max}'
    )

    secuencia = _secuencia(cursor, tabla)
    last_value, is_called = _estado_secuencia(cursor, secuencia)
    if not secuencia:
        print('  (sin secuencia asociada)')
        return

    proximo = int(last_value or 0) + (0 if not is_called else 1)
    print(f'  secuencia={secuencia} last_value={last_value} is_called={is_called}')
    if proximo <= maximo:
        # Esto SÍ es un problema real: el próximo INSERT usaría un id ya ocupado.
        _ok(
            False,
            f'{tabla}: la secuencia está por detrás de MAX(id) '
            f'(próximo={proximo} <= max={maximo}); el próximo alta fallaría',
        )
        if aplicar:
            cursor.execute('SELECT setval(?, ?, true)', (secuencia, maximo))
            print(f'  -> secuencia reajustada a {maximo} (apply)')
    else:
        _ok(True, f'{tabla}: secuencia por delante de MAX(id) (correcto)')


def _revisar_codigos(cursor, tabla, clave, aplicar):
    print(f'\n--- códigos públicos de {tabla} ---')
    try:
        nulos = int(
            _una(
                cursor,
                f"SELECT COUNT(*) FROM {tabla} "
                "WHERE codigo_publico IS NULL OR TRIM(codigo_publico) = ''",
            )
            or 0
        )
    except Exception as error:
        print(f'  (columna codigo_publico no disponible: {error})')
        return

    total = int(_una(cursor, f'SELECT COUNT(*) FROM {tabla}') or 0)
    maximo_codigo = _una(
        cursor,
        f"""
        SELECT COALESCE(MAX(NULLIF(regexp_replace(codigo_publico, '\\D', '', 'g'), '')::INTEGER), 0)
        FROM {tabla}
        """,
    )
    valor_contador = None
    try:
        cursor.execute(
            'SELECT valor FROM contadores_publicos WHERE clave = ?', (clave,)
        )
        fila = cursor.fetchone()
        valor_contador = (
            (fila.get('valor') if isinstance(fila, dict) else fila[0]) if fila else None
        )
    except Exception as error:
        print(f'  (contadores_publicos no disponible: {error})')

    print(
        f'  filas={total} sin_codigo={nulos} '
        f'max_codigo={int(maximo_codigo or 0)} contador={valor_contador}'
    )
    _ok(nulos == 0, f'{tabla}: todos los registros tienen código público')
    if valor_contador is not None and int(valor_contador) < int(maximo_codigo or 0):
        _ok(
            False,
            f'{tabla}: el contador ({valor_contador}) está por detrás del '
            f'máximo código emitido ({maximo_codigo}); se repetirían códigos',
        )
        if aplicar:
            cursor.execute(
                """
                INSERT INTO contadores_publicos (clave, valor)
                VALUES (?, ?)
                ON CONFLICT (clave) DO UPDATE
                    SET valor = GREATEST(contadores_publicos.valor, EXCLUDED.valor)
                """,
                (clave, int(maximo_codigo or 0)),
            )
            print(f'  -> contador reajustado a {int(maximo_codigo or 0)} (apply)')
    elif valor_contador is not None:
        _ok(True, f'{tabla}: contador por delante del último código (correcto)')


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Diagnóstico de ids SERIAL y códigos públicos (Localis)'
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='reajusta secuencias/contadores atrasados (no toca los ids existentes)',
    )
    args = parser.parse_args()

    from backend.db import get_db_connection

    print('Diagnóstico de IDs y códigos públicos')
    print('(los huecos por borrados/rollbacks son normales y NO se rellenan)')

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            for tabla in TABLAS:
                try:
                    _revisar_tabla(cursor, tabla, args.apply)
                except Exception as error:
                    print(f'  ERROR al revisar {tabla}: {type(error).__name__}: {error}')

            for tabla, clave in TABLAS_CON_CODIGO.items():
                try:
                    _revisar_codigos(cursor, tabla, clave, args.apply)
                except Exception as error:
                    print(f'  ERROR en códigos de {tabla}: {type(error).__name__}: {error}')

            if args.apply:
                conexion.commit()
                print('\nCambios aplicados (solo reajustes de contadores).')
            else:
                conexion.rollback()
                print('\nDry-run: no se escribió nada. Usa --apply para corregir lo crítico.')
    except Exception as error:
        print(f'\nNo se pudo conectar a la base de datos: {type(error).__name__}: {error}')
        return 2

    print('\n=== RESULTADO ===')
    if _ERRORES:
        print(f'AVISOS: {len(_ERRORES)} (revisa los que digan "por detrás")')
        return 1
    print('OK ids y códigos públicos coherentes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
