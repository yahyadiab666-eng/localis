"""Compatibilidad: la política de imágenes vive ahora en ``motor_imagenes``.

Se conserva este módulo para no romper imports existentes (pipeline, tests).
Toda la lógica (enrutamiento por sector, whitelist y excepción segura) está en
``backend/motor_imagenes.py``.
"""

from __future__ import annotations

from backend.motor_imagenes import (
    CATEGORIAS_ESTRUCTURADAS,
    FUENTES_CATALOGO,
    FUENTES_LOGO,
    FUENTES_WEB,
    es_estructurado,
    evaluar_candidato,
    sector_de,
)


def categoria_estructurada(categoria):
    """True si la categoría es de ficha oficial por producto (fuentes globales)."""
    return es_estructurado(categoria)


def evaluar_candidato_por_sector(candidato, categoria=None, marca=None):
    """(aceptado, motivo). Delega en el motor de imágenes."""
    return evaluar_candidato(candidato, categoria=categoria, marca=marca)
