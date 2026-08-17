"""Esquema SQLite, migraciones incrementales e índices de rendimiento."""

import os
import sqlite3

from config import DATABASE_FILE, RUTA_SCHEMA
from backend.db import get_db_connection

TABLAS_PERMITIDAS = frozenset({
    'usuarios',
    'planes',
    'categorias',
    'comercios',
    'tiendas',
    'sucursales',
    'productos',
    'pagos',
    'soporte_y_reportes',
    'configuracion_sistema',
    'logs_auditoria',
    'intentos_login',
    'solicitudes_pago',
})

COLUMNAS_COMERCIOS = [
    ('banner_url', 'TEXT'),
    ('ciudad', 'TEXT'),
    ('zona', 'TEXT'),
    ('maps_url', 'TEXT'),
    ('ubicacion_maps_url', 'TEXT'),
    ('documento_identidad', 'TEXT'),
    ('plan_id', 'INTEGER DEFAULT 1'),
    ('plan_tipo', "TEXT DEFAULT 'gratis'"),
    ('fecha_inicio_suscripcion', 'DATETIME'),
    ('fecha_vencimiento', 'DATE'),
    ('limite_productos', 'INTEGER DEFAULT 50'),
    ('estado_pago', "TEXT DEFAULT 'activo'"),
    ('aviso_bienvenida_visto', 'INTEGER DEFAULT 0'),
    ('imagen_portada', 'TEXT'),
]

PLANES_SEED = [
    ('gratis', 'Plan Gratis / Prueba', 0, 50, 0, 30, 0, 1),
    ('basica', 'Básica', 10, 50, 0, 30, 0, 1),
    ('pro', 'Pro', 17, 200, 1, 30, 1, 1),
    ('business', 'Business', 35, None, 1, 30, 0, 1),
]


def _columnas_existentes(cursor, tabla):
    if tabla not in TABLAS_PERMITIDAS:
        raise ValueError(f'Tabla no permitida para introspección: {tabla}')
    cursor.execute(f'PRAGMA table_info({tabla})')
    return {row[1] for row in cursor.fetchall()}


def _tabla_existe(cursor, tabla):
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (tabla,),
    )
    return cursor.fetchone() is not None


def _agregar_columna_si_falta(cursor, tabla, nombre, tipo_sql):
    existentes = _columnas_existentes(cursor, tabla)
    if nombre not in existentes:
        cursor.execute(f'ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo_sql}')


def _ejecutar_schema_base(cursor):
    if os.path.exists(RUTA_SCHEMA):
        with open(RUTA_SCHEMA, 'r', encoding='utf-8') as archivo:
            cursor.executescript(archivo.read())


def _crear_tabla_planes(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS planes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE,
            nombre TEXT UNIQUE NOT NULL,
            precio REAL NOT NULL,
            limite_productos INTEGER NOT NULL,
            soporte_prioritario INTEGER DEFAULT 0,
            dias_duracion INTEGER DEFAULT 30,
            destacado INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1
        )
        """
    )

    columnas = _columnas_existentes(cursor, 'planes')
    if 'codigo' not in columnas:
        _agregar_columna_si_falta(cursor, 'planes', 'codigo', 'TEXT')

    for fila in PLANES_SEED:
        cursor.execute(
            """
            INSERT OR IGNORE INTO planes
            (codigo, nombre, precio, limite_productos, soporte_prioritario,
             dias_duracion, destacado, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            fila,
        )


