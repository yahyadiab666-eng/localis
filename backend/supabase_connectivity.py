"""Sanitización de SUPABASE_URL y pruebas de conectividad HTTP/DNS (sin depender del SDK)."""

from __future__ import annotations

import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

LOG_PREFIX = '[Localis Supabase Red]'
_HOST_SUPABASE_RE = re.compile(r'^[a-z0-9-]+\.supabase\.co$', re.IGNORECASE)
_PATHS_ERRONEOS = (
    '/rest/v1',
    '/rest/v1/',
    '/storage/v1',
    '/storage/v1/',
    '/auth/v1',
    '/auth/v1/',
    '/functions/v1',
    '/functions/v1/',
)


def _limpiar_texto_env(valor):
    if valor is None:
        return ''
    return str(valor).strip().strip('"').strip("'").replace('\r', '').replace('\n', '').strip()


@dataclass
class ResultadoUrlSupabase:
    url: str = ''
    host: str = ''
    valida: bool = False
    advertencias: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)


def sanitizar_url_supabase(raw_url: str | None) -> ResultadoUrlSupabase:
    """
    Normaliza SUPABASE_URL para evitar fallos DNS por barras finales, paths extra
    o caracteres inválidos copiados desde el panel de Render/Supabase.
    """
    resultado = ResultadoUrlSupabase()
    texto = _limpiar_texto_env(raw_url)

    if not texto:
        resultado.errores.append('SUPABASE_URL vacía o ausente.')
        return resultado

    if texto != (raw_url or '').strip():
        resultado.advertencias.append(
            'SUPABASE_URL contenía comillas, saltos de línea o espacios extra; se limpió.'
        )

    texto = re.sub(r'\s+', '', texto)

    if not re.match(r'^https?://', texto, re.IGNORECASE):
        if re.match(r'^[a-z0-9-]+\.supabase\.co', texto, re.IGNORECASE):
            texto = f'https://{texto}'
            resultado.advertencias.append(
                'SUPABASE_URL no tenía esquema; se añadió https:// automáticamente.'
            )
        else:
            resultado.errores.append(
                'SUPABASE_URL debe comenzar con https:// (p. ej. https://REF.supabase.co).'
            )
            return resultado

    texto = texto.rstrip('/')

    for path_erroneo in _PATHS_ERRONEOS:
        sufijo = path_erroneo.rstrip('/')
        if texto.lower().endswith(sufijo):
            texto = texto[: -len(sufijo)].rstrip('/')
            resultado.advertencias.append(
                f'SUPABASE_URL incluía el path {sufijo}; se normalizó al origen del proyecto.'
            )
            break

    parsed = urlparse(texto)
    host = (parsed.hostname or '').lower()
    resultado.host = host

    if parsed.scheme.lower() != 'https':
        resultado.advertencias.append(
            f'Esquema {parsed.scheme}: se recomienda https:// para Supabase en la nube.'
        )

    if not host:
        resultado.errores.append('SUPABASE_URL no contiene un hostname válido.')
        return resultado

    if not _HOST_SUPABASE_RE.match(host):
        resultado.advertencias.append(
            f'Host "{host}" no coincide con el patrón REF.supabase.co; verifica el valor en el dashboard.'
        )

    if parsed.username or parsed.password:
        resultado.errores.append(
            'SUPABASE_URL no debe incluir credenciales embebidas (user:pass@host).'
        )
        return resultado

    if parsed.query or parsed.fragment:
        resultado.advertencias.append(
            'SUPABASE_URL contenía query o fragmento; se descartó para usar solo el origen.'
        )

    if parsed.port and parsed.port not in (443, 80):
        resultado.advertencias.append(
            f'SUPABASE_URL especifica puerto {parsed.port}; lo habitual es omitir el puerto.'
        )

    netloc = host
    if parsed.port and parsed.port not in (443, 80):
        netloc = f'{host}:{parsed.port}'

    resultado.url = f'{parsed.scheme}://{netloc}'.rstrip('/')
    resultado.valida = not resultado.errores
    return resultado


def _clasificar_excepcion_red(error: Exception) -> str:
    nombre = type(error).__name__
    if nombre in ('ConnectError', 'ConnectTimeout', 'NetworkError'):
        return 'tcp/dns'
    if isinstance(error, ssl.SSLError):
        return 'ssl/tls'
    if isinstance(error, (socket.gaierror, TimeoutError, ConnectionError, OSError)):
        return 'dns'
    if isinstance(error, httpx.TimeoutException):
        return 'timeout'
    mensaje = str(error).lower()
    if any(
        frag in mensaje
        for frag in (
            'name or service not known',
            'getaddrinfo failed',
            'failed to resolve',
            'nodename nor servname',
            'temporary failure in name resolution',
        )
    ):
        return 'dns'
    if 'certificate' in mensaje or 'ssl' in mensaje or 'tls' in mensaje:
        return 'ssl/tls'
    return 'red'


