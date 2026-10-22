"""Relleno automático de ubicación (ciudad/zona/dirección) desde Google Maps.

Un enlace de Maps compartido suele ser **corto** (``maps.app.goo.gl/...``) y no
trae texto de ubicación: solo coordenadas tras resolverlo. Para que la búsqueda
por zona/ciudad siga funcionando como antes —pero sin que el comerciante
escriba nada— este módulo:

1. **Resuelve** el enlace corto (redirección de Google) para obtener coordenadas.
2. **Geocodifica al revés** esas coordenadas con OpenStreetMap/Nominatim (gratis,
   sin clave) para obtener ciudad, zona y una dirección legible.

Principios (coherentes con el resto del proyecto):

- **Nunca lanza.** Si la red falla, el servicio no responde o se agota el tiempo,
  la sede se guarda igual; solo queda sin ciudad/zona.
- **Apagable** con ``LOCALIS_GEOCODIFICACION=0``.
- **Con caché** por coordenadas (los comercios de una misma zona comparten datos)
  y **con límite de tiempo** por lote, para no encadenar peticiones.
- La parte de interpretación de la respuesta (:func:`datos_desde_respuesta_nominatim`)
  es **pura** y se prueba sin red.
"""

import logging
import os
import threading
import time

import requests

from backend.runtime_cache import get_or_load
from backend.utils import derivar_datos_maps

logger = logging.getLogger('localis.geo_ubicacion')

LOG_PREFIX = '[Localis Geo]'

NOMINATIM_URL = os.getenv(
    'LOCALIS_NOMINATIM_URL', 'https://nominatim.openstreetmap.org/reverse'
)
USER_AGENT = os.getenv(
    'LOCALIS_GEO_USER_AGENT',
    'Localis/1.0 (marketplace Venezuela; uso moderado)',
)

# Nominatim pide como máximo ~1 petición por segundo y una caché por cliente.
_INTERVALO_MINIMO = 1.1
_ultimo_llamado = [0.0]
_lock = threading.Lock()

# Hosts de enlaces cortos de Google Maps (requieren resolver la redirección).
_HOSTS_CORTOS = ('goo.gl', 'g.co')

_CACHE_TTL_SEG = 30 * 24 * 3600  # una zona no cambia: cache de 30 días


def geocodificacion_habilitada():
    """True salvo que ``LOCALIS_GEOCODIFICACION`` la desactive explícitamente."""
    valor = str(os.getenv('LOCALIS_GEOCODIFICACION', '1')).strip().lower()
    return valor in ('1', 'true', 'yes', 'on')


def _timeout():
    try:
        return max(1.0, float(os.getenv('LOCALIS_GEOCODIFICACION_TIMEOUT_SEG', '5')))
    except (TypeError, ValueError):
        return 5.0


def _presupuesto_seg():
    """Presupuesto total para geocodificar todas las sedes de un comercio.

    Nominatim exige ~1 req/s, así que hay que dar margen: con el máximo de 10
    sedes hacen falta ~15-20 s. Un valor corto dejaría las últimas sedes sin
    ciudad/zona. El envío del formulario puede tardar eso una sola vez.
    """
    try:
        return max(
            0.0,
            float(os.getenv('LOCALIS_GEOCODIFICACION_PRESUPUESTO_SEG', '25')),
        )
    except (TypeError, ValueError):
        return 25.0


# ---------------------------------------------------------------------------
# Interpretación de la respuesta (PURA: se prueba sin red)
# ---------------------------------------------------------------------------

_PREFIJOS_ADMIN = ('estado ', 'municipio ', 'parroquia ', 'municipio autónomo ')


def _limpiar(texto):
    """Normaliza un nombre administrativo quitando prefijos tipo 'Estado '."""
    limpio = str(texto or '').strip()
    if not limpio:
        return None
    bajo = limpio.lower()
    for prefijo in _PREFIJOS_ADMIN:
        if bajo.startswith(prefijo):
            limpio = limpio[len(prefijo):].strip()
            break
    return limpio or None


