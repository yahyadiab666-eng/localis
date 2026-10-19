"""Generador de códigos públicos visibles (USR-/COM-/PROD-).

Los IDs de la API/DB son ``SERIAL`` (PK interna) y presentan saltos naturales
por upserts y transacciones fallidas. Para no exponer esos huecos al cliente se
asigna un **código público** independiente de la PK, con su propio contador
monotónico en la tabla ``contadores_publicos``.

Decisiones de diseño (seguridad en producción):

- **Nunca toca la PK** ni las relaciones: solo una columna ``codigo_publico``.
- **Sin huecos visibles**: el contador se incrementa con un ``UPDATE ... RETURNING``
  atómico (bloqueo de fila), no con ``setval`` de secuencias.
- **Aislamiento transaccional**: cada asignación usa un ``SAVEPOINT``; si algo
  falla (columna ausente, error de BD) se hace ``ROLLBACK TO SAVEPOINT`` y se
  continúa sin abortar la transacción del llamador (crítico en PostgreSQL).
- **Idempotente**: solo asigna donde ``codigo_publico`` está nulo o vacío.

Las funciones devuelven ``None``/``0`` ante error (degradación segura) y se
invocan desde los puntos de inserción y desde el backfill de ``init_db``.
"""

# tabla -> (clave del contador, prefijo, ancho de ceros)
CONFIG_CODIGOS = {
    'usuarios': ('usuarios', 'USR', 4),
    'comercios': ('comercios', 'COM', 4),
    'productos': ('productos', 'PROD', 5),
}

LOG_PREFIX = '[Localis Codigos]'


def _valor_fila(fila, clave, indice=0):
    """Lee una columna de fila dict (PostgreSQL) o tupla (sin row_factory)."""
    if fila is None:
        return None
    if isinstance(fila, dict):
        return fila.get(clave)
    return fila[indice]


def formatear_codigo(prefijo, valor, ancho):
    """Devuelve ``PREFIJO-0000`` con ceros a la izquierda."""
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return f'{prefijo}-{numero:0{int(ancho)}d}'


def _siguiente_valor(cursor, clave):
    """Incrementa y devuelve el contador de forma atómica (sin huecos)."""
    cursor.execute(
        """
        INSERT INTO contadores_publicos (clave, valor)
        VALUES (?, 1)
        ON CONFLICT (clave) DO UPDATE
            SET valor = contadores_publicos.valor + 1
        RETURNING valor
        """,
        (clave,),
    )
    return _valor_fila(cursor.fetchone(), 'valor', 0)


def asignar_codigo(cursor, tabla, entidad_id):
    """Asigna ``codigo_publico`` a una fila si aún no lo tiene.

    Reutiliza el cursor del llamador (misma transacción). Aísla cualquier fallo
    con un ``SAVEPOINT`` para no abortar la transacción externa. Devuelve el
    código asignado (o el existente) o ``None`` si no se pudo.
    """
    config = CONFIG_CODIGOS.get(tabla)
    if not config or not entidad_id:
        return None
    clave, prefijo, ancho = config

    try:
        cursor.execute('SAVEPOINT codigo_publico')
    except Exception:
        # Sin soporte de savepoint: se intenta igualmente (mejor esfuerzo).
        pass

    try:
        cursor.execute(
            f'SELECT codigo_publico FROM {tabla} WHERE id = ?',
            (int(entidad_id),),
        )
        actual = _valor_fila(cursor.fetchone(), 'codigo_publico', 0)
        if actual and str(actual).strip():
            _liberar_savepoint(cursor)
            return str(actual).strip()

        valor = _siguiente_valor(cursor, clave)
        codigo = formatear_codigo(prefijo, valor, ancho)
        if not codigo:
            _revertir_savepoint(cursor)
            return None
        cursor.execute(
            f"UPDATE {tabla} SET codigo_publico = ? "
            "WHERE id = ? AND (codigo_publico IS NULL OR TRIM(codigo_publico) = '')",
            (codigo, int(entidad_id)),
        )
        _liberar_savepoint(cursor)
        return codigo
    except Exception as error:
        print(
            f'{LOG_PREFIX} no se pudo asignar a {tabla}.{entidad_id}: '
            f'{type(error).__name__}: {error}',
            flush=True,
        )
        _revertir_savepoint(cursor)
        return None