def _crear_tabla_pagos(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tienda_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            monto REAL NOT NULL,
            metodo TEXT NOT NULL,
            referencia TEXT,
            banco_origen TEXT,
            cedula_pagador TEXT,
            telefono_pagador TEXT,
            estado TEXT DEFAULT 'pendiente',
            fecha_pago DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (tienda_id) REFERENCES comercios(id),
            FOREIGN KEY (plan_id) REFERENCES planes(id)
        )
        """
    )


def _crear_vista_tiendas(cursor):
    """Vista de suscripción sobre comercios (alias tiendas)."""
    cursor.execute(
        """
        CREATE VIEW IF NOT EXISTS tiendas AS
        SELECT
            id,
            usuario_id,
            nombre,
            COALESCE(plan_id, 1) AS plan_id,
            COALESCE(estado_pago, 'activo') AS estado,
            fecha_registro,
            fecha_vencimiento,
            COALESCE(ubicacion_maps_url, maps_url) AS ubicacion_maps_url,
            COALESCE(imagen_portada, banner_url) AS imagen_portada
        FROM comercios
        """
    )


def _migrar_columnas_comercios(cursor):
    if not _tabla_existe(cursor, 'comercios'):
        return

    for nombre, tipo in COLUMNAS_COMERCIOS:
        _agregar_columna_si_falta(cursor, 'comercios', nombre, tipo)

    cursor.execute(
        """
        UPDATE comercios
        SET ubicacion_maps_url = maps_url
        WHERE ubicacion_maps_url IS NULL AND maps_url IS NOT NULL
        """
    )
    cursor.execute(
        """
        UPDATE comercios
        SET imagen_portada = banner_url
        WHERE imagen_portada IS NULL AND banner_url IS NOT NULL
        """
    )
    cursor.execute(
        """
        UPDATE comercios
        SET plan_id = 1
        WHERE plan_id IS NULL
        """
    )


def _asignar_plan_id_existentes(cursor):
    if not _tabla_existe(cursor, 'comercios'):
        return

    cursor.execute('SELECT id, codigo FROM planes')
    mapa = {row[1]: row[0] for row in cursor.fetchall() if row[1]}

    cursor.execute('SELECT id, plan_tipo, plan_id FROM comercios')
    for comercio_id, plan_tipo, plan_id in cursor.fetchall():
        if plan_id:
            continue
        codigo = (plan_tipo or 'gratis').lower()
        if codigo not in mapa:
            codigo = 'gratis'
        cursor.execute(
            'UPDATE comercios SET plan_id = ? WHERE id = ?',
            (mapa.get(codigo, 1), comercio_id),
        )


def _crear_indices(cursor):
    indices = [
        'CREATE INDEX IF NOT EXISTS idx_productos_tienda ON productos(comercio_id)',
        'CREATE INDEX IF NOT EXISTS idx_tiendas_estado ON comercios(estado_pago, plan_id)',
        'CREATE INDEX IF NOT EXISTS idx_pagos_tienda ON pagos(tienda_id)',
        'CREATE INDEX IF NOT EXISTS idx_productos_comercio_id ON productos(comercio_id)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_estado_plan ON comercios(estado_pago, plan_tipo)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_plan_id ON comercios(plan_id)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_ciudad ON comercios(ciudad)',
        'CREATE INDEX IF NOT EXISTS idx_intentos_correo ON intentos_login(correo_intentado)',
        'CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos(nombre)',
        'CREATE INDEX IF NOT EXISTS idx_productos_codigo_barras ON productos(codigo_barras)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_nombre ON comercios(nombre)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_categoria ON comercios(categoria_id)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_visible ON comercios(visible)',
    ]
    for ddl in indices:
        cursor.execute(ddl)


def _sembrar_configuracion(cursor):
    cursor.execute(
        """
        INSERT OR IGNORE INTO configuracion_sistema (clave, valor)
        VALUES ('tasa_dolar', '36.50')
        """
    )
    cursor.execute(
        """
        INSERT OR IGNORE INTO configuracion_sistema (clave, valor)
        VALUES ('banner_principal', '/static/images/default-banner.jpg')
        """
    )
    cursor.execute(
        """
        INSERT OR IGNORE INTO configuracion_sistema (clave, valor)
        VALUES ('whatsapp_soporte', '584125970507')
        """
    )
    for clave, valor in [
        ('pago_movil_banco', 'Banesco'),
        ('pago_movil_cedula', 'J-501234567'),
        ('pago_movil_telefono', '04125970507'),
    ]:
        cursor.execute(
            """
            INSERT OR IGNORE INTO configuracion_sistema (clave, valor)
            VALUES (?, ?)
            """,
            (clave, valor),
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS solicitudes_pago (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            comercio_id INTEGER NOT NULL,
            plan_tipo TEXT NOT NULL,
            referencia TEXT NOT NULL,
            fecha_transferencia TEXT NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (comercio_id) REFERENCES comercios(id) ON DELETE CASCADE
        )
        """
    )


def init_db():
    """
    Inicializa o actualiza la base de datos sin destruir datos existentes.
    No utiliza DROP TABLE IF EXISTS.
    """
    os.makedirs(os.path.dirname(DATABASE_FILE), exist_ok=True)

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute('PRAGMA foreign_keys = ON;')

            _ejecutar_schema_base(cursor)
            _crear_tabla_planes(cursor)
            _migrar_columnas_comercios(cursor)
            _asignar_plan_id_existentes(cursor)
            _crear_tabla_pagos(cursor)
            _crear_vista_tiendas(cursor)
            _sembrar_configuracion(cursor)
            _crear_indices(cursor)

            conexion.commit()
        return True
    except Exception as error:
        print(f'Error en init_db(): {error}')
        return False


if __name__ == '__main__':
    if init_db():
        print('Base de datos localis.db inicializada/actualizada correctamente.')
    else:
        print('No se pudo completar init_db().')
