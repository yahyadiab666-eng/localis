"""Validación dura de cobertura visual de imágenes (100% obligatorio).

Verifica, contra la base de datos real, que **cada producto** del comercio tenga
una imagen utilizable y, cuando corresponde, que el archivo exista de verdad
(integridad de asset). Si la cobertura baja del mínimo, lanza
``ErrorCoberturaVisual`` (error duro, no un aviso silencioso).

Métricas:
  - ``visual``: producto con cualquier imagen válida (real, logo o categoría).
  - ``real``: foto real obtenida de la web/Storage.
  - ``logo``: logo/monograma de marca.
  - ``pendiente``: imagen limpia de categoría (aún sin foto real).

Uso CLI::

    python -m backend.cobertura_visual            # audita todos los comercios
    python -m backend.cobertura_visual 1 1.0      # comercio 1, mínimo 100%
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path


class ErrorCoberturaVisual(Exception):
    """La cobertura visual de imágenes no alcanza el mínimo exigido."""


@dataclass
class ReporteCobertura:
    comercio_id: int
    total: int = 0
    con_imagen: int = 0
    reales: int = 0
    logos: int = 0
    pendientes: int = 0
    sin_imagen: int = 0
    generadas: int = 0
    problemas: list = field(default_factory=list)

    @property
    def porcentaje(self) -> float:
        if self.total <= 0:
            return 1.0
        return self.con_imagen / self.total

    def resumen(self) -> str:
        return (
            f'comercio={self.comercio_id} con_imagen='
            f'{self.con_imagen}/{self.total} ({self.porcentaje:.1%}) '
            f'reales={self.reales} logos={self.logos} '
            f'sin_imagen={self.sin_imagen} fabricadas={self.generadas}'
        )


def _ruta_local(url):
    from config import RUTA_RAIZ

    texto = str(url or '').strip()
    if not texto.startswith('/static/'):
        return None
    relativo = texto[len('/static/') :].split('?', 1)[0]
    partes = [p for p in relativo.split('/') if p]
    if not partes or any(p in ('..', '.') for p in partes):
        return None
    return Path(RUTA_RAIZ) / 'static' / Path(*partes)


def _url_efectiva_producto(producto):
    """URL **persistida** (sin el fallback de UI) para auditar el asset real."""
    try:
        from utils.images import url_publica_producto_desde_bd

        crudo = producto.get('imagen_url') if hasattr(producto, 'get') else None
        return url_publica_producto_desde_bd(crudo) or ''
    except Exception:
        return ''


def auditar_comercio(comercio_id, *, verificar_storage=False, muestra_storage=5):
    """Audita los assets de un comercio. No lanza; retorna el reporte.

    Un producto **sin imagen** es un estado honesto (no un problema). Solo se
    reportan como problema los assets rotos y los **fabricados** (placeholder,
    monograma, tarjeta) que no deberían estar persistidos.
    """
    from backend.activos_verificados import es_asset_generado
    from backend.db import get_db_connection
    from backend.estado_imagenes import normalizar_estado, ESTADO_REAL, ESTADO_LOGO

    reporte = ReporteCobertura(comercio_id=int(comercio_id))
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT id, nombre, codigo_barras, imagen_url, imagen_estado,
                   imagen_fuente
            FROM productos
            WHERE comercio_id = ?
            ORDER BY id
            """,
            (int(comercio_id),),
        )
        filas = cursor.fetchall()

    urls_storage = []
    for fila in filas:
        registro = fila if isinstance(fila, dict) else {
            'id': fila[0], 'nombre': fila[1], 'codigo_barras': fila[2],
            'imagen_url': fila[3], 'imagen_estado': fila[4],
            'imagen_fuente': fila[5] if len(fila) > 5 else None,
        }
        reporte.total += 1
        url = _url_efectiva_producto(registro)
        if not url:
            # Sin imagen: estado neutro legítimo (no se inventa nada).
            reporte.sin_imagen += 1
            continue

        if es_asset_generado(url, registro.get('imagen_fuente')):
            reporte.generadas += 1
            reporte.problemas.append(
                f'producto={registro.get("id")} asset fabricado: {str(url)[:90]}'
            )
            continue

        reporte.con_imagen += 1

        estado = normalizar_estado(registro.get('imagen_estado'))
        if estado == ESTADO_REAL:
            reporte.reales += 1
        elif estado == ESTADO_LOGO:
            reporte.logos += 1
        else:
            reporte.pendientes += 1

        if url.startswith('/static/'):
            ruta = _ruta_local(url)
            if ruta is None or not ruta.is_file() or ruta.stat().st_size == 0:
                reporte.problemas.append(
                    f'producto={registro.get("id")} asset local faltante: {url}'
                )
        elif url.startswith('http'):
            urls_storage.append(url)

    if verificar_storage and urls_storage:
        import requests

        objetivo = (
            urls_storage
            if muestra_storage is None
            else urls_storage[: max(0, int(muestra_storage))]
        )
        vistas = set()
        for url in objetivo:
            if url in vistas:
                continue
            vistas.add(url)
            try:
                respuesta = requests.head(
                    url, timeout=8, allow_redirects=True
                )
                if respuesta.status_code >= 400 or respuesta.status_code == 405:
                    respuesta = requests.get(
                        url, timeout=8, allow_redirects=True
                    )
                if respuesta.status_code >= 400:
                    reporte.problemas.append(
                        f'URL inaccesible ({respuesta.status_code}): {url[:110]}'
                    )
            except Exception as error:
                reporte.problemas.append(
                    f'URL inaccesible ({type(error).__name__}): {url[:110]}'
                )
    return reporte


