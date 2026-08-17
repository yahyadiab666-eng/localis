"""Conexión SQLite optimizada para uso concurrente."""

import sqlite3

from config import DATABASE_FILE


def get_db_connection(row_factory=None):
    """Abre conexión con WAL, busy_timeout y foreign keys activos."""
    conn = sqlite3.connect(DATABASE_FILE, timeout=5.0)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA busy_timeout=5000;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA foreign_keys=ON;')
    if row_factory:
        conn.row_factory = row_factory
    return conn
