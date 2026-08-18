"""Reexporta la conexión PostgreSQL centralizada en database.py."""

from database import DATABASE_URL, get_db_connection, using_postgres

__all__ = ['DATABASE_URL', 'get_db_connection', 'using_postgres']
