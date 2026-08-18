"""Esquema PostgreSQL (Supabase), conexión vía psycopg2 e init_db()."""

import os

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = (os.getenv('DATABASE_URL') or '').strip()
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

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
    ('fecha_inicio_suscripcion', 'TIMESTAMP'),
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


def using_postgres():
    return bool(DATABASE_URL)


def _require_database_url():
    if not DATABASE_URL:
        raise RuntimeError(
            'DATABASE_URL no está configurada. '
            'Define la cadena de conexión PostgreSQL de Supabase en tu entorno.'
        )


def _adapt_sql(query):
    """Compatibilidad con consultas legacy que usaban placeholders de SQLite (?)."""
    return query.replace('?', '%s').replace("date('now')", 'CURRENT_DATE')


class _PgCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=()):
        if query.strip().upper().startswith('PRAGMA'):
            return self
        self._cursor.execute(_adapt_sql(query), params or None)
        return self

    def executemany(self, query, params_seq):
        self._cursor.executemany(_adapt_sql(query), params_seq)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _PgConnection:
    def __init__(self, conn, dict_rows=False):
        self._conn = conn
        self._dict_rows = dict_rows

    def cursor(self):
        if self._dict_rows:
            return _PgCursor(self._conn.cursor(cursor_factory=RealDictCursor))
        return _PgCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False


def get_db_connection(row_factory=None):
    """Abre conexión externa a PostgreSQL (Supabase) usando DATABASE_URL."""
    _require_database_url()
    conn = psycopg2.connect(DATABASE_URL)
    return _PgConnection(conn, dict_rows=row_factory is not None)


def _columnas_existentes(cursor, tabla):
    if tabla not in TABLAS_PERMITIDAS:
        raise ValueError(f'Tabla no permitida para introspección: {tabla}')
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (tabla,),
    )
    return {row[0] for row in cursor.fetchall()}