def _liberar_savepoint(cursor):
    try:
        cursor.execute('RELEASE SAVEPOINT codigo_publico')
    except Exception:
        pass


def _revertir_savepoint(cursor):
    try:
        cursor.execute('ROLLBACK TO SAVEPOINT codigo_publico')
    except Exception:
        pass


def _reservar_bloque(cursor, clave, cantidad):
    """Reserva ``cantidad`` valores contiguos y devuelve el primero (o None)."""
    cantidad = int(cantidad)
    if cantidad <= 0:
        return None
    cursor.execute(
        """
        INSERT INTO contadores_publicos (clave, valor)
        VALUES (?, ?)
        ON CONFLICT (clave) DO UPDATE
            SET valor = contadores_publicos.valor + ?
        RETURNING valor
        """,
        (clave, cantidad, cantidad),
    )
    final = _valor_fila(cursor.fetchone(), 'valor', 0)
    if final is None:
        return None
    return int(final) - cantidad + 1


def asignar_codigos_pendientes(cursor, tabla, filtro_sql=None, params=()):
    """Asigna código a todas las filas sin ``codigo_publico`` de una tabla.

    Idempotente y eficiente: reserva un bloque contiguo del contador y asigna en
    un ÚNICO ``UPDATE`` (evita N statements en la importación/backfill). El
    contador garantiza valores monotónicos y sin huecos visibles.

    ``filtro_sql`` permite acotar (p. ej. ``comercio_id = ?``). Devuelve el
    número de códigos generados. Nunca lanza.
    """
    config = CONFIG_CODIGOS.get(tabla)
    if not config:
        return 0
    clave, prefijo, ancho = config

    where = "codigo_publico IS NULL OR TRIM(codigo_publico) = ''"
    if filtro_sql:
        where = f'({where}) AND ({filtro_sql})'

    try:
        cursor.execute('SAVEPOINT codigos_lote')
    except Exception:
        pass

    try:
        cursor.execute(
            f'SELECT COUNT(*) AS n FROM {tabla} WHERE {where}', tuple(params or ())
        )
        pendientes = int(_valor_fila(cursor.fetchone(), 'n', 0) or 0)
        if pendientes <= 0:
            _cerrar_savepoint(cursor)
            return 0

        inicio = _reservar_bloque(cursor, clave, pendientes)
        if inicio is None:
            _revertir_savepoint_lote(cursor)
            return 0
        cursor.execute(
            f"""
            UPDATE {tabla} t
            SET codigo_publico = ? || '-' || LPAD(CAST(? + s.rn - 1 AS TEXT), ?, '0')
            FROM (
                SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS rn
                FROM {tabla}
                WHERE {where}
            ) s
            WHERE t.id = s.id
            """,
            (prefijo, inicio, ancho, *(params or ())),
        )
        _cerrar_savepoint(cursor)
        return pendientes
    except Exception as error:
        print(f'{LOG_PREFIX} no se pudo asignar en {tabla}: {error}', flush=True)
        _revertir_savepoint_lote(cursor)
        return 0


def _cerrar_savepoint(cursor):
    try:
        cursor.execute('RELEASE SAVEPOINT codigos_lote')
    except Exception:
        pass


def _revertir_savepoint_lote(cursor):
    try:
        cursor.execute('ROLLBACK TO SAVEPOINT codigos_lote')
    except Exception:
        pass


# --- Envoltorios por entidad (usados por los puntos de inserción) ------------


def asignar_codigo_usuario(cursor, usuario_id):
    return asignar_codigo(cursor, 'usuarios', usuario_id)


def asignar_codigo_comercio(cursor, comercio_id):
    return asignar_codigo(cursor, 'comercios', comercio_id)


def asignar_codigo_producto(cursor, producto_id):
    return asignar_codigo(cursor, 'productos', producto_id)