def _primer_valor(mapa, claves):
    """Primer valor no vacío de ``mapa`` según el orden de ``claves``."""
    if not isinstance(mapa, dict):
        return None
    for clave in claves:
        valor = mapa.get(clave)
        if valor is None:
            continue
        texto = str(valor).strip()
        if texto:
            return texto
    return None


def _componer_direccion(mapa, nombre=None, ciudad=None, estado=None):
    """Dirección legible y buscable: vía, zona, ciudad, estado (sin repetir)."""
    partes = [
        _primer_valor(mapa, ('road', 'pedestrian', 'residential')) or nombre,
        _primer_valor(mapa, ('neighbourhood', 'suburb', 'city_district', 'quarter')),
        ciudad,
        estado,
    ]
    vistas = []
    for parte in partes:
        limpio = _limpiar(parte)
        if limpio and limpio.lower() not in (v.lower() for v in vistas):
            vistas.append(limpio)
    return ', '.join(vistas) if vistas else None


def datos_desde_respuesta_nominatim(payload):
    """Convierte la respuesta de Nominatim en ``ciudad``/``zona``/``direccion``.

    Pura y defensiva: ante una respuesta incompleta o inesperada devuelve ``{}``.
    """
    if not isinstance(payload, dict):
        return {}
    mapa = payload.get('address')
    if not isinstance(mapa, dict):
        mapa = {}

    # Orden pensado para Venezuela: OSM a veces no trae ``city`` y la ciudad real
    # está en ``county`` ("Municipio Maracaibo" -> "Maracaibo"). Se prefiere
    # ``county`` antes que ``municipality`` (que suele ser la parroquia).
    ciudad = _limpiar(
        _primer_valor(
            mapa, ('city', 'town', 'village', 'hamlet', 'county', 'municipality')
        )
    )
    estado = _limpiar(_primer_valor(mapa, ('state',)))
    zona = _limpiar(
        _primer_valor(
            mapa,
            ('neighbourhood', 'suburb', 'city_district', 'quarter', 'county'),
        )
    ) or estado
    direccion = _componer_direccion(
        mapa if mapa else payload, nombre=payload.get('name'), ciudad=ciudad, estado=estado
    )

    return {
        'ciudad': ciudad,
        'zona': zona,
        'estado': estado,
        'pais': (mapa.get('country') or None),
        'direccion': direccion,
        'display_name': payload.get('display_name') or None,
    }


# ---------------------------------------------------------------------------
# Resolución de enlace corto y geocodificación inversa (RED, tolerante a fallos)
# ---------------------------------------------------------------------------


def es_enlace_corto(url):
    """True si el enlace de Maps es un acortador y hay que resolverlo."""
    texto = str(url or '').lower()
    return any(host in texto for host in _HOSTS_CORTOS)


def resolver_enlace_maps(url, timeout=None):
    """Sigue la redirección de un enlace corto. Devuelve la URL final o ``url``.

    Nunca lanza: si falla, devuelve la entrada tal cual.
    """
    texto = str(url or '').strip()
    if not texto or not es_enlace_corto(texto):
        return texto
    try:
        respuesta = requests.get(
            texto,
            allow_redirects=True,
            stream=True,
            timeout=timeout or _timeout(),
            headers={'User-Agent': USER_AGENT},
        )
        final = respuesta.url or texto
        respuesta.close()
        return final
    except Exception as error:
        logger.error(f'{LOG_PREFIX} resolver enlace corto: {type(error).__name__}: {error}')
        return texto


def _esperar_turno():
    """Respeta el límite de ~1 petición/segundo de Nominatim."""
    with _lock:
        espera = _INTERVALO_MINIMO - (time.monotonic() - _ultimo_llamado[0])
        if espera > 0:
            time.sleep(espera)
        _ultimo_llamado[0] = time.monotonic()