def validar_cobertura(comercio_id, minimo=0.0, *, verificar_storage=False):
    """Lanza ``ErrorCoberturaVisual`` si hay assets fabricados/rotos.

    ``minimo`` es opcional (por defecto 0.0): la ausencia de imagen es un estado
    honesto, no un error. Lo que **sí** falla es persistir un asset fabricado o
    un archivo roto.
    """
    reporte = auditar_comercio(
        comercio_id, verificar_storage=verificar_storage
    )
    sin_cobertura = float(minimo) > 0 and reporte.porcentaje < float(minimo)
    if reporte.problemas or sin_cobertura:
        detalle = '; '.join(reporte.problemas[:10]) or 'cobertura insuficiente'
        raise ErrorCoberturaVisual(f'{reporte.resumen()} -> {detalle}')
    return reporte


def _url_real_es_valida(url, *, verificar_remotas=True):
    """True si el asset de una imagen 'real' existe de verdad."""
    texto = str(url or '').strip()
    if not texto:
        return False
    if texto.startswith('/static/'):
        ruta = _ruta_local(texto)
        return bool(ruta and ruta.is_file() and ruta.stat().st_size > 0)
    if texto.startswith('http'):
        if not verificar_remotas:
            return True
        try:
            from backend.supabase_storage import url_publica_storage_accesible

            return bool(url_publica_storage_accesible(texto))
        except Exception:
            return False
    return False


def reparar_imagenes_rotas(comercio_id=None, limite=400, *, verificar_remotas=True):
    """Auto-reparación: vacía las fotos 'reales' cuyo asset ya no existe.

    Nunca inventa un reemplazo: la URL rota se deja **nula** (estado neutro,
    ``pendiente``) para no mostrar un asset falso y seguir reintentando la foto
    real en segundo plano. Devuelve ``{'revisadas', 'reparadas', 'detalle'}``.
    """
    from backend.db import get_db_connection

    sql = (
        "SELECT id, nombre, imagen_url, imagen_fuente FROM productos "
        "WHERE COALESCE(imagen_estado, 'pendiente') = 'real' "
        "  AND imagen_url IS NOT NULL"
    )
    parametros = []
    if comercio_id is not None:
        sql += " AND comercio_id = ?"
        parametros.append(int(comercio_id))
    sql += " ORDER BY id LIMIT ?"
    parametros.append(int(limite))

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql, tuple(parametros))
            filas = cursor.fetchall()
    except Exception as error:
        print(f'[Localis Cobertura] reparación no consultable: {type(error).__name__}: {error}')
        return {'revisadas': 0, 'reparadas': 0, 'detalle': []}

    resultado = {'revisadas': 0, 'reparadas': 0, 'detalle': []}
    for fila in filas:
        registro = fila if isinstance(fila, dict) else {
            'id': fila[0], 'nombre': fila[1],
            'imagen_url': fila[2], 'imagen_fuente': fila[3],
        }
        resultado['revisadas'] += 1
        url = registro.get('imagen_url')
        if _url_real_es_valida(url, verificar_remotas=verificar_remotas):
            continue

        try:
            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    """
                    UPDATE productos
                    SET imagen_url = NULL, imagen_fuente = NULL,
                        imagen_estado = 'pendiente', imagen_intentos = 0
                    WHERE id = ?
                    """,
                    (int(registro.get('id')),),
                )
                conexion.commit()
            resultado['reparadas'] += 1
            resultado['detalle'].append(
                f'producto={registro.get("id")} rota -> sin imagen (neutro)'
            )
        except Exception as error:
            resultado['detalle'].append(
                f'producto={registro.get("id")} no reparado: {type(error).__name__}'
            )
    return resultado


def comercios_con_productos():
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            "SELECT comercio_id, COUNT(*) n FROM productos "
            "WHERE comercio_id IS NOT NULL GROUP BY comercio_id ORDER BY n DESC"
        )
        filas = cursor.fetchall()
    resultado = []
    for fila in filas:
        if isinstance(fila, dict):
            resultado.append((fila.get('comercio_id'), fila.get('n')))
        else:
            resultado.append((fila[0], fila[1]))
    return resultado


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from dotenv import load_dotenv

        from config import RUTA_RAIZ

        load_dotenv(Path(RUTA_RAIZ) / '.env', override=False)
    except Exception:
        pass
    verificar_storage = '--storage' in argv
    reparar = '--reparar' in argv
    argv = [a for a in argv if a not in ('--storage', '--reparar')]
    minimo = float(argv[1]) if len(argv) > 1 else 0.0
    comercios = (
        [(int(argv[0]), None)]
        if argv and argv[0].isdigit()
        else comercios_con_productos()
    )
    if reparar:
        limite = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 400
        for comercio_id, _ in comercios:
            resultado = reparar_imagenes_rotas(comercio_id, limite=limite)
            print(
                f'comercio={comercio_id} reparación: '
                f'reparadas={resultado["reparadas"]}/{resultado["revisadas"]}'
            )
            for linea in resultado['detalle'][:10]:
                print(f'   - {linea}')
        return 0
    fallos = 0
    for comercio_id, _ in comercios:
        reporte = auditar_comercio(
            comercio_id,
            verificar_storage=verificar_storage,
            muestra_storage=None if verificar_storage else 0,
        )
        print(reporte.resumen())
        for problema in reporte.problemas[:10]:
            print(f'   - {problema}')
        if reporte.problemas or (minimo > 0 and reporte.porcentaje < minimo):
            fallos += 1
    if fallos:
        print(f'ASSETS INVÁLIDOS en {fallos} comercio(s)')
        return 1
    print('OK integridad de assets (sin fabricados ni roto; los faltantes son estados neutros)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
