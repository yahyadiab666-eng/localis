"""Sanitización de SUPABASE_URL y pruebas de conectividad HTTP/DNS (sin depender del SDK)."""

from __future__ import annotations

import os
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

LOG_PREFIX = '[Localis Supabase Red]'
_SUFIJO_HOST_SUPABASE = '.supabase.co'
_RE_HOST_SUPABASE = re.compile(r'(?:[a-z0-9-]+\.)+supabase\.co', re.IGNORECASE)
_RE_ESQUEMAS_INICIALES = re.compile(r'^(?:https?://)+', re.IGNORECASE)
_CHARS_INVISIBLES_URL = (
    '\ufeff',  # BOM
    '\u200b',  # zero-width space
    '\u200c',
    '\u200d',
    '\u2060',
    '\u00ad',  # soft hyphen
)
_RE_ESQUEMA_URL = re.compile(r'^(https?)://', re.IGNORECASE)
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


def _quitar_caracteres_invisibles(texto: str) -> str:
    resultado = str(texto or '')
    for caracter in _CHARS_INVISIBLES_URL:
        resultado = resultado.replace(caracter, '')
    return resultado


def _limpiar_texto_env(valor):
    if valor is None:
        return ''
    texto = str(valor).strip().strip('"').strip("'").replace('\r', '').replace('\n', '')
    texto = _quitar_caracteres_invisibles(texto)
    return texto.strip()


def _normalizar_esquema_https(texto: str) -> tuple[str, list[str]]:
    """Fuerza https:// sin importar mayusculas en el esquema; evita doble prefijo."""
    advertencias: list[str] = []
    candidato = texto.strip()
    coincidencia = _RE_ESQUEMAS_INICIALES.match(candidato)
    if not coincidencia:
        return candidato, advertencias

    esquemas = re.findall(r'https?://', coincidencia.group(0), flags=re.IGNORECASE)
    resto = candidato[coincidencia.end() :].lstrip('/')
    if len(esquemas) > 1:
        advertencias.append(
            'SUPABASE_URL tenia el esquema https:// duplicado al inicio; se dejo uno solo.'
        )
    elif esquemas and esquemas[0].lower().startswith('http://'):
        advertencias.append('SUPABASE_URL usaba http://; se normalizo a https://.')
    return f'https://{resto}', advertencias


def _normalizar_host_supabase(host: str | None) -> str:
    """Host en minusculas, sin puerto, puntos finales ni caracteres invisibles."""
    host_limpio = _quitar_caracteres_invisibles((host or '').strip()).lower()
    return host_limpio.rstrip('.')


def _es_host_supabase_oficial(host: str | None) -> bool:
    """Cualquier subdominio de *.supabase.co (p. ej. wesnnnvoavprgqcczzsg.supabase.co)."""
    host_normalizado = _normalizar_host_supabase(host)
    if not host_normalizado or host_normalizado == 'supabase.co':
        return False
    return host_normalizado.endswith(_SUFIJO_HOST_SUPABASE)


def _extraer_host_desde_texto_url(texto: str) -> str:
    """Extrae hostname sin puerto; tolera esquemas en mayusculas y host plano sin https://."""
    parsed = urlparse(texto)
    if parsed.hostname:
        return _normalizar_host_supabase(parsed.hostname)

    candidato = texto.split('://', 1)[-1]
    candidato = candidato.split('/', 1)[0]
    candidato = candidato.split('@', 1)[-1]
    candidato = candidato.split(':', 1)[0]
    return _normalizar_host_supabase(candidato)


@dataclass
class ResultadoUrlSupabase:
    url: str = ''
    host: str = ''
    project_ref: str = ''
    valida: bool = False
    id_sospechoso: bool = False
    url_recomendada: str = ''
    advertencias: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)


def extraer_ref_proyecto_de_host(host: str | None) -> str:
    """Extrae REF de REF.supabase.co (o db.REF.supabase.co)."""
    texto = _normalizar_host_supabase(host)
    if not _es_host_supabase_oficial(texto) or texto == 'supabase.co':
        return ''
    prefijo = texto[: -len(_SUFIJO_HOST_SUPABASE)]
    if prefijo.startswith('db.') and '.' not in prefijo[3:]:
        return prefijo[3:]
    return prefijo


