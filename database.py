import os
import re
import time
from datetime import date, datetime, time as time_of_day
from decimal import Decimal
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import UUID

import psycopg2
from psycopg2 import OperationalError, pool
from psycopg2.extensions import TRANSACTION_STATUS_INERROR
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError

# Compatibilidad: sustituye sqlite3.Row en get_db_connection(row_factory=...).
ROW_AS_DICT = object()

# Fragmentos SQL PostgreSQL reutilizables (intervalos y fechas seguras).
SQL_NOW = 'CURRENT_TIMESTAMP'
SQL_TODAY = 'CURRENT_DATE'
SQL_INTERVAL_DAYS = "INTERVAL '1 day' * %s"
SQL_INTERVAL_MONTHS = "INTERVAL '1 month' * %s"
SQL_VENCIMIENTO_30_DIAS = "CURRENT_TIMESTAMP + INTERVAL '30 days'"
SQL_FECHA_VENCIDA = 'CAST(%s AS DATE) < CURRENT_DATE'

DB_CONNECT_TIMEOUT = int(os.getenv('DB_CONNECT_TIMEOUT', '10'))
DB_POOL_MIN = int(os.getenv('DB_POOL_MIN', '2'))
DB_POOL_MAX = int(os.getenv('DB_POOL_MAX', '20'))
DB_STATEMENT_TIMEOUT_MS = int(os.getenv('DB_STATEMENT_TIMEOUT_MS', '120000'))
DB_RETRY_ATTEMPTS = int(os.getenv('DB_RETRY_ATTEMPTS', '3'))
DB_RETRY_BASE_DELAY = float(os.getenv('DB_RETRY_BASE_DELAY', '0.08'))

_connection_pool = None

# Parámetros válidos en URIs libpq/psycopg2. El resto (p. ej. pgbouncer=true) se descarta.
_PARAMS_URI_PERMITIDOS = frozenset({
    'application_name',
    'channel_binding',
    'connect_timeout',
    'gssencmode',
    'krbsrvname',
    'options',
    'service',
    'sslcert',
    'sslcrl',
    'sslkey',
    'sslmode',
    'sslrootcert',
    'target_session_attrs',
})


def normalize_database_url(url):
    """
    Normaliza DATABASE_URL para psycopg2/SQLAlchemy:
    - Convierte postgres:// → postgresql://
    - Elimina parámetros de consulta no soportados (?pgbouncer=true, etc.)
    """
    valor = (url or '').strip()
    if not valor:
        return ''

    if valor.startswith('postgres://'):
        valor = valor.replace('postgres://', 'postgresql://', 1)

    parsed = urlparse(valor)
    if not parsed.scheme.startswith('postgres'):
        return valor

    if not parsed.query:
        return valor

    params_limpios = []
    for clave, param_valor in parse_qsl(parsed.query, keep_blank_values=True):
        if clave.lower() in _PARAMS_URI_PERMITIDOS:
            params_limpios.append((clave, param_valor))

    query_limpia = urlencode(params_limpios)
    return urlunparse(parsed._replace(query=query_limpia))


DATABASE_URL = normalize_database_url(os.getenv('DATABASE_URL'))
DATABASE_KEY = (os.getenv('DATABASE_KEY') or '').strip()

_IDENTIFICADOR_SQL = re.compile(r'^[a-z_][a-z0-9_]*$')

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