def _tabla_existe(cursor, tabla):
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        LIMIT 1
        """,
        (tabla,),
    )
    return cursor.fetchone() is not None


def _agregar_columna_si_falta(cursor, tabla, nombre, tipo_sql):
    existentes = _columnas_existentes(cursor, tabla)
    if nombre not in existentes:
        cursor.execute(f'ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo_sql}')


def _ejecutar_ddl(cursor, ddl):
    for statement in ddl.split(';'):
        sql = statement.strip()
        if sql:
            cursor.execute(sql)


def _ejecutar_schema_base(cursor):
    """Crea tablas base si aún no existen (PostgreSQL)."""
    if _tabla_existe(cursor, 'usuarios'):
        return

    ddl_base = """
    CREATE TABLE IF NOT EXISTS usuarios (
        id SERIAL PRIMARY KEY,
        nombre TEXT NOT NULL,
        correo TEXT UNIQUE NOT NULL,
        contrasena TEXT,
        foto_url TEXT,
        rol TEXT DEFAULT 'comerciante'
    );

    CREATE TABLE IF NOT EXISTS categorias (
        id SERIAL PRIMARY KEY,
        nombre TEXT UNIQUE NOT NULL
    );

    CREATE TABLE IF NOT EXISTS comercios (
        id SERIAL PRIMARY KEY,
        usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
        nombre TEXT NOT NULL,
        descripcion TEXT,
        telefono TEXT,
        documento_identidad TEXT,
        logo_url TEXT,
        banner_url TEXT,
        delivery INTEGER DEFAULT 0,
        direccion TEXT,
        ciudad TEXT,
        zona TEXT,
        maps_url TEXT,
        categoria_id INTEGER REFERENCES categorias(id) ON DELETE SET NULL,
        plan_id INTEGER,
        plan_tipo TEXT DEFAULT 'gratis',
        fecha_inicio_suscripcion TIMESTAMP,
        fecha_vencimiento TIMESTAMP,
        limite_productos INTEGER DEFAULT 50,
        estado_pago TEXT DEFAULT 'activo',
        fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        aviso_bienvenida_visto INTEGER DEFAULT 0,
        visible INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS sucursales (
        id SERIAL PRIMARY KEY,
        comercio_id INTEGER REFERENCES comercios(id) ON DELETE CASCADE,
        direccion TEXT NOT NULL,
        coordinates_maps TEXT
    );

    CREATE TABLE IF NOT EXISTS productos (
        id SERIAL PRIMARY KEY,
        comercio_id INTEGER REFERENCES comercios(id) ON DELETE CASCADE,
        nombre TEXT NOT NULL,
        precio_usd DOUBLE PRECISION NOT NULL,
        descripcion TEXT,
        imagen_url TEXT,
        stock INTEGER DEFAULT 0,
        codigo_barras TEXT
    );

    CREATE TABLE IF NOT EXISTS soporte_y_reportes (
        id SERIAL PRIMARY KEY,
        usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        tipo TEXT NOT NULL,
        correo TEXT NOT NULL,
        mensaje TEXT NOT NULL,
        referencia_id INTEGER,
        fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        estado TEXT DEFAULT 'pendiente'
    );

    CREATE TABLE IF NOT EXISTS configuracion_sistema (
        clave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS logs_auditoria (
        id SERIAL PRIMARY KEY,
        usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        accion TEXT NOT NULL,
        detalles TEXT,
        fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS intentos_login (
        id SERIAL PRIMARY KEY,
        correo_intentado TEXT NOT NULL,
        ip_direccion TEXT DEFAULT '127.0.0.1',
        intentos INTEGER DEFAULT 1,
        fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    _ejecutar_ddl(cursor, ddl_base)

    for nombre in ('Alimentos', 'Ropa', 'Tecnología', 'Otros'):
        cursor.execute(
            """
            INSERT INTO categorias (nombre)
            VALUES (%s)
            ON CONFLICT (nombre) DO NOTHING
            """,
            (nombre,),
        )


def _crear_tabla_planes(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS planes (
            id SERIAL PRIMARY KEY,
            codigo TEXT UNIQUE,
            nombre TEXT UNIQUE NOT NULL,
            precio DOUBLE PRECISION NOT NULL,
            limite_productos INTEGER,
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
            INSERT INTO planes
            (codigo, nombre, precio, limite_productos, soporte_prioritario,
             dias_duracion, destacado, activo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (codigo) DO NOTHING
            """,
            fila,
        )


def _crear_tabla_pagos(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pagos (
            id SERIAL PRIMARY KEY,
            tienda_id INTEGER NOT NULL REFERENCES comercios(id),
            plan_id INTEGER NOT NULL REFERENCES planes(id),
            monto DOUBLE PRECISION NOT NULL,
            metodo TEXT NOT NULL,
            referencia TEXT,
            banco_origen TEXT,
            cedula_pagador TEXT,
            telefono_pagador TEXT,
            estado TEXT DEFAULT 'pendiente',
            fecha_pago TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _crear_vista_tiendas(cursor):
    cursor.execute(
        """
        CREATE OR REPLACE VIEW tiendas AS
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
            'UPDATE comercios SET plan_id = %s WHERE id = %s',
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
    defaults = [
        ('tasa_dolar', '36.50'),
        ('banner_principal', '/static/images/default-banner.jpg'),
        ('whatsapp_soporte', '584125970507'),
        ('pago_movil_banco', 'Banesco'),
        ('pago_movil_cedula', 'J-501234567'),
        ('pago_movil_telefono', '04125970507'),
    ]
    for clave, valor in defaults:
        cursor.execute(
            """
            INSERT INTO configuracion_sistema (clave, valor)
            VALUES (%s, %s)
            ON CONFLICT (clave) DO NOTHING
            """,
            (clave, valor),
        )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS solicitudes_pago (
            id SERIAL PRIMARY KEY,
            comercio_id INTEGER NOT NULL REFERENCES comercios(id) ON DELETE CASCADE,
            plan_tipo TEXT NOT NULL,
            referencia TEXT NOT NULL,
            fecha_transferencia TEXT NOT NULL,
            estado TEXT DEFAULT 'pendiente',
            fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def init_db():
    """
    Inicializa o actualiza la base PostgreSQL en Supabase sin destruir datos.
    Requiere DATABASE_URL en el entorno.
    """
    _require_database_url()

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

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
        print('Base de datos PostgreSQL (Supabase) inicializada/actualizada correctamente.')
    else:
        print('No se pudo completar init_db().')