def url_canonica_proyecto(ref: str | None) -> str:
    """Origen API de un project ref: https://REF.supabase.co."""
    texto = (ref or '').strip().lower()
    if not texto or '.' in texto:
        return ''
    return f'https://{texto}{_SUFIJO_HOST_SUPABASE}'


def _extraer_hosts_supabase(texto: str) -> list[str]:
    return [
        _normalizar_host_supabase(coincidencia.group(0))
        for coincidencia in _RE_HOST_SUPABASE.finditer(texto or '')
    ]


def _colapsar_host_malformado(host: str) -> tuple[str, list[str]]:
    """
    Quita prefijos duplicados o de Postgres: REF.REF.supabase.co y db.REF.supabase.co
    pasan a REF.supabase.co.
    """
    advertencias: list[str] = []
    texto = _normalizar_host_supabase(host)
    if not _es_host_supabase_oficial(texto) or texto == 'supabase.co':
        return texto, advertencias

    prefijo = texto[: -len(_SUFIJO_HOST_SUPABASE)]
    if prefijo.startswith('db.') and '.' not in prefijo[3:]:
        canonico = f'{prefijo[3:]}{_SUFIJO_HOST_SUPABASE}'
        advertencias.append(
            f'Host "{texto}" es el de Postgres (db.*); se uso el origen API {canonico}.'
        )
        return canonico, advertencias

    partes = [parte for parte in prefijo.split('.') if parte]
    if len(partes) >= 2 and all(parte == partes[0] for parte in partes):
        canonico = f'{partes[0]}{_SUFIJO_HOST_SUPABASE}'
        advertencias.append(
            f'Host "{texto}" tenia el project ref duplicado como prefijo; '
            f'se normalizo a {canonico}.'
        )
        return canonico, advertencias

    return texto, advertencias


def _elegir_host_canonico(
    hosts: list[str],
    ref_db: str = '',
) -> tuple[str, list[str], bool]:
    advertencias: list[str] = []
    sospechoso = False
    candidatos: list[str] = []
    vistos: set[str] = set()

    for host in hosts:
        colapsado, avisos = _colapsar_host_malformado(host)
        advertencias.extend(avisos)
        if avisos:
            sospechoso = True
        if not colapsado or colapsado in vistos:
            continue
        vistos.add(colapsado)
        candidatos.append(colapsado)

    if not candidatos:
        return '', advertencias, sospechoso

    if len(candidatos) > 1:
        sospechoso = True
        advertencias.append(
            'SUPABASE_URL contenia mas de un host *.supabase.co; '
            'se eligio el origen API del proyecto.'
        )

    if ref_db:
        esperado = f'{ref_db}{_SUFIJO_HOST_SUPABASE}'
        for candidato in candidatos:
            if candidato == esperado or extraer_ref_proyecto_de_host(candidato) == ref_db:
                return esperado, advertencias, sospechoso

    for candidato in candidatos:
        ref = extraer_ref_proyecto_de_host(candidato)
        if ref and '.' not in ref:
            return candidato, advertencias, sospechoso

    return candidatos[-1], advertencias, sospechoso


def extraer_ref_proyecto_de_database_url(database_url: str | None) -> str:
    """
    Obtiene el project ref desde DATABASE_URL de Supabase.
    Formatos: db.REF.supabase.co o usuario postgres.REF en pooler.
    """
    from urllib.parse import unquote

    valor = _limpiar_texto_env(database_url)
    if not valor:
        return ''

    parsed = urlparse(valor)
    usuario = unquote(parsed.username or '')
    if usuario.startswith('postgres.') and len(usuario) > len('postgres.'):
        return usuario.split('.', 1)[1].strip().lower()

    host = (parsed.hostname or '').lower()
    if host.startswith('db.') and host.endswith(_SUFIJO_HOST_SUPABASE):
        return host[3 : -len(_SUFIJO_HOST_SUPABASE)]
    return ''


def _advertir_mismatch_database_url(ref: str, database_url: str | None = None) -> list[str]:
    ref_db = extraer_ref_proyecto_de_database_url(database_url)
    if ref_db and ref and ref_db != ref:
        return [
            f'SUPABASE_URL usa "{ref}" pero DATABASE_URL usa "{ref_db}". '
            'Verifica que ambas apunten al mismo proyecto si estan configuradas.'
        ]
    return []


