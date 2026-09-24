"""Conector oficial de **Serper.dev (Google Images)** para Localis.

Sustituye por completo a Google Custom Search Engine (CSE) y a Open Food Facts
en el flujo de enriquecimiento automático de imágenes de productos.

Contrato principal
==================
``buscar_imagen(query) -> str | None`` recibe una cadena de consulta y devuelve
**estrictamente** la URL de la primera imagen limpia de alta calidad (campo
``imageUrl`` de Serper) o ``None`` si no hay clave, no hay resultados o falla la
red. **Nunca lanza**.

API
===
- Endpoint:  ``POST https://google.serper.dev/images``
- Headers:   ``X-API-KEY``, ``Content-Type: application/json``
- Payload:   ``{"q": "...", "gl": "ve", "hl": "es"}``

Variables de entorno::

    SERPER_API_KEY=...               # OBLIGATORIA (sin valor por defecto)
    LOCALIS_SERPER_GL=ve             # país (gl)
    LOCALIS_SERPER_HL=es             # idioma (hl)
    LOCALIS_SERPER_TIMEOUT_SEC=12
    LOCALIS_SERPER_REINTENTOS=2
    LOCALIS_SERPER_LIMITE=10
    LOCALIS_SERPER_MIN_LADO=200      # lado mínimo de la imagen elegida
    LOCALIS_SERPER_MAX_DIA=90        # tope duro diario de llamadas (0 = sin tope)
    LOCALIS_SERPER_CACHE_TTL_SEC=86400  # caché en memoria por consulta
    LOCALIS_SERPER_CACHE_MAX=2000
    LOCALIS_IMG_SERPER_PARALELO=2    # llamadas concurrentes máximas
    LOCALIS_SERPER_CONFIG_COOLDOWN_SEG=3600  # pausa tras 401/403
    LOCALIS_SERPER_CUOTA_COOLDOWN_SEG=3600   # pausa tras 429

Seguridad
=========
La clave **nunca** se escribe en el código ni en los logs: este módulo solo la
lee de ``SERPER_API_KEY`` y, si falta, degrada a un estado neutro sin lanzar
hacia el pipeline (salvo al llamar a :func:`require_clave`, que sí es explícito).
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlparse

import requests

ENDPOINT = 'https://google.serper.dev/images'
_LOG = '[Localis Serper]'
_VAR_CLAVE = 'SERPER_API_KEY'


class SerperConfigError(RuntimeError):
    """``SERPER_API_KEY`` ausente o vacía: configuración obligatoria."""


def _env_int(nombre, defecto):
    try:
        return int(str(os.getenv(nombre, '')).strip() or defecto)
    except (TypeError, ValueError):
        return defecto


def _env_float(nombre, defecto):
    try:
        return float(str(os.getenv(nombre, '')).strip() or defecto)
    except (TypeError, ValueError):
        return defecto


# ---------------------------------------------------------------------------
# Estado de concurrencia / cuota (thread-safe)
# ---------------------------------------------------------------------------
_SERPER_PARALELO = max(1, _env_int('LOCALIS_IMG_SERPER_PARALELO', 2))
_SEM = threading.Semaphore(_SERPER_PARALELO)

_lock = threading.Lock()
_config_invalida_hasta = 0.0
_cuota_agotada_hasta = 0.0
_llamadas = 0            # llamadas HTTP 200 reales (consumo de créditos)
_aciertos_cache = 0      # consultas resueltas por caché en memoria (sin costo)
_ultimo_error = {}

# Caché en memoria de consultas (evita repetir la MISMA búsqueda entre
# productos, reintentos del backfill y re-subidas del mismo CSV).
_cache_consultas = {}
_cache_lock = threading.Lock()
_CACHE_TTL = max(0, _env_int('LOCALIS_SERPER_CACHE_TTL_SEC', 86400))
_CACHE_MAX = max(50, _env_int('LOCALIS_SERPER_CACHE_MAX', 2000))

# Tope diario duro: corta el consumo si un lote intenta disparar cientos de
# consultas. 0 desactiva el tope.
_dia_contable = None
_tope_dia_alcanzado = False


def _limite_diario():
    return max(0, _env_int('LOCALIS_SERPER_MAX_DIA', 90))


def _reiniciar_dia_si_aplica():
    global _dia_contable, _llamadas, _tope_dia_alcanzado
    from datetime import date

    hoy = date.today()
    if _dia_contable != hoy:
        _dia_contable = hoy
        _llamadas = 0
        _tope_dia_alcanzado = False


def _normalizar_consulta(consulta):
    return ' '.join(str(consulta or '').split()).strip().lower()


def _cache_get(clave):
    if _CACHE_TTL <= 0 or not clave:
        return None
    with _cache_lock:
        entrada = _cache_consultas.get(clave)
        if not entrada:
            return None
        marca, valor = entrada
        if time.time() - marca > _CACHE_TTL:
            _cache_consultas.pop(clave, None)
            return None
        return valor


def _cache_set(clave, valor):
    if _CACHE_TTL <= 0 or not clave:
        return
    with _cache_lock:
        if len(_cache_consultas) >= _CACHE_MAX:
            # Purga simple del 20% más antiguo para acotar memoria.
            ordenados = sorted(_cache_consultas.items(), key=lambda item: item[1][0])
            for vieja, _ in ordenados[: max(1, _CACHE_MAX // 5)]:
                _cache_consultas.pop(vieja, None)
        _cache_consultas[clave] = (time.time(), valor)


def require_clave():
    """Devuelve ``SERPER_API_KEY`` o lanza :class:`SerperConfigError`.

    Es la única función que falla de forma explícita ante una configuración
    incompleta; el resto del módulo degrada de forma segura.
    """
    valor = str(os.environ.get(_VAR_CLAVE, '') or '').strip()
    if not valor:
        raise SerperConfigError(
            f'{_VAR_CLAVE} no está configurada. Define la variable de entorno '
            'con tu API key de Serper.dev antes de arrancar el servicio.'
        )
    return valor


# Alias retrocompatible: obtiene la clave o lanza SerperConfigError.
def clave_serper():
    return require_clave()


def configurada():
    """True si ``SERPER_API_KEY`` existe y no está vacía. Nunca lanza."""
    try:
        return bool(require_clave())
    except SerperConfigError:
        return False


def habilitado():
    """True si hay clave configurada (no implica conectividad)."""
    return configurada()


def _gl():
    return str(os.getenv('LOCALIS_SERPER_GL', 've')).strip() or 've'


def _hl():
    return str(os.getenv('LOCALIS_SERPER_HL', 'es')).strip() or 'es'


def _limite_defecto():
    return max(1, min(20, _env_int('LOCALIS_SERPER_LIMITE', 10)))


def _min_lado():
    return max(0, _env_int('LOCALIS_SERPER_MIN_LADO', 200))


def _timeout():
    return max(2.0, _env_float('LOCALIS_SERPER_TIMEOUT_SEC', 12.0))


def _reintentos():
    return max(1, _env_int('LOCALIS_SERPER_REINTENTOS', 2))


def _cooldown_config_seg():
    return max(60, _env_int('LOCALIS_SERPER_CONFIG_COOLDOWN_SEG', 3600))


def _cooldown_cuota_seg():
    return max(60, _env_int('LOCALIS_SERPER_CUOTA_COOLDOWN_SEG', 3600))


def _ahora():
    return time.time()


def cuota_agotada():
    with _lock:
        return _ahora() < _cuota_agotada_hasta


def api_invalida():
    with _lock:
        return _ahora() < _config_invalida_hasta


def estado_cuota():
    """Diagnóstico de cuota/configuración (para health/logs)."""
    with _lock:
        return {
            'configurada': configurada(),
            'llamadas_api': _llamadas,
            'aciertos_cache': _aciertos_cache,
            'cache_consultas': len(_cache_consultas),
            'limite_diario': _limite_diario(),
            'tope_dia_alcanzado': _tope_dia_alcanzado,
            'concurrentes_max': _SERPER_PARALELO,
            'agotada': _ahora() < _cuota_agotada_hasta,
            'api_invalida': _ahora() < _config_invalida_hasta,
            'ultimo_error': dict(_ultimo_error) or None,
        }


def estado_configuracion():
    """Estado de configuración **sin exponer el secreto**.

    Devuelve metadatos seguros para logs/health: variable esperada, si está
    presente y su longitud, más los parámetros no sensibles del cliente.
    """
    try:
        longitud = len(require_clave())
        presente = True
    except SerperConfigError:
        presente, longitud = False, 0
    return {
        'variable': _VAR_CLAVE,
        'configurada': presente,
        'key_longitud': longitud,
        'endpoint': ENDPOINT,
        'gl': _gl(),
        'hl': _hl(),
        'concurrentes_max': _SERPER_PARALELO,
    }


def reiniciar_estado():
    """Reinicia contadores, caché y banderas (uso en pruebas/diagnóstico)."""
    global _config_invalida_hasta, _cuota_agotada_hasta, _llamadas
    global _aciertos_cache, _tope_dia_alcanzado
    with _lock:
        _config_invalida_hasta = 0.0
        _cuota_agotada_hasta = 0.0
        _llamadas = 0
        _aciertos_cache = 0
        _tope_dia_alcanzado = False
    with _cache_lock:
        _cache_consultas.clear()
    _ultimo_error.clear()


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------
def _dominio(url):
    try:
        return (urlparse(str(url or '')).hostname or '').lower()
    except Exception:
        return ''


def _entero(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _url_valida(url):
    """URL http(s) de imagen, descartando SVG y huecos."""
    texto = str(url or '').strip()
    if not texto.lower().startswith(('http://', 'https://')):
        return False
    if urlparse(texto).path.lower().endswith('.svg'):
        return False
    return True


def _calidad_ok(item):
    """True si la imagen parece limpia y de calidad suficiente.

    Si Serper no informa dimensiones se acepta (no se puede verificar).
    """
    ancho, alto = item.get('ancho'), item.get('alto')
    if ancho and alto:
        return min(int(ancho), int(alto)) >= _min_lado()
    return True


def _marcar_config_invalida(status, mensaje):
    global _config_invalida_hasta
    with _lock:
        _config_invalida_hasta = _ahora() + _cooldown_config_seg()
        _ultimo_error.clear()
        _ultimo_error.update({'tipo': 'config', 'status': status, 'mensaje': str(mensaje)[:200]})
    print(
        f'{_LOG} evento=config_invalida status={status} '
        f'pausa={_cooldown_config_seg()}s detalle={str(mensaje)[:160]!r}',
        flush=True,
    )


def _marcar_cuota_agotada(status):
    global _cuota_agotada_hasta
    with _lock:
        _cuota_agotada_hasta = _ahora() + _cooldown_cuota_seg()
        _ultimo_error.clear()
        _ultimo_error.update({'tipo': 'cuota', 'status': status})
    print(
        f'{_LOG} evento=cuota_agotada status={status} '
        f'pausa={_cooldown_cuota_seg()}s; los productos quedan pendientes',
        flush=True,
    )


def _motivo_http(status, datos):
    """Mensaje breve del error devuelto por Serper."""
    if isinstance(datos, dict):
        for clave in ('message', 'error', 'detail'):
            valor = datos.get(clave)
            if valor:
                return str(valor)[:200]
    return ''


def _post_imagenes(consulta, *, timeout=None, reintentos=None):
    """POST crudo a Serper. Devuelve el dict de respuesta o ``None``.

    Nunca lanza: los errores quedan en logs estructurados y se devuelve ``None``.
    """
    global _llamadas, _tope_dia_alcanzado

    try:
        clave = require_clave()
    except SerperConfigError:
        print(
            f'{_LOG} evento=config_faltante variable={_VAR_CLAVE} '
            'detalle=no configurada; se omite la búsqueda',
            flush=True,
        )
        return None

    cabeceras = {'X-API-KEY': clave, 'Content-Type': 'application/json'}
    payload = {'q': consulta, 'gl': _gl(), 'hl': _hl()}
    intentos = max(1, int(reintentos or _reintentos()))
    timeout = timeout or _timeout()
    backoff = 0.5

    for intento in range(intentos):
        try:
            with _SEM:
                respuesta = requests.post(
                    ENDPOINT,
                    json=payload,
                    headers=cabeceras,
                    timeout=timeout,
                )
        except requests.RequestException as error:
            print(
                f'{_LOG} evento=error_red intento={intento + 1}/{intentos} '
                f'q={consulta!r} tipo={type(error).__name__}',
                flush=True,
            )
            if intento + 1 >= intentos:
                return None
            time.sleep(backoff * (2 ** intento))
            continue

        status = respuesta.status_code
        try:
            datos = respuesta.json()
        except ValueError:
            datos = None

        if status == 200:
            with _lock:
                _reiniciar_dia_si_aplica()
                _llamadas += 1
                limite = _limite_diario()
                if limite > 0 and _llamadas >= limite:
                    _tope_dia_alcanzado = True
            if _tope_dia_alcanzado:
                print(
                    f'{_LOG} evento=tope_diario alcanzado={_llamadas}/'
                    f'{_limite_diario()} detalle=se detienen las consultas Serper',
                    flush=True,
                )
            return datos if isinstance(datos, dict) else None

        if status in (401, 403):
            _marcar_config_invalida(status, _motivo_http(status, datos))
            return None

        if status == 429:
            _marcar_cuota_agotada(status)
            return None

        if status in (500, 502, 503, 504) and intento + 1 < intentos:
            print(
                f'{_LOG} evento=http_transitorio status={status} '
                f'intento={intento + 1}/{intentos} q={consulta!r}',
                flush=True,
            )
            time.sleep(backoff * (2 ** intento))
            continue

        print(
            f'{_LOG} evento=http_error status={status} q={consulta!r} '
            f'detalle={_motivo_http(status, datos)[:160]!r}',
            flush=True,
        )
        return None

    return None


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def buscar_imagenes(consulta, limite=None):
    """Lista de imágenes de Serper.

    Devuelve ``[{url, titulo, dominio, ancho, alto, contexto}]``. Ante cuota,
    clave inválida, error HTTP o JSON inválido devuelve ``[]`` (sin lanzar).

    Control de créditos:
      - Caché en memoria por consulta normalizada (TTL) → repetir la misma
        búsqueda (re-subida del CSV, backfill, varios productos) no gasta.
      - Tope diario duro (``LOCALIS_SERPER_MAX_DIA``) que corta el consumo.
    """
    global _aciertos_cache, _tope_dia_alcanzado

    consulta = ' '.join(str(consulta or '').split()).strip()
    if not consulta or not habilitado():
        return []
    if cuota_agotada() or api_invalida():
        return []

    limite = max(1, min(20, int(limite or _limite_defecto())))
    clave_cache = _normalizar_consulta(consulta)
    almacenado = _cache_get(clave_cache)
    if almacenado is not None:
        with _lock:
            _aciertos_cache += 1
        return [dict(item) for item in almacenado[:limite]]

    with _lock:
        _reiniciar_dia_si_aplica()
        limite_dia = _limite_diario()
        if _tope_dia_alcanzado or (limite_dia > 0 and _llamadas >= limite_dia):
            _tope_dia_alcanzado = True
            tope = True
        else:
            tope = False
    if tope:
        _marcar_cuota_agotada(f'tope diario Serper {_limite_diario()}')
        return []

    datos = _post_imagenes(consulta)
    if not datos:
        return []

    salida = []
    vistos = set()
    for item in datos.get('images') or []:
        if not isinstance(item, dict):
            continue
        url = item.get('imageUrl') or item.get('link') or item.get('thumbnailUrl')
        if not _url_valida(url) or url in vistos:
            continue
        vistos.add(url)
        salida.append(
            {
                'url': url,
                'titulo': str(item.get('title') or ''),
                'dominio': _dominio(url),
                'ancho': _entero(item.get('imageWidth')),
                'alto': _entero(item.get('imageHeight')),
                'contexto': str(item.get('source') or item.get('domain') or ''),
            }
        )
        if len(salida) >= limite:
            break
    # Se cachea también el resultado vacío (negativo) para no repetir el gasto.
    _cache_set(clave_cache, salida)
    return salida


def buscar_imagen(consulta):
    """URL de la **primera imagen limpia y de calidad**, o ``None``.

    Contrato estricto usado por el registro automático: una sola consulta por
    producto, sin efectos secundarios sobre el sistema.
    """
    for item in buscar_imagenes(consulta, limite=max(_limite_defecto(), 5)):
        if _calidad_ok(item):
            return item['url']
    return None


def buscar_por_codigo(codigo_barras, limite=None):
    """Prioridad 1: búsqueda por EAN/UPC exacto."""
    codigo = str(codigo_barras or '').strip()
    if not codigo:
        return []
    return buscar_imagenes(codigo, limite=limite)


def buscar_por_nombre_descripcion(nombre, descripcion=None, limite=None):
    """Prioridad 2: consulta optimizada ``nombre + descripción corta``."""
    partes = [str(nombre or '').strip()]
    desc = ' '.join(str(descripcion or '').split())
    if desc:
        partes.append(desc[:80])
    consulta = ' '.join(p for p in partes if p).strip()
    if not consulta:
        return []
    return buscar_imagenes(consulta, limite=limite)


def diagnosticar(*, consulta='laptop', imprimir=True):
    """Consulta de prueba real y clasificación del resultado. No muta el estado."""
    informe = {
        'habilitado': False,
        'key_presente': False,
        'key_longitud': 0,
        'status': None,
        'ok': False,
        'categoria': None,
        'mensaje': '',
    }
    try:
        clave = require_clave()
    except SerperConfigError:
        informe['categoria'] = 'configuracion'
        informe['mensaje'] = f'Falta {_VAR_CLAVE}.'
        if imprimir:
            print(f'{_LOG} diagnostico={informe["categoria"]} {informe["mensaje"]}', flush=True)
        return informe

    informe['habilitado'] = True
    informe['key_presente'] = True
    informe['key_longitud'] = len(clave)

    try:
        with _SEM:
            respuesta = requests.post(
                ENDPOINT,
                json={'q': consulta, 'gl': _gl(), 'hl': _hl()},
                headers={'X-API-KEY': clave, 'Content-Type': 'application/json'},
                timeout=_timeout(),
            )
    except requests.RequestException as error:
        informe.update(
            {
                'categoria': 'red',
                'mensaje': f'{type(error).__name__}: {error}',
            }
        )
        if imprimir:
            print(f'{_LOG} diagnostico=red detalle={informe["mensaje"][:160]!r}', flush=True)
        return informe

    informe['status'] = respuesta.status_code
    try:
        datos = respuesta.json()
    except ValueError:
        datos = None

    if respuesta.status_code == 200:
        informe['ok'] = True
        informe['categoria'] = 'ok'
        informe['mensaje'] = 'Credenciales válidas: Serper respondió HTTP 200.'
    elif respuesta.status_code in (401, 403):
        informe['categoria'] = 'clave_invalida'
        informe['mensaje'] = _motivo_http(respuesta.status_code, datos) or 'API key rechazada.'
    elif respuesta.status_code == 429:
        informe['categoria'] = 'cuota'
        informe['mensaje'] = _motivo_http(respuesta.status_code, datos) or 'Cuota agotada.'
    else:
        informe['categoria'] = 'http_error'
        informe['mensaje'] = _motivo_http(respuesta.status_code, datos)

    if imprimir:
        print(
            f'{_LOG} diagnostico={informe["categoria"]} status={informe["status"]} '
            f'mensaje={informe["mensaje"][:160]!r}',
            flush=True,
        )
    return informe
