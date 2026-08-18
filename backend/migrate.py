"""Migraciones incrementales para bases de datos existentes."""

from database import init_db


def ejecutar_migraciones():
    """Delega en database.init_db() para migraciones no destructivas."""
    return init_db()