def imprimir_alerta_supabase_url(resultado: ResultadoUrlSupabase, url_raw: str | None = None) -> None:
    """Aviso en consola solo ante errores de formato o sanitizacion relevante."""
    if not resultado.errores and not resultado.advertencias:
        return

    raw = _limpiar_texto_env(url_raw)
    if resultado.valida and not resultado.errores:
        for aviso in resultado.advertencias:
            print(f'{LOG_PREFIX} URL: {aviso}')
        return

    print(f'{LOG_PREFIX} ===== REVISION SUPABASE_URL =====')
    if raw and raw != resultado.url:
        print(f'{LOG_PREFIX} Valor en entorno (raw): {raw}')
    if resultado.url:
        print(f'{LOG_PREFIX} URL sanitizada: {resultado.url}')
    if resultado.project_ref:
        print(f'{LOG_PREFIX} ID de proyecto detectado: {resultado.project_ref}')
    if resultado.id_sospechoso:
        print(
            f'{LOG_PREFIX} ID/host sospechoso: el valor de Render tenia prefijo '
            'duplicado o basura pegada al inicio.'
        )

    for error in resultado.errores:
        print(f'{LOG_PREFIX} ERROR: {error}')
    for aviso in resultado.advertencias:
        print(f'{LOG_PREFIX} AVISO: {aviso}')

    recomendada = resultado.url_recomendada or resultado.url
    if recomendada:
        print(
            f'{LOG_PREFIX} Accion: en Render -> Environment -> SUPABASE_URL deja exactamente '
            f'{recomendada} (https://REF.supabase.co, sin comillas, sin /rest/v1, '
            'sin host pegado delante).'
        )
    else:
        print(
            f'{LOG_PREFIX} Accion: abre Supabase Dashboard -> Settings -> API -> '
            'Project URL y copia exactamente https://TU_REF.supabase.co en Render '
            '(variable SUPABASE_URL, sin comillas ni /rest/v1).'
        )
    print(f'{LOG_PREFIX} =================================')