# Columnas que deben existir en tablas ya creadas (ADD COLUMN IF NOT EXISTS).
# No incluye id SERIAL: las tablas nuevas lo traen en CREATE TABLE.
COLUMNAS_ESQUEMA = {
    'usuarios': [
        ('nombre', 'TEXT'),
        ('correo', 'TEXT'),
        ('contrasena', 'TEXT'),
        ('foto_url', 'TEXT'),
        ('rol', "TEXT DEFAULT 'comerciante'"),
    ],
    'categorias': [
        ('nombre', 'TEXT'),
    ],
    'planes': [
        ('codigo', 'TEXT'),
        ('nombre', 'TEXT'),
        ('precio', 'DOUBLE PRECISION DEFAULT 0'),
        ('limite_productos', 'INTEGER'),
        ('soporte_prioritario', 'INTEGER DEFAULT 0'),
        ('dias_duracion', 'INTEGER DEFAULT 30'),
        ('destacado', 'INTEGER DEFAULT 0'),
        ('activo', 'INTEGER DEFAULT 1'),
    ],
    'comercios': [
        ('usuario_id', 'INTEGER'),
        ('nombre', 'TEXT'),
        ('descripcion', 'TEXT'),
        ('telefono', 'TEXT'),
        ('documento_identidad', 'TEXT'),
        ('logo_url', 'TEXT'),
        ('banner_url', 'TEXT'),
        ('direccion', 'TEXT'),
        ('ciudad', 'TEXT'),
        ('zona', 'TEXT'),
        ('maps_url', 'TEXT'),
        ('ubicacion_maps_url', 'TEXT'),
        ('categoria_id', 'INTEGER'),
        ('plan_id', 'INTEGER DEFAULT 1'),
        ('plan_tipo', "TEXT DEFAULT 'gratis'"),
        ('fecha_inicio_suscripcion', 'TIMESTAMP'),
        ('fecha_vencimiento', 'TIMESTAMP'),
        ('limite_productos', 'INTEGER DEFAULT 50'),
        ('estado_pago', "TEXT DEFAULT 'activo'"),
        ('fecha_registro', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
        ('aviso_bienvenida_visto', 'INTEGER DEFAULT 0'),
        ('imagen_portada', 'TEXT'),
        ('visible', 'INTEGER DEFAULT 1'),
        ('plan_pendiente', 'TEXT'),
        ('plan_id_pendiente', 'INTEGER'),
    ],
    'sucursales': [
        ('comercio_id', 'INTEGER'),
        ('direccion', 'TEXT'),
        ('coordinates_maps', 'TEXT'),
    ],
    'productos': [
        ('comercio_id', 'INTEGER'),
        ('nombre', 'TEXT'),
        ('precio_usd', 'DOUBLE PRECISION'),
        ('descripcion', 'TEXT'),
        ('imagen_url', 'TEXT'),
        ('stock', 'INTEGER DEFAULT 0'),
        ('codigo_barras', 'TEXT'),
    ],
    'soporte_y_reportes': [
        ('usuario_id', 'INTEGER'),
        ('tipo', 'TEXT'),
        ('correo', 'TEXT'),
        ('mensaje', 'TEXT'),
        ('referencia_id', 'INTEGER'),
        ('fecha', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
        ('estado', "TEXT DEFAULT 'pendiente'"),
    ],
    'configuracion_sistema': [
        ('clave', 'TEXT'),
        ('valor', 'TEXT'),
    ],
    'logs_auditoria': [
        ('usuario_id', 'INTEGER'),
        ('accion', 'TEXT'),
        ('detalles', 'TEXT'),
        ('fecha', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
    ],
    'intentos_login': [
        ('correo_intentado', 'TEXT'),
        ('ip_direccion', "TEXT DEFAULT '127.0.0.1'"),
        ('intentos', 'INTEGER DEFAULT 1'),
        ('fecha', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
    ],
    'pagos': [
        ('tienda_id', 'INTEGER'),
        ('plan_id', 'INTEGER'),
        ('monto', 'DOUBLE PRECISION'),
        ('metodo', 'TEXT'),
        ('referencia', 'TEXT'),
        ('banco_origen', 'TEXT'),
        ('cedula_pagador', 'TEXT'),
        ('telefono_pagador', 'TEXT'),
        ('estado', "TEXT DEFAULT 'pendiente'"),
        ('fecha_pago', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
    ],
    'solicitudes_pago': [
        ('comercio_id', 'INTEGER'),
        ('plan_tipo', 'TEXT'),
        ('referencia', 'TEXT'),
        ('fecha_transferencia', 'TEXT'),
        ('estado', "TEXT DEFAULT 'pendiente'"),
        ('fecha_registro', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP'),
    ],
}

CLAVES_FORANEAS = (
    (
        'comercios',
        'comercios_usuario_id_fkey',
        'FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE',
    ),
    (
        'comercios',
        'comercios_categoria_id_fkey',
        'FOREIGN KEY (categoria_id) REFERENCES categorias(id) ON DELETE SET NULL',
    ),
    (
        'comercios',
        'comercios_plan_id_fkey',
        'FOREIGN KEY (plan_id) REFERENCES planes(id) ON DELETE SET NULL',
    ),
    (
        'sucursales',
        'sucursales_comercio_id_fkey',
        'FOREIGN KEY (comercio_id) REFERENCES comercios(id) ON DELETE CASCADE',
    ),
    (
        'productos',
        'productos_comercio_id_fkey',
        'FOREIGN KEY (comercio_id) REFERENCES comercios(id) ON DELETE CASCADE',
    ),
    (
        'soporte_y_reportes',
        'soporte_y_reportes_usuario_id_fkey',
        'FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL',
    ),
    (
        'logs_auditoria',
        'logs_auditoria_usuario_id_fkey',
        'FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL',
    ),
    (
        'pagos',
        'pagos_tienda_id_fkey',
        'FOREIGN KEY (tienda_id) REFERENCES comercios(id)',
    ),
    (
        'pagos',
        'pagos_plan_id_fkey',
        'FOREIGN KEY (plan_id) REFERENCES planes(id)',
    ),
    (
        'solicitudes_pago',
        'solicitudes_pago_comercio_id_fkey',
        'FOREIGN KEY (comercio_id) REFERENCES comercios(id) ON DELETE CASCADE',
    ),
)

PLANES_SEED = [
    ('gratis', 'Plan Gratis / Prueba', 0, 50, 0, 30, 0, 1),
    ('basica', 'Plan Básico', 10, 100, 0, 30, 0, 1),
    ('pro', 'Pro', 15, 300, 1, 30, 1, 1),
    ('business', 'Business', 35, None, 1, 30, 0, 1),
]


def using_postgres():
    return bool(DATABASE_URL)


def _require_database_url():
    if not DATABASE_URL:
        raise RuntimeError(
            'DATABASE_URL no está configurada. '
            'Define la cadena de conexión PostgreSQL en tu entorno.'
        )
    if not DATABASE_KEY:
        raise RuntimeError(
            'DATABASE_KEY no está configurada. '
            'Define la clave de acceso a la base de datos en tu entorno.'
        )


def _adapt_sql(query):
    """Adapta consultas legacy de SQLite a PostgreSQL de forma segura."""
    sql = query.replace('?', '%s')
    sql = sql.replace('ON CONFLICT(', 'ON CONFLICT (')
    sql = sql.replace('excluded.', 'EXCLUDED.')
    sql = re.sub(
        r"datetime\s*\(\s*['\"]now['\"]\s*(?:,\s*['\"][^'\"]*['\"])?\s*\)",
        'CURRENT_TIMESTAMP',
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"date\s*\(\s*['\"]now['\"]\s*\)",
        'CURRENT_DATE',
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"strftime\s*\(\s*['\"]%Y-%m-%d['\"]\s*,",
        "TO_CHAR(",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


def _valor_python(valor):
    """Convierte tipos nativos de PostgreSQL a valores planos para la app."""
    if valor is None:
        return None
    if isinstance(valor, bool):
        return 1 if valor else 0
    if isinstance(valor, datetime):
        if (
            valor.hour == 0
            and valor.minute == 0
            and valor.second == 0
            and valor.microsecond == 0
        ):
            return valor.strftime('%Y-%m-%d')
        return valor.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(valor, date):
        return valor.strftime('%Y-%m-%d')
    if isinstance(valor, time_of_day):
        return valor.strftime('%H:%M:%S')
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, UUID):
        return str(valor)
    if isinstance(valor, float):
        if valor != valor:  # NaN
            return None
        return valor
    if isinstance(valor, (bytes, bytearray, memoryview)):
        data = bytes(valor)
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return data
    if isinstance(valor, (list, dict)):
        return valor
    return valor


def normalizar_valor_columna(valor):
    """API pública: un solo valor PostgreSQL → Python plano."""
    return _valor_python(valor)


def normalizar_fila(fila):
    """API pública: fila de cursor → dict o tupla con tipos planos."""
    return _normalizar_fila(fila)


def normalizar_filas(filas):
    """API pública: lista de filas normalizadas."""
    if not filas:
        return []
    return [_normalizar_fila(fila) for fila in filas]


def _normalizar_fila(fila):
    """Dict o tupla con tipos ya adaptados para plantillas, JSON y [.get]/[:]."""
    if fila is None:
        return None
    if isinstance(fila, dict):
        return {clave: _valor_python(valor) for clave, valor in fila.items()}
    return tuple(_valor_python(valor) for valor in fila)


def fila_a_dict(fila):
    """Normaliza una fila de cursor a dict plano (o None)."""
    normalizada = _normalizar_fila(fila)
    if normalizada is None:
        return None
    if isinstance(normalizada, dict):
        return normalizada
    return None


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
        return _normalizar_fila(self._cursor.fetchone())

    def fetchall(self):
        return [_normalizar_fila(fila) for fila in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        """En PostgreSQL lastrowid no aplica a SERIAL; usar INSERT ... RETURNING id."""
        return getattr(self._cursor, 'lastrowid', None) or None


class _PgConnection:
    def __init__(self, conn, dict_rows=False, from_pool=False):
        self._conn = conn
        self._dict_rows = dict_rows
        self._from_pool = from_pool
        self._closed = False

    def cursor(self):
        if self._dict_rows:
            return _PgCursor(self._conn.cursor(cursor_factory=RealDictCursor))
        return _PgCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._conn.get_transaction_status() == TRANSACTION_STATUS_INERROR:
                self._conn.rollback()
        except Exception:
            pass
        try:
            if self._from_pool and _connection_pool is not None:
                _connection_pool.putconn(self._conn)
            else:
                self._conn.close()
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type:
                self.rollback()
            else:
                self.commit()
        finally:
            self.close()
        return False


def _obtener_pool():
    global _connection_pool
    if _connection_pool is None:
        _require_database_url()
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=DB_POOL_MIN,
            maxconn=DB_POOL_MAX,
            dsn=DATABASE_URL,
            connect_timeout=DB_CONNECT_TIMEOUT,
        )
    return _connection_pool


def _preparar_conexion_pg(pg_conn):
    pg_conn.autocommit = False
    if DB_STATEMENT_TIMEOUT_MS > 0:
        with pg_conn.cursor() as cur:
            cur.execute('SET statement_timeout = %s', (DB_STATEMENT_TIMEOUT_MS,))


def es_error_bd_transitorio(exc):
    if not isinstance(exc, OperationalError):
        return False
    pgcode = getattr(exc, 'pgcode', None)
    if pgcode in ('40001', '40P01', '55P03', '57014'):
        return True
    mensaje = str(exc).lower()
    return any(
        token in mensaje
        for token in (
            'deadlock',
            'lock timeout',
            'could not serialize',
            'connection reset',
            'server closed the connection',
        )
    )


def get_db_connection(row_factory=None):
    """
    Conexión del pool PostgreSQL. row_factory truthy (p. ej. sqlite3.Row o ROW_AS_DICT)
    devuelve filas como diccionarios planos con tipos ya normalizados.
    """
    _require_database_url()
    try:
        pg_conn = _obtener_pool().getconn()
    except PoolError as exc:
        raise RuntimeError(
            'No hay conexiones de base de datos disponibles en este momento. '
            'Intenta de nuevo en unos segundos.'
        ) from exc
    _preparar_conexion_pg(pg_conn)
    usar_dict = row_factory is not None
    return _PgConnection(pg_conn, dict_rows=usar_dict, from_pool=True)


def consultar_uno(query, params=(), row_factory=ROW_AS_DICT):
    """Ejecuta SELECT y retorna un dict plano o None."""
    with get_db_connection(row_factory=row_factory) as conexion:
        cursor = conexion.cursor()
        cursor.execute(query, params)
        fila = cursor.fetchone()
        if fila is None:
            return None
        if isinstance(fila, dict):
            return fila
        return fila_a_dict(fila) if row_factory is not None else fila


def consultar_todos(query, params=(), row_factory=ROW_AS_DICT):
    """Ejecuta SELECT y retorna lista de dicts planos."""
    with get_db_connection(row_factory=row_factory) as conexion:
        cursor = conexion.cursor()
        cursor.execute(query, params)
        filas = cursor.fetchall()
        if not filas:
            return []
        if isinstance(filas[0], dict):
            return filas
        return [fila_a_dict(f) for f in filas] if row_factory is not None else filas


def ejecutar_escritura(query, params=()):
    """INSERT/UPDATE/DELETE con commit explícito. Retorna filas afectadas."""
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(query, params)
        afectadas = cursor.rowcount
        conexion.commit()
        return afectadas


def diagnosticar_postgresql():
    """Diagnóstico automático de conexión, latencia y tablas críticas."""
    resultado = {
        'ok': False,
        'database_url_configurada': bool(DATABASE_URL),
        'latencia_ms': None,
        'version': None,
        'tablas_criticas': {},
        'pool_min': DB_POOL_MIN,
        'pool_max': DB_POOL_MAX,
        'error': None,
    }
    if not DATABASE_URL:
        resultado['error'] = 'DATABASE_URL no configurada.'
        return resultado

    tablas_criticas = ('usuarios', 'comercios', 'productos', 'planes', 'categorias')
    inicio = time.time()
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute('SELECT version()')
            fila_version = cursor.fetchone()
            if fila_version:
                resultado['version'] = (
                    fila_version[0] if isinstance(fila_version, tuple) else fila_version.get('version')
                )
            for tabla in tablas_criticas:
                cursor.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = %s
                    )
                    """,
                    (tabla,),
                )
                existe = cursor.fetchone()
                if isinstance(existe, dict):
                    resultado['tablas_criticas'][tabla] = bool(
                        next(iter(existe.values()), False)
                    )
                elif isinstance(existe, tuple):
                    resultado['tablas_criticas'][tabla] = bool(existe[0])
                else:
                    resultado['tablas_criticas'][tabla] = False
            conexion.rollback()
        resultado['latencia_ms'] = round((time.time() - inicio) * 1000, 1)
        resultado['ok'] = all(resultado['tablas_criticas'].values())
    except Exception as error:
        resultado['error'] = str(error)
        resultado['latencia_ms'] = round((time.time() - inicio) * 1000, 1)

    return resultado


def ejecutar_con_reintentos_bd(operacion, reintentos=None):
    """
    Ejecuta operacion(conexion) con reintentos ante bloqueos/deadlocks transitorios.
    operacion debe hacer commit explícito si corresponde; la conexión siempre se cierra.
    """
    intentos = reintentos if reintentos is not None else DB_RETRY_ATTEMPTS
    ultimo_error = None

    for intento in range(intentos):
        conexion = get_db_connection()
        try:
            resultado = operacion(conexion)
            conexion.commit()
            return resultado
        except Exception as exc:
            ultimo_error = exc
            try:
                conexion.rollback()
            except Exception:
                pass
            if intento < intentos - 1 and es_error_bd_transitorio(exc):
                time.sleep(DB_RETRY_BASE_DELAY * (2 ** intento))
                continue
            raise
        finally:
            conexion.close()

    if ultimo_error:
        raise ultimo_error
    return None


def _validar_identificador(nombre):
    if not _IDENTIFICADOR_SQL.match(nombre or ''):
        raise ValueError(f'Identificador SQL no permitido: {nombre}')
    return nombre


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


def _ejecutar_ddl_seguro(cursor, sql):
    """Ejecuta DDL con savepoint para no abortar init_db si el objeto ya existe."""
    cursor.execute('SAVEPOINT ddl_seguro')
    try:
        cursor.execute(sql)
        cursor.execute('RELEASE SAVEPOINT ddl_seguro')
        return True
    except Exception as error:
        cursor.execute('ROLLBACK TO SAVEPOINT ddl_seguro')
        print(f'Aviso DDL: {error}')
        return False


def _agregar_columna_si_falta(cursor, tabla, nombre, tipo_sql):
    _validar_identificador(tabla)
    _validar_identificador(nombre)
    if tabla not in TABLAS_PERMITIDAS:
        raise ValueError(f'Tabla no permitida: {tabla}')
    cursor.execute(
        f'ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {nombre} {tipo_sql}'
    )


def _migrar_columnas(cursor):
    for tabla, columnas in COLUMNAS_ESQUEMA.items():
        if not _tabla_existe(cursor, tabla):
            continue
        for nombre, tipo_sql in columnas:
            _agregar_columna_si_falta(cursor, tabla, nombre, tipo_sql)


def _restriccion_existe(cursor, nombre):
    cursor.execute(
        """
        SELECT 1
        FROM pg_constraint
        WHERE conname = %s
        LIMIT 1
        """,
        (nombre,),
    )
    return cursor.fetchone() is not None


def _asegurar_claves_foraneas(cursor):
    for tabla, constraint, definicion in CLAVES_FORANEAS:
        if not _tabla_existe(cursor, tabla):
            continue
        if _restriccion_existe(cursor, constraint):
            continue
        _validar_identificador(tabla)
        _validar_identificador(constraint)
        _ejecutar_ddl_seguro(
            cursor,
            f'ALTER TABLE {tabla} ADD CONSTRAINT {constraint} {definicion} NOT VALID',
        )


def _crear_tabla_usuarios(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            correo TEXT UNIQUE NOT NULL,
            contrasena TEXT,
            foto_url TEXT,
            rol TEXT DEFAULT 'comerciante'
        )
        """
    )


def _crear_tabla_categorias(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS categorias (
            id SERIAL PRIMARY KEY,
            nombre TEXT UNIQUE NOT NULL
        )
        """
    )


def _crear_tabla_planes(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS planes (
            id SERIAL PRIMARY KEY,
            codigo TEXT UNIQUE,
            nombre TEXT UNIQUE NOT NULL,
            precio DOUBLE PRECISION NOT NULL DEFAULT 0,
            limite_productos INTEGER,
            soporte_prioritario INTEGER DEFAULT 0,
            dias_duracion INTEGER DEFAULT 30,
            destacado INTEGER DEFAULT 0,
            activo INTEGER DEFAULT 1
        )
        """
    )


def _crear_tabla_comercios(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS comercios (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            telefono TEXT,
            documento_identidad TEXT,
            logo_url TEXT,
            banner_url TEXT,
            direccion TEXT,
            ciudad TEXT,
            zona TEXT,
            maps_url TEXT,
            ubicacion_maps_url TEXT,
            categoria_id INTEGER REFERENCES categorias(id) ON DELETE SET NULL,
            plan_id INTEGER REFERENCES planes(id) ON DELETE SET NULL,
            plan_tipo TEXT DEFAULT 'gratis',
            fecha_inicio_suscripcion TIMESTAMP,
            fecha_vencimiento TIMESTAMP,
            limite_productos INTEGER DEFAULT 50,
            estado_pago TEXT DEFAULT 'activo',
            fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            aviso_bienvenida_visto INTEGER DEFAULT 0,
            imagen_portada TEXT,
            visible INTEGER DEFAULT 1
        )
        """
    )


def _crear_tabla_sucursales(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sucursales (
            id SERIAL PRIMARY KEY,
            comercio_id INTEGER REFERENCES comercios(id) ON DELETE CASCADE,
            direccion TEXT NOT NULL,
            coordinates_maps TEXT
        )
        """
    )


def _crear_tabla_productos(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS productos (
            id SERIAL PRIMARY KEY,
            comercio_id INTEGER REFERENCES comercios(id) ON DELETE CASCADE,
            nombre TEXT NOT NULL,
            precio_usd DOUBLE PRECISION NOT NULL,
            descripcion TEXT,
            imagen_url TEXT,
            stock INTEGER DEFAULT 0,
            codigo_barras TEXT
        )
        """
    )


def _crear_tabla_catalogo_maestro_imagenes(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS catalogo_maestro_imagenes (
            codigo_barras TEXT PRIMARY KEY,
            url_imagen TEXT NOT NULL,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )


def _crear_tabla_soporte_y_reportes(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS soporte_y_reportes (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            tipo TEXT NOT NULL,
            correo TEXT NOT NULL,
            mensaje TEXT NOT NULL,
            referencia_id INTEGER,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            estado TEXT DEFAULT 'pendiente'
        )
        """
    )


def _crear_tabla_configuracion_sistema(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS configuracion_sistema (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        )
        """
    )


def _crear_tabla_logs_auditoria(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS logs_auditoria (
            id SERIAL PRIMARY KEY,
            usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            accion TEXT NOT NULL,
            detalles TEXT,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _crear_tabla_intentos_login(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS intentos_login (
            id SERIAL PRIMARY KEY,
            correo_intentado TEXT NOT NULL,
            ip_direccion TEXT DEFAULT '127.0.0.1',
            intentos INTEGER DEFAULT 1,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
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


def _crear_tabla_solicitudes_pago(cursor):
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


def _crear_tablas(cursor):
    """Crea todas las tablas de la aplicación. El orden respeta las FKs."""
    _crear_tabla_usuarios(cursor)
    _crear_tabla_categorias(cursor)
    _crear_tabla_planes(cursor)
    _crear_tabla_comercios(cursor)
    _crear_tabla_sucursales(cursor)
    _crear_tabla_productos(cursor)
    _crear_tabla_catalogo_maestro_imagenes(cursor)
    _crear_tabla_soporte_y_reportes(cursor)
    _crear_tabla_configuracion_sistema(cursor)
    _crear_tabla_logs_auditoria(cursor)
    _crear_tabla_intentos_login(cursor)
    _crear_tabla_pagos(cursor)
    _crear_tabla_solicitudes_pago(cursor)


def _asegurar_indices_unicos(cursor):
    indices = [
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_correo ON usuarios(correo)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_categorias_nombre ON categorias(nombre)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_planes_codigo ON planes(codigo)',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_catalogo_maestro_codigo ON catalogo_maestro_imagenes(codigo_barras)',
    ]
    for ddl in indices:
        _ejecutar_ddl_seguro(cursor, ddl)


def _sembrar_categorias(cursor):
    for nombre in ('Alimentos', 'Ropa', 'Tecnología', 'Otros'):
        cursor.execute(
            """
            INSERT INTO categorias (nombre)
            VALUES (%s)
            ON CONFLICT (nombre) DO NOTHING
            """,
            (nombre,),
        )


def _sembrar_planes(cursor):
    for fila in PLANES_SEED:
        cursor.execute(
            """
            INSERT INTO planes
            (codigo, nombre, precio, limite_productos, soporte_prioritario,
             dias_duracion, destacado, activo)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (codigo) DO UPDATE SET
                nombre = EXCLUDED.nombre,
                precio = EXCLUDED.precio,
                limite_productos = EXCLUDED.limite_productos,
                soporte_prioritario = EXCLUDED.soporte_prioritario,
                dias_duracion = EXCLUDED.dias_duracion,
                destacado = EXCLUDED.destacado,
                activo = EXCLUDED.activo
            """,
            fila,
        )


def _crear_vista_tiendas(cursor):
    if not _tabla_existe(cursor, 'comercios'):
        return
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


def _backfill_comercios(cursor):
    if not _tabla_existe(cursor, 'comercios'):
        return

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
        SET visible = 1
        WHERE visible IS NULL
        """
    )


def _asignar_plan_id_existentes(cursor):
    if not _tabla_existe(cursor, 'comercios') or not _tabla_existe(cursor, 'planes'):
        return

    cursor.execute('SELECT id, codigo FROM planes')
    mapa = {row[1]: row[0] for row in cursor.fetchall() if row[1]}
    plan_gratis_id = mapa.get('gratis')
    if not plan_gratis_id and mapa:
        plan_gratis_id = next(iter(mapa.values()))

    cursor.execute('SELECT id, plan_tipo, plan_id FROM comercios')
    for comercio_id, plan_tipo, plan_id in cursor.fetchall():
        if plan_id:
            continue
        codigo = (plan_tipo or 'gratis').lower()
        if codigo not in mapa:
            codigo = 'gratis'
        nuevo_plan = mapa.get(codigo, plan_gratis_id)
        if not nuevo_plan:
            continue
        cursor.execute(
            'UPDATE comercios SET plan_id = %s WHERE id = %s',
            (nuevo_plan, comercio_id),
        )


def _crear_indices(cursor):
    indices = [
        'CREATE INDEX IF NOT EXISTS idx_productos_comercio_id ON productos(comercio_id)',
        'CREATE INDEX IF NOT EXISTS idx_productos_comercio_id_desc ON productos(comercio_id, id DESC)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_publicos ON comercios(visible, estado_pago)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_vencimiento ON comercios(fecha_vencimiento)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_plan_pendiente ON comercios(plan_pendiente) WHERE plan_pendiente IS NOT NULL',
        'CREATE INDEX IF NOT EXISTS idx_tiendas_estado ON comercios(estado_pago, plan_id)',
        'CREATE INDEX IF NOT EXISTS idx_pagos_tienda ON pagos(tienda_id)',
        'CREATE INDEX IF NOT EXISTS idx_pagos_referencia ON pagos(referencia)',
        'CREATE INDEX IF NOT EXISTS idx_pagos_estado ON pagos(estado)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_estado_plan ON comercios(estado_pago, plan_tipo)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_plan_id ON comercios(plan_id)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_ciudad ON comercios(ciudad)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_usuario_id ON comercios(usuario_id)',
        'CREATE INDEX IF NOT EXISTS idx_intentos_correo ON intentos_login(correo_intentado)',
        'CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos(nombre)',
        'CREATE INDEX IF NOT EXISTS idx_productos_codigo_barras ON productos(codigo_barras)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_nombre ON comercios(nombre)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_categoria ON comercios(categoria_id)',
        'CREATE INDEX IF NOT EXISTS idx_comercios_visible ON comercios(visible)',
        'CREATE INDEX IF NOT EXISTS idx_soporte_estado ON soporte_y_reportes(estado)',
        'CREATE INDEX IF NOT EXISTS idx_solicitudes_pago_comercio ON solicitudes_pago(comercio_id)',
        'CREATE INDEX IF NOT EXISTS idx_solicitudes_pago_referencia ON solicitudes_pago(referencia)',
        'CREATE INDEX IF NOT EXISTS idx_solicitudes_pago_estado ON solicitudes_pago(estado)',
    ]
    for ddl in indices:
        _ejecutar_ddl_seguro(cursor, ddl)


def _sembrar_configuracion(cursor):
    from config import DEFAULT_BANNER_URL

    defaults = [
        ('tasa_dolar', '36.50'),
        ('banner_principal', DEFAULT_BANNER_URL),
        ('whatsapp_soporte', '584125970507'),
        ('pago_movil_banco', 'Banco Caribe'),
        ('pago_movil_cedula', '30209716'),
        ('pago_movil_telefono', '04127957989'),
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
    _corregir_banner_principal_legacy(cursor)


def _corregir_banner_principal_legacy(cursor):
    """Reemplaza rutas /static/images/... inexistentes por el banner HTTPS por defecto."""
    from config import DEFAULT_BANNER_URL

    cursor.execute(
        """
        UPDATE configuracion_sistema
        SET valor = %s
        WHERE clave = 'banner_principal'
          AND (
            valor LIKE '/static/images/%%'
            OR TRIM(COALESCE(valor, '')) = ''
          )
        """,
        (DEFAULT_BANNER_URL,),
    )


def init_db():
    """
    Inicializa o actualiza la base PostgreSQL sin destruir datos.
    Crea tablas faltantes, añade columnas ausentes y asegura FKs e índices.
    """
    _require_database_url()

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            _crear_tablas(cursor)
            _migrar_columnas(cursor)
            _asegurar_indices_unicos(cursor)
            _sembrar_categorias(cursor)
            _sembrar_planes(cursor)
            _backfill_comercios(cursor)
            _asignar_plan_id_existentes(cursor)
            _asegurar_claves_foraneas(cursor)
            _crear_vista_tiendas(cursor)
            _sembrar_configuracion(cursor)
            _crear_indices(cursor)

            conexion.commit()
        from backend.catalogo_maestro import sembrar_catalogo_maestro_imagenes

        sembrar_catalogo_maestro_imagenes()
        return True
    except Exception as error:
        print(f'Error en init_db(): {error}')
        return False


if __name__ == '__main__':
    if init_db():
        print('Base de datos PostgreSQL inicializada/actualizada correctamente.')
    else:
        print('No se pudo completar init_db().')
