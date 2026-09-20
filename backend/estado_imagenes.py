"""Estado explícito de la imagen de cada producto (anti falsos positivos).

Regla estricta: un producto solo cuenta como **imagen real** cuando su
``imagen_url`` apunta a una foto real (Storage/local o URL de fuente) y NO a un
placeholder. Mientras no exista foto real, el producto queda marcado como
``pendiente`` (aún no procesado) o ``rechazada`` (se intentó y ninguna
candidata superó la validación), y el motor seguirá reintentando.

Esto evita reportar una importación como "completa" cuando en realidad solo se
rellenó con placeholders.
"""

from __future__ import annotations

ESTADO_REAL = 'real'
ESTADO_PENDIENTE = 'pendiente'
ESTADO_RECHAZADA = 'rechazada'

ESTADOS_VALIDOS = (ESTADO_REAL, ESTADO_PENDIENTE, ESTADO_RECHAZADA)
# Estados que todavía necesitan una foto real (se reintentan en segundo plano).
ESTADOS_PENDIENTES = (ESTADO_PENDIENTE, ESTADO_RECHAZADA)

SIN_IMAGEN_REAL = frozenset({
    ESTADO_PENDIENTE,
    ESTADO_RECHAZADA,
})


def normalizar_estado(valor):
    texto = str(valor or '').strip().lower()
    if texto in ESTADOS_VALIDOS:
        return texto
    return ESTADO_PENDIENTE


def construir_reporte_importacion(total, reales, pendientes, rechazadas=0):
    """(mensaje, meta) honesto: el éxito refleja solo imágenes reales.

    - ``completo``: todos los productos tienen imagen real.
    - ``parcial``: el catálogo cargó, pero hay pendientes.
    - ``sin_reales``: ninguno tiene imagen real todavía.
    """
    total = max(0, int(total or 0))
    reales = max(0, int(reales or 0))
    pendientes = max(0, int(pendientes or 0))
    rechazadas = max(0, int(rechazadas or 0))
    faltantes = pendientes + rechazadas

    if total <= 0:
        estado = 'vacio'
        mensaje = 'No se cargaron productos.'
    elif reales >= total and faltantes == 0:
        estado = 'completo'
        mensaje = (
            f'Importación completada: {total} productos con imagen real '
            f'({reales}/{total}).'
        )
    elif reales == 0:
        estado = 'sin_reales'
        mensaje = (
            f'Catálogo cargado: {total} productos, pero 0 con imagen real. '
            f'{faltantes} quedan pendientes de imagen (el motor los procesará en segundo plano).'
        )
    else:
        estado = 'parcial'
        mensaje = (
            f'Catálogo cargado: {total} productos. '
            f'Imágenes reales: {reales}. '
            f'Pendientes de imagen: {faltantes} (procesando en segundo plano).'
        )

    meta = {
        'estado_imagenes': estado,
        'imagenes_total': total,
        'imagenes_reales': reales,
        'imagenes_pendientes': pendientes,
        'imagenes_rechazadas': rechazadas,
    }
    return mensaje, meta


def resumir_conteos(filas):
    """Conteos por estado a partir de filas [(estado, n)] o dict."""
    conteos = {ESTADO_REAL: 0, ESTADO_PENDIENTE: 0, ESTADO_RECHAZADA: 0}
    if isinstance(filas, dict):
        iterable = filas.items()
    else:
        iterable = filas or ()
    for clave, valor in iterable:
        estado = normalizar_estado(clave)
        try:
            conteos[estado] += int(valor or 0)
        except (TypeError, ValueError):
            continue
    conteos['total'] = sum(conteos.values())
    return conteos