def _probar_resolucion_dns(host: str) -> dict[str, Any]:
    if not host:
        return {'ok': False, 'capa': 'config', 'error': 'Host vacío'}

    inicio = time.perf_counter()
    try:
        registros = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        ips = sorted({registro[4][0] for registro in registros})
        return {
            'ok': True,
            'capa': 'dns',
            'host': host,
            'ips': ips,
            'latencia_ms': round((time.perf_counter() - inicio) * 1000, 1),
        }
    except socket.gaierror as error:
        return {
            'ok': False,
            'capa': 'dns',
            'host': host,
            'error': str(error),
            'latencia_ms': round((time.perf_counter() - inicio) * 1000, 1),
        }


def _timeout_http():
    return httpx.Timeout(connect=5.0, read=12.0, write=12.0, pool=5.0)


def _probar_endpoint_http(
    metodo: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    etiqueta: str = 'http',
) -> dict[str, Any]:
    inicio = time.perf_counter()
    try:
        transport = httpx.HTTPTransport(retries=0)
        with httpx.Client(
            timeout=_timeout_http(),
            transport=transport,
            follow_redirects=True,
        ) as cliente:
            respuesta = cliente.request(metodo, url, headers=headers or {})
        latencia = round((time.perf_counter() - inicio) * 1000, 1)
        codigo = respuesta.status_code
        ok_red = codigo < 500
        resultado = {
            'ok': ok_red,
            'capa': etiqueta,
            'url': url,
            'status': codigo,
            'latencia_ms': latencia,
            'detalle': respuesta.reason_phrase,
        }
        if codigo in (401, 403):
            resultado['auth_ok'] = False
            resultado['nota'] = (
                'La red responde pero la API rechazó la clave; revisa SUPABASE_KEY.'
            )
        else:
            resultado['auth_ok'] = True
        return resultado
    except Exception as error:
        capa = _clasificar_excepcion_red(error)
        return {
            'ok': False,
            'capa': capa,
            'url': url,
            'error': f'{type(error).__name__}: {error}',
            'latencia_ms': round((time.perf_counter() - inicio) * 1000, 1),
        }


def _recomendacion_fallo(resultado: dict[str, Any]) -> str:
    capa = resultado.get('capa_fallo')
    if capa == 'config':
        return (
            'Corrige SUPABASE_URL en Render: solo https://TU_REF.supabase.co '
            '(sin /rest/v1, sin barra final, sin comillas).'
        )
    if capa == 'dns':
        return (
            'El contenedor no resuelve el host de Supabase (DNS). Verifica SUPABASE_URL, '
            'reinicia el servicio en Render y confirma que no haya firewall saliente bloqueando *.supabase.co:443.'
        )
    if capa == 'ssl/tls':
        return (
            'Fallo TLS hacia Supabase. Revisa fecha/hora del servidor y que Python tenga '
            'certificados CA actualizados (certifi).'
        )
    if capa == 'timeout':
        return (
            'Timeout de red hacia Supabase. Puede ser latencia o restricción saliente en Render; '
            'el respaldo local en static/uploads/ seguirá operativo.'
        )
    if capa == 'http':
        return (
            'Hay conectividad TCP pero la API respondió con error HTTP. Revisa SUPABASE_KEY '
            'y que el proyecto Supabase no esté pausado.'
        )
    if capa == 'sdk':
        return (
            'La red responde pero el SDK de Python falló. Actualiza supabase/httpx en requirements '
            'y revisa los logs del cliente.'
        )
    return 'Revisa SUPABASE_URL, SUPABASE_KEY y el estado del proyecto en supabase.com/dashboard.'