def sanitizar_url_supabase(
    raw_url: str | None,
    *,
    database_url: str | None = None,
) -> ResultadoUrlSupabase:
    """
    Normaliza SUPABASE_URL para evitar fallos DNS por barras finales, paths extra,
    esquemas duplicados o prefijos pegados al copiar en Render.
    """
    resultado = ResultadoUrlSupabase()
    texto = _limpiar_texto_env(raw_url)
    ref_db = extraer_ref_proyecto_de_database_url(database_url)
    if ref_db:
        resultado.url_recomendada = url_canonica_proyecto(ref_db)

    if not texto:
        resultado.errores.append('SUPABASE_URL vacia o ausente.')
        return resultado

    if texto != _quitar_caracteres_invisibles(str(raw_url or '').strip()):
        resultado.advertencias.append(
            'SUPABASE_URL contenia comillas, saltos de linea, espacios extra o caracteres '
            'invisibles; se limpio.'
        )

    texto = re.sub(r'\s+', '', texto)
    texto = re.sub(r'(\.supabase\.co)\.+', r'\1', texto, flags=re.IGNORECASE)

    coincidencia_esquema = _RE_ESQUEMA_URL.search(texto)
    if coincidencia_esquema and coincidencia_esquema.start() > 0:
        prefijo_basura = texto[: coincidencia_esquema.start()]
        texto = texto[coincidencia_esquema.start() :]
        resultado.id_sospechoso = True
        resultado.advertencias.append(
            f'SUPABASE_URL tenia el prefijo invalido "{prefijo_basura}" pegado antes de '
            'https://; se descarto.'
        )

    if not _RE_ESQUEMA_URL.search(texto):
        if _SUFIJO_HOST_SUPABASE in texto.lower():
            texto = f'https://{texto.lstrip("/")}'
            resultado.advertencias.append(
                'SUPABASE_URL no tenia esquema; se anadio https:// automaticamente.'
            )
        else:
            resultado.errores.append(
                'SUPABASE_URL debe comenzar con https:// y apuntar a *.supabase.co.'
            )
            return resultado

    texto, advertencias_esquema = _normalizar_esquema_https(texto)
    resultado.advertencias.extend(advertencias_esquema)
    if any('duplicado' in aviso for aviso in advertencias_esquema):
        resultado.id_sospechoso = True
    texto = texto.rstrip('/')

    for path_erroneo in _PATHS_ERRONEOS:
        sufijo = path_erroneo.rstrip('/')
        if texto.lower().endswith(sufijo):
            texto = texto[: -len(sufijo)].rstrip('/')
            resultado.advertencias.append(
                f'SUPABASE_URL incluia el path {sufijo}; se normalizo al origen del proyecto.'
            )
            break

    parsed = urlparse(texto)
    host_parseado = _extraer_host_desde_texto_url(texto)
    hosts_detectados = _extraer_hosts_supabase(texto)
    if host_parseado and host_parseado not in hosts_detectados:
        hosts_detectados.insert(0, host_parseado)

    # Preferir el hostname parseado si ya es *.supabase.co; no descartarlo.
    if _es_host_supabase_oficial(host_parseado):
        host = host_parseado
        avisos_host: list[str] = []
        sospechoso_host = False
    else:
        host, avisos_host, sospechoso_host = _elegir_host_canonico(
            hosts_detectados, ref_db
        )
    resultado.advertencias.extend(avisos_host)
    if sospechoso_host:
        resultado.id_sospechoso = True
    resultado.host = host

    if not host:
        resultado.errores.append('SUPABASE_URL no contiene un hostname valido.')
        return resultado

    if not _es_host_supabase_oficial(host):
        host_mostrado = _quitar_caracteres_invisibles(parsed.hostname or host)
        resultado.errores.append(
            f'Host "{host_mostrado}" invalido: la URL debe apuntar a un subdominio de '
            f'{_SUFIJO_HOST_SUPABASE} (por ejemplo https://TU_REF.supabase.co).'
        )
        return resultado

    ref = extraer_ref_proyecto_de_host(host)
    resultado.advertencias.extend(
        _advertir_mismatch_database_url(ref, database_url)
    )

    if parsed.username or parsed.password:
        resultado.advertencias.append(
            'SUPABASE_URL incluia credenciales embebidas; se usara solo el origen https.'
        )

    if parsed.query or parsed.fragment:
        resultado.advertencias.append(
            'SUPABASE_URL contenia query o fragmento; se descarto para usar solo el origen.'
        )

    if parsed.port and parsed.port not in (443, 80):
        resultado.advertencias.append(
            f'SUPABASE_URL especifica puerto {parsed.port}; se omitio al reconstruir la URL canonica.'
        )

    # Cualquier *.supabase.co es valido: asignar siempre el origen, no vaciar.
    resultado.project_ref = ref
    resultado.url = f'https://{host}'
    resultado.url_recomendada = resultado.url
    resultado.valida = True
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
            'name not resolved',
            'nameresolutionerror',
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
        recomendada = resultado.get('url_recomendada')
        if recomendada:
            return (
                f'Corrige SUPABASE_URL en Render: deja exactamente {recomendada} '
                '(sin /rest/v1, sin barra final, sin comillas ni prefijo pegado).'
            )
        return (
            'Corrige SUPABASE_URL en Render: solo https://TU_REF.supabase.co '
            '(sin /rest/v1, sin barra final, sin comillas).'
        )
    if capa == 'dns':
        recomendada = resultado.get('url_recomendada')
        ref = extraer_ref_proyecto_de_host(resultado.get('host'))
        base = (
            'El contenedor no resuelve el host de Supabase (DNS / name not resolved). '
            'Verifica SUPABASE_URL en Render y confirma que coincida con '
            'Settings -> API en supabase.com/dashboard.'
        )
        if recomendada:
            return f'{base} Valor correcto: {recomendada}'
        if ref:
            return f'{base} Host actual: {ref}.supabase.co'
        return base
    if capa == 'ssl/tls':
        return (
            'Fallo TLS hacia Supabase. Revisa fecha/hora del servidor y que Python tenga '
            'certificados CA actualizados (certifi).'
        )
    if capa == 'timeout':
        return (
            'Timeout de red hacia Supabase. Puede ser latencia o restriccion saliente en Render; '
            'las subidas a Storage fallaran con SupabaseUploadError hasta restablecer la conexion.'
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

    url_info = sanitizar_url_supabase(url_raw, database_url=os.getenv('DATABASE_URL'))
    informe: dict[str, Any] = {
        'ok': False,
        'config_ok': url_info.valida and bool(key),
        'url': url_info.url,
        'url_raw_presente': bool(_limpiar_texto_env(url_raw)),
        'host': url_info.host,
        'project_ref': url_info.project_ref,
        'id_sospechoso': url_info.id_sospechoso,
        'url_recomendada': url_info.url_recomendada,
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
