"""Prueba de integración: ubicaciones (sedes) de un comercio con base de datos.

Verifica el flujo real contra PostgreSQL/Supabase:
  * ``registrar_comercio_completo`` con varias sedes (nombre + Maps).
  * La primera sede alimenta las columnas históricas del comercio.
  * ``obtener_comercio_por_id`` devuelve las sedes ordenadas.
  * ``actualizar_datos_comercio`` reemplaza la lista completa de sedes.
  * ``actualizar_comercio_admin`` también soporta el repeater.

Crea y borra datos sandbox con el prefijo ``__localis_qa_ubic__``. No toca
datos reales. Se ejecuta con base de datos configurada (DATABASE_URL).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PREFIJO = '__localis_qa_ubic__'
CORREO = f'{PREFIJO}@example.com'
NOMBRE = f'{PREFIJO} comercio'
NOMBRE_ADMIN = f'{PREFIJO} admin'
CORREO_ADMIN = f'{PREFIJO}admin@example.com'

MAPS_SEDE_CENTRO = 'https://www.google.com/maps/place/Sede+Centro/@10.9570,-63.8697,17z'
MAPS_SEDE_NORTE = 'https://www.google.com/maps?q=10.9700,-63.8750'


def _limpiar_sandbox():
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        # Las sucursales se borran en cascada al borrar el comercio.
        cursor.execute('DELETE FROM comercios WHERE nombre LIKE ?', (f'{PREFIJO}%',))
        cursor.execute(
            'DELETE FROM usuarios WHERE correo IN (?, ?)', (CORREO, CORREO_ADMIN)
        )
        conexion.commit()


def _crear_usuarios():
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            "INSERT INTO usuarios (nombre, correo, rol) VALUES (?, ?, 'comerciante') RETURNING id",
            (f'{PREFIJO} dueño', CORREO),
        )
        fila = cursor.fetchone()
        usuario_id = fila['id'] if isinstance(fila, dict) else fila[0]
        cursor.execute(
            "INSERT INTO usuarios (nombre, correo, rol) VALUES (?, ?, 'admin') RETURNING id",
            (NOMBRE_ADMIN, CORREO_ADMIN),
        )
        fila = cursor.fetchone()
        admin_id = fila['id'] if isinstance(fila, dict) else fila[0]
        cursor.execute('SELECT id FROM categorias ORDER BY id LIMIT 1')
        cat = cursor.fetchone()
        categoria_id = cat['id'] if isinstance(cat, dict) else (cat[0] if cat else 1)
        conexion.commit()
    return int(usuario_id), int(admin_id), int(categoria_id)


def main():
    from backend.db import get_db_connection
    from backend.stores import (
        actualizar_datos_comercio,
        obtener_comercio_por_id,
        registrar_comercio_completo,
    )
    from backend.admin import actualizar_comercio_admin

    errores = []

    def _ok(cond, msg):
        if cond:
            print(f'  OK  {msg}')
        else:
            print(f'  FALLO  {msg}')
            errores.append(msg)

    _limpiar_sandbox()
    usuario_id, admin_id, categoria_id = _crear_usuarios()

    try:
        print('\n=== Alta con varias sedes ===')
        exito, msg = registrar_comercio_completo(
            usuario_id,
            NOMBRE,
            'Comercio de prueba con varias sedes',
            '04141234567',
            '',
            categoria_id,
            ubicaciones=[
                {'nombre': 'Sede Centro', 'maps_url': MAPS_SEDE_CENTRO},
                {'nombre': 'Sede Norte', 'maps_url': MAPS_SEDE_NORTE},
            ],
        )
        _ok(exito is True, f'alta con sedes exitosa ({msg})')

        comercio = None
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT id FROM comercios WHERE nombre = ? ORDER BY id DESC LIMIT 1',
                (NOMBRE,),
            )
            fila = cursor.fetchone()
        if fila:
            comercio = obtener_comercio_por_id(
                fila['id'] if isinstance(fila, dict) else fila[0],
                solo_visible=False,
            )
        _ok(comercio is not None, 'comercio recuperable por id')

        sedes = (comercio or {}).get('sucursales') or []
        _ok(len(sedes) == 2, f'se guardaron 2 sedes (got {len(sedes)})')
        nombres = [s.get('nombre') for s in sedes]
        _ok(nombres == ['Sede Centro', 'Sede Norte'], f'sedes ordenadas y con nombre ({nombres})')
        _ok(
            all(s.get('maps_link') for s in sedes),
            'cada sede expone su enlace de Maps',
        )
        _ok(
            sedes[0].get('maps_link') == MAPS_SEDE_CENTRO,
            'la primera sede conserva su enlace original',
        )
        _ok(
            bool(comercio.get('direccion')),
            f'la dirección histórica se deriva de la sede principal ({comercio.get("direccion")!r})',
        )
        _ok(
            comercio.get('maps_url') == MAPS_SEDE_CENTRO,
            'el maps_url histórico usa la sede principal',
        )

        print('\n=== Edición del comerciante: reemplaza sedes ===')
        exito, msg = actualizar_datos_comercio(
            comercio['id'],
            NOMBRE,
            '04149876543',
            '',
            descripcion='Actualizado',
            ubicaciones=[
                {'nombre': 'Única Sede', 'maps_url': MAPS_SEDE_NORTE},
            ],
        )
        _ok(exito is True, f'edición del comerciante exitosa ({msg})')
        comercio = obtener_comercio_por_id(comercio['id'], solo_visible=False)
        sedes = comercio.get('sucursales') or []
        _ok(len(sedes) == 1 and sedes[0].get('nombre') == 'Única Sede',
            f'la lista de sedes se reemplaza por completo ({[s.get("nombre") for s in sedes]})')

        print('\n=== Edición desde admin: repeater ===')
        exito, msg = actualizar_comercio_admin(
            admin_id,
            comercio['id'],
            {
                'nombre': NOMBRE,
                'descripcion': 'Admin',
                'telefono': '04141112223',
                'ubicaciones': [
                    {'nombre': 'A', 'maps_url': MAPS_SEDE_CENTRO},
                    {'nombre': 'B', 'maps_url': MAPS_SEDE_NORTE},
                ],
            },
        )
        _ok(exito is True, f'edición admin exitosa ({msg})')
        comercio = obtener_comercio_por_id(comercio['id'], solo_visible=False)
        sedes = comercio.get('sucursales') or []
        _ok(len(sedes) == 2, f'admin guarda 2 sedes (got {len(sedes)})')
        _ok(
            [s.get('nombre') for s in sedes] == ['A', 'B'],
            'admin respeta el orden del repeater',
        )

        print('\n=== Validación: sede sin Maps se rechaza ===')
        exito, msg = actualizar_datos_comercio(
            comercio['id'],
            NOMBRE,
            '04141112223',
            '',
            ubicaciones=[{'nombre': 'Sin mapa', 'maps_url': ''}],
        )
        _ok(exito is False, 'se rechaza una sede sin enlace de Maps')

        print('\n=== Compatibilidad: sin repeater no toca las sedes ===')
        exito, msg = actualizar_datos_comercio(
            comercio['id'],
            NOMBRE,
            '04141112223',
            'Calle vieja 123',
            ciudad='Porlamar',
            zona='Centro',
        )
        _ok(exito is True, f'edición legada sin ubicaciones exitosa ({msg})')
        comercio = obtener_comercio_por_id(comercio['id'], solo_visible=False)
        _ok(
            len(comercio.get('sucursales') or []) == 2,
            'las sedes previas se conservan sin repeater',
        )

    finally:
        _limpiar_sandbox()

    if errores:
        print(f'\nRESULTADO: {len(errores)} fallo(s)')
        return 1
    print('\nRESULTADO: OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