def _llamar_nominatim(lat, lng, timeout):
    _esperar_turno()
    respuesta = requests.get(
        NOMINATIM_URL,
        params={
            'format': 'jsonv2',
            'lat': f'{lat}',
            'lon': f'{lng}',
            'addressdetails': '1',
            'zoom': '16',
            'accept-language': 'es',
        },
        headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'},
        timeout=timeout,
    )
    if respuesta.status_code != 200:
        logger.error(f'{LOG_PREFIX} Nominatim HTTP {respuesta.status_code}')
        return None
    return datos_desde_respuesta_nominatim(respuesta.json())


def geocodificar_coordenadas(lat, lng):
    """Ciudad/zona/dirección de unas coordenadas, con caché. ``None`` si falla."""
    if not geocodificacion_habilitada():
        return None
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None

    clave = f'geo:nominatim:{round(lat_f, 4)},{round(lng_f, 4)}'

    def _cargar():
        try:
            return _llamar_nominatim(lat_f, lng_f, _timeout())
        except Exception as error:
            logger.error(f'{LOG_PREFIX} geocodificar: {type(error).__name__}: {error}')
            return None

    return get_or_load(clave, _cargar, ttl_seconds=_CACHE_TTL_SEG)


# ---------------------------------------------------------------------------
# Enriquecimiento de sedes
# ---------------------------------------------------------------------------


def _coordenadas_de_sede(sede):
    """Coordenadas de una sede: del enlace guardado o resolviendo el corto."""
    url = str(sede.get('maps_url') or '').strip()
    if not url:
        return None
    datos = derivar_datos_maps(url)
    if datos.get('coordinates_maps'):
        return (datos['latitud'], datos['longitud'])
    if es_enlace_corto(url):
        resuelto = resolver_enlace_maps(url)
        if resuelto and resuelto != url:
            datos_resuelto = derivar_datos_maps(resuelto)
            if datos_resuelto.get('coordinates_maps'):
                return (datos_resuelto['latitud'], datos_resuelto['longitud'])
    return None


def _enriquecer_sede(sede):
    """Completa ciudad/zona/dirección de una sede. Nunca lanza."""
    coordenadas = _coordenadas_de_sede(sede)
    if not coordenadas:
        return
    lat, lng = coordenadas
    if not sede.get('coordinates_maps'):
        sede['coordinates_maps'] = f'{lat},{lng}'

    datos = geocodificar_coordenadas(lat, lng)
    if not datos:
        return
    if datos.get('ciudad'):
        sede['ciudad'] = datos['ciudad']
    if datos.get('zona'):
        sede['zona'] = datos['zona']

    # La dirección automática solo sustituye si no había una mejor: la derivada
    # del enlace suele ser el propio nombre de la sede, que no es una dirección.
    actual = str(sede.get('direccion') or '').strip()
    nombre = str(sede.get('nombre') or '').strip()
    if datos.get('direccion') and (not actual or actual == nombre):
        sede['direccion'] = datos['direccion']


def enriquecer_ubicaciones_geo(ubicaciones, presupuesto_seg=None):
    """Rellena ciudad/zona/dirección de cada sede desde su enlace de Maps.

    Devuelve una **lista nueva** (no muta la de entrada). Respeta un presupuesto
    de tiempo global para no encadenar peticiones si hay muchas sedes.
    """
    lista = [dict(u) for u in (ubicaciones or []) if isinstance(u, dict)]
    if not lista or not geocodificacion_habilitada():
        return lista

    limite = _presupuesto_seg() if presupuesto_seg is None else max(0.0, float(presupuesto_seg))
    inicio = time.monotonic()
    for sede in lista:
        if time.monotonic() - inicio >= limite:
            logger.error(f'{LOG_PREFIX} presupuesto agotado; sedes sin geocodificar')
            break
        try:
            _enriquecer_sede(sede)
        except Exception as error:
            logger.error(f'{LOG_PREFIX} enriquecer sede: {type(error).__name__}: {error}')
    return lista