def diagnosticar_conectividad_supabase(
    supabase_url: str | None = None,
    supabase_key: str | None = None,
    bucket: str | None = None,
    *,
    probar_sdk: bool = True,
) -> dict[str, Any]:
    """
    Prueba estricta: config -> DNS -> HTTP directo (REST + Storage) -> SDK opcional.
    No bloquea la aplicación; retorna un dict serializable para logs y /health.
    """
    from backend.supabase_client import (
        SUPABASE_BUCKET_IMAGENES,
        SUPABASE_KEY,
        SUPABASE_URL,
        obtener_cliente_storage,
    )

    url_raw = supabase_url if supabase_url is not None else SUPABASE_URL
    key = _limpiar_texto_env(supabase_key if supabase_key is not None else SUPABASE_KEY)
    bucket_nombre = (bucket or SUPABASE_BUCKET_IMAGENES or 'imagenes').strip('/')

    url_info = sanitizar_url_supabase(url_raw)
    informe: dict[str, Any] = {
        'ok': False,
        'config_ok': url_info.valida and bool(key),
        'url': url_info.url,
        'url_raw_presente': bool(_limpiar_texto_env(url_raw)),
        'host': url_info.host,
        'advertencias_config': url_info.advertencias,
        'errores_config': url_info.errores,
        'key_presente': bool(key),
        'capa_fallo': None,
        'recomendacion': '',
    }

    if not url_info.valida:
        informe['capa_fallo'] = 'config'
        informe['recomendacion'] = _recomendacion_fallo(informe)
        return informe

    if not key:
        informe['errores_config'].append('SUPABASE_KEY ausente para probar la API REST.')
        informe['capa_fallo'] = 'config'
        informe['recomendacion'] = _recomendacion_fallo(informe)
        return informe

    dns = _probar_resolucion_dns(url_info.host)
    informe['dns'] = dns
    if not dns.get('ok'):
        informe['capa_fallo'] = 'dns'
        informe['recomendacion'] = _recomendacion_fallo(informe)
        return informe

    headers_api = {
        'apikey': key,
        'Authorization': f'Bearer {key}',
    }

    rest = _probar_endpoint_http(
        'GET',
        f'{url_info.url}/rest/v1/',
        headers=headers_api,
        etiqueta='http_rest',
    )
    informe['http_rest'] = rest

    storage = _probar_endpoint_http(
        'GET',
        f'{url_info.url}/storage/v1/bucket',
        headers=headers_api,
        etiqueta='http_storage',
    )
    informe['http_storage'] = storage

    if not rest.get('ok'):
        informe['capa_fallo'] = rest.get('capa') or 'http'
        informe['recomendacion'] = _recomendacion_fallo(informe)
        return informe

    if probar_sdk:
        sdk_resultado: dict[str, Any] = {'ok': False, 'capa': 'sdk'}
        cliente = obtener_cliente_storage()
        if not cliente:
            sdk_resultado['error'] = 'Cliente Storage del SDK no inicializado.'
            informe['sdk_storage'] = sdk_resultado
            informe['capa_fallo'] = 'sdk'
            informe['recomendacion'] = _recomendacion_fallo(informe)
            return informe
        inicio = time.perf_counter()
        try:
            cliente.storage.from_(bucket_nombre).list()
            sdk_resultado = {
                'ok': True,
                'capa': 'sdk',
                'bucket': bucket_nombre,
                'latencia_ms': round((time.perf_counter() - inicio) * 1000, 1),
            }
        except Exception as error:
            capa = _clasificar_excepcion_red(error)
            sdk_resultado = {
                'ok': False,
                'capa': capa if capa != 'red' else 'sdk',
                'error': f'{type(error).__name__}: {error}',
                'latencia_ms': round((time.perf_counter() - inicio) * 1000, 1),
            }
        informe['sdk_storage'] = sdk_resultado
        if not sdk_resultado.get('ok'):
            informe['capa_fallo'] = sdk_resultado.get('capa') or 'sdk'
            informe['recomendacion'] = _recomendacion_fallo(informe)
            return informe

    informe['ok'] = True
    informe['capa_fallo'] = None
    informe['recomendacion'] = 'Conectividad con Supabase verificada.'
    return informe


def imprimir_diagnostico_conectividad(informe: dict[str, Any]) -> None:
    """Log legible en consola (Render logs)."""
    if informe.get('ok'):
        rest_ms = (informe.get('http_rest') or {}).get('latencia_ms')
        print(
            f'{LOG_PREFIX} Conectividad OK host={informe.get("host")} '
            f'rest={rest_ms}ms'
        )
        return

    capa = informe.get('capa_fallo') or 'desconocida'
    print(
        f'{LOG_PREFIX} FALLO capa={capa} host={informe.get("host") or "?"} '
        f'| {informe.get("recomendacion") or ""}'
    )
    for aviso in informe.get('advertencias_config') or []:
        print(f'{LOG_PREFIX} Aviso config: {aviso}')
    for err in informe.get('errores_config') or []:
        print(f'{LOG_PREFIX} Error config: {err}')
    for prueba in ('dns', 'http_rest', 'http_storage', 'sdk_storage'):
        detalle = informe.get(prueba)
        if detalle and not detalle.get('ok'):
            print(
                f'{LOG_PREFIX} {prueba}: {detalle.get("error") or detalle.get("status")}'
            )
