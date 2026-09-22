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
    problemas: list = field(default_factory=list)

    @property
    def porcentaje(self) -> float:
        if self.total <= 0:
            return 1.0
        return self.con_imagen / self.total

    def resumen(self) -> str:
        return (
            f'comercio={self.comercio_id} cobertura_visual='
            f'{self.con_imagen}/{self.total} ({self.porcentaje:.1%}) '
            f'reales={self.reales} logos={self.logos} pendientes={self.pendientes}'
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
    try:
        from utils.images import url_imagen_producto

        return url_imagen_producto(producto)
    except Exception:
        return None


def auditar_comercio(comercio_id, *, verificar_storage=False, muestra_storage=5):
    """Audita la cobertura de un comercio. No lanza; retorna el reporte."""
    from backend.db import get_db_connection
    from backend.estado_imagenes import normalizar_estado, ESTADO_REAL, ESTADO_LOGO

    reporte = ReporteCobertura(comercio_id=int(comercio_id))
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT id, nombre, codigo_barras, imagen_url, imagen_estado
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
        }
        reporte.total += 1
        url = _url_efectiva_producto(registro)
        if not url:
            reporte.problemas.append(
                f'producto={registro.get("id")} sin URL de imagen'
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


def validar_cobertura(comercio_id, minimo=1.0, *, verificar_storage=False):
    """Lanza ``ErrorCoberturaVisual`` si la cobertura no alcanza ``minimo``."""
    reporte = auditar_comercio(
        comercio_id, verificar_storage=verificar_storage
    )
    if reporte.porcentaje < float(minimo) or reporte.problemas:
        detalle = '; '.join(reporte.problemas[:10]) or 'cobertura insuficiente'
        raise ErrorCoberturaVisual(f'{reporte.resumen()} -> {detalle}')
    return reporte


def _respaldo_para_producto(nombre):
    """(url, fuente) de respaldo inmediato para un producto sin foto usable."""
    try:
        from backend.categorias_producto import clasificar_categoria

        categoria = clasificar_categoria(nombre=nombre)
    except Exception:
        categoria = None
    try:
        from backend.marca_logo import archivo_tarjeta_producto

        url = archivo_tarjeta_producto(nombre, categoria)
        if url:
            return url, 'tarjeta_producto'
    except Exception:
        pass
    try:
        from backend.categorias_producto import imagen_para_categoria

        return imagen_para_categoria(categoria or 'otros'), 'placeholder_categoria'
    except Exception:
        return None, None


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
    """Auto-reparación: degrada fotos 'reales' cuyo asset ya no existe.

    Nunca deja hueco: la URL rota se sustituye por la tarjeta del producto
    (``pendiente``, reintentable) para conservar la cobertura visual y evitar
    reportar como real una imagen que el cliente vería rota. Devuelve
    ``{'revisadas', 'reparadas', 'detalle'}``.
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

        nueva_url, nueva_fuente = _respaldo_para_producto(registro.get('nombre'))
        if not nueva_url:
            resultado['detalle'].append(
                f'producto={registro.get("id")} sin respaldo para {str(url)[:80]}'
            )
            continue
        try:
            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    """
                    UPDATE productos
                    SET imagen_url = ?, imagen_fuente = ?,
                        imagen_estado = 'pendiente', imagen_intentos = 0
                    WHERE id = ?
                    """,
                    (nueva_url, nueva_fuente, int(registro.get('id'))),
                )
                conexion.commit()
            resultado['reparadas'] += 1
            resultado['detalle'].append(
                f'producto={registro.get("id")} rota -> {nueva_fuente}'
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
    minimo = float(argv[1]) if len(argv) > 1 else 1.0
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
        if reporte.porcentaje < minimo or reporte.problemas:
            fallos += 1
    if fallos:
        print(f'COBERTURA INSUFICIENTE en {fallos} comercio(s) (mínimo {minimo:.0%})')
        return 1
    print('OK cobertura visual 100% (sin URLs vacías ni assets rotos)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
