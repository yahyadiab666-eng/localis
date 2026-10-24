"""Base de conocimiento del asistente (recuperacion lexica en memoria).

Indexa la documentacion del producto y una FAQ curada, y devuelve los
fragmentos mas relevantes para una consulta usando BM25 en Python puro. Sin
red, sin embeddings y sin dependencias nuevas: el corpus es pequeno.

Nunca lanza: ante cualquier error devuelve una lista vacia (el asistente sigue
funcionando, solo con menos contexto). El indice se construye una vez y se
guarda en memoria del worker.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import unicodedata

from config import RUTA_RAIZ, fase_gratuita

_log = logging.getLogger('localis.asistente.conocimiento')

# Marcadores de bloques que SOLO se indexan cuando los planes estan activos.
# En la fase gratuita (planes ocultos) su contenido se excluye del RAG, pero el
# texto NO se borra de los documentos: se reactiva con LOCALIS_PAGOS_HABILITADOS=1.
_MARCADORES_PLANES = (
    ('<!-- PLANES_INICIO -->', '<!-- PLANES_FIN -->'),
    ('<!-- RESTAURAR_PAGOS_INICIO -->', '<!-- RESTAURAR_PAGOS_FIN -->'),
)
# Marcadores de bloques que SOLO se indexan en la fase gratuita (p. ej. la
# revalidacion centrada en DESTACADOS, sin planes). Con los planes activos se
# excluyen, pero el texto permanece en el documento.
_MARCADORES_GRATIS = (
    ('<!-- GRATIS_INICIO -->', '<!-- GRATIS_FIN -->'),
)


def _quitar_bloques_planes(texto):
    """Maneja los bloques segun la fase. Nunca lanza.

    - Bloques ``PLANES_INICIO/FIN``: en fase gratuita se elimina el bloque
      completo; con planes activos solo se quitan los comentarios-marcador.
    - Bloques ``GRATIS_INICIO/FIN``: al reves (solo se indexan en fase gratuita).
    """
    try:
        gratis = fase_gratuita()
        for inicio, fin in _MARCADORES_PLANES:
            patron = re.escape(inicio) + r'([\s\S]*?)' + re.escape(fin)
            texto = re.sub(patron, ' ' if gratis else r'\1', texto)
        for inicio, fin in _MARCADORES_GRATIS:
            patron = re.escape(inicio) + r'([\s\S]*?)' + re.escape(fin)
            texto = re.sub(patron, r'\1' if gratis else ' ', texto)
        return texto
    except Exception:
        return texto

# Orden = prioridad de indexado. Los que no existan se omiten sin fallar.
# Solo contenido orientado al USUARIO: los documentos de desarrollo
# (.env.example, AGENTS.md, docs/arquitectura.md...) meten ruido y empeoran la
# recuperacion de preguntas reales ("cuesta", "plan", "pago"...).
_ARCHIVOS = (
    'docs/asistente-app.md',
    'docs/asistente-faq.md',
    'templates/como_funciona.html',
    'templates/privacidad.html',
    'templates/eliminar_cuenta.html',
)

_LARGO_CHUNK = 1200
_LARGO_MINIMO = 40

_INDICE = None
_LOCK = threading.Lock()

_STOPWORDS = frozenset(
    'de la el los las un una unos unas y o u que en con por para sin sobre entre '
    'al del lo se su sus mi mis tu te me le les nos es son esta estan este esto '
    'como cuando donde cual cuales mas menos muy no ni si ya pero tambien solo '
    'cada todo toda todos todas hay ser estoy hacer tiene tienen puedo puede '
    'pueden yo cuanto cuanta cuantos cuantas cuesta cuestan vale uno'.split()
)


def _normalizar(texto):
    """Minusculas sin acentos (para que 'imagenes' == 'imagenes')."""
    if not isinstance(texto, str):
        return ''
    base = unicodedata.normalize('NFKD', texto)
    base = ''.join(c for c in base if not unicodedata.combining(c))
    return base.lower()


def _tokens(texto):
    return [
        t for t in re.findall(r'[a-z0-9]{3,}', _normalizar(texto))
        if t not in _STOPWORDS
    ]


def _texto_de_archivo(ruta):
    try:
        with open(ruta, 'r', encoding='utf-8', errors='ignore') as archivo:
            crudo = archivo.read()
    except Exception:
        return ''
    # Antes de limpiar etiquetas: quita los bloques de planes en fase gratuita.
    crudo = _quitar_bloques_planes(crudo)
    if ruta.endswith('.html'):
        crudo = re.sub(r'(?is)<(script|style).*?</\1>', ' ', crudo)
        crudo = re.sub(r'(?s)<[^>]+>', ' ', crudo)
        # Quita la sintaxis Jinja ({{ ... }} y {% ... %}) para que no ensucie el
        # texto recuperable de las plantillas.
        crudo = re.sub(r'(?s)\{\{.*?\}\}', ' ', crudo)
        crudo = re.sub(r'(?s)\{%.*?%\}', ' ', crudo)
        crudo = crudo.replace('&nbsp;', ' ').replace('&amp;', '&')
    return crudo


def _chunks(texto, fuente):
    """Parte un documento por encabezados y parrafos en trozos manejables."""
    trozos = []
    titulo_actual = fuente
    for bloque in re.split(r'\n(?=#{1,4}\s)', texto):
        encabezado = re.match(r'#{1,4}\s+(.*)', bloque)
        if encabezado:
            titulo_actual = encabezado.group(1).strip() or titulo_actual
        buffer = ''
        for parrafo in re.split(r'\n\s*\n', bloque):
            parrafo = parrafo.strip()
            if not parrafo:
                continue
            if buffer and len(buffer) + len(parrafo) + 1 > _LARGO_CHUNK:
                trozos.append((titulo_actual, buffer.strip()))
                buffer = parrafo
            else:
                buffer = f'{buffer}\n{parrafo}' if buffer else parrafo
        if buffer.strip():
            trozos.append((titulo_actual, buffer.strip()))
    return [
        (fuente, titulo, cuerpo)
        for titulo, cuerpo in trozos
        if len(cuerpo) >= _LARGO_MINIMO
    ]


def _construir():
    docs = []
    for relativo in _ARCHIVOS:
        ruta = os.path.join(RUTA_RAIZ, relativo)
        if not os.path.exists(ruta):
            continue
        contenido = _texto_de_archivo(ruta)
        if not contenido.strip():
            continue
        for fuente, titulo, cuerpo in _chunks(contenido, relativo):
            toks = _tokens(f'{titulo} {cuerpo}')
            if not toks:
                continue
            tf = {}
            for token in toks:
                tf[token] = tf.get(token, 0) + 1
            docs.append(
                {
                    'fuente': fuente,
                    'titulo': titulo or fuente,
                    'texto': cuerpo,
                    'tf': tf,
                    'dl': len(toks),
                }
            )
    df = {}
    for doc in docs:
        for token in doc['tf']:
            df[token] = df.get(token, 0) + 1
    total = len(docs)
    promedio = (sum(d['dl'] for d in docs) / total) if total else 0.0
    return {'docs': docs, 'df': df, 'n': total, 'avgdl': promedio or 1.0}


def obtener_indice():
    """Construye (una sola vez) y devuelve el indice en memoria."""
    global _INDICE
    if _INDICE is not None:
        return _INDICE
    with _LOCK:
        if _INDICE is None:
            try:
                _INDICE = _construir()
            except Exception as error:
                _log.warning('no se pudo construir el indice: %s', error)
                _INDICE = {'docs': [], 'df': {}, 'n': 0, 'avgdl': 1.0}
    return _INDICE


def buscar(consulta, k=4):
    """Devuelve hasta ``k`` fragmentos relevantes. Nunca lanza."""
    try:
        indice = obtener_indice()
        terminos = _tokens(consulta)
        if not indice['docs'] or not terminos:
            return []
        n = indice['n']
        avgdl = indice['avgdl']
        k1, b = 1.5, 0.75
        resultados = []
        for doc in indice['docs']:
            score = 0.0
            for token in set(terminos):
                tf = doc['tf'].get(token)
                if not tf:
                    continue
                df = indice['df'].get(token, 0)
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                score += idf * (tf * (k1 + 1)) / (
                    tf + k1 * (1 - b + b * doc['dl'] / avgdl)
                )
            if score > 0:
                resultados.append((score, doc))
        resultados.sort(key=lambda par: par[0], reverse=True)
        return [{**doc, 'score': round(score, 4)} for score, doc in resultados[:k]]
    except Exception as error:
        _log.warning('busqueda de conocimiento fallo: %s', error)
        return []


def contexto_para(consulta, k=4, max_chars=4000):
    """Texto con los fragmentos mas relevantes, listo para el prompt."""
    fragmentos = buscar(consulta, k=k)
    if not fragmentos:
        return ''
    partes = []
    usado = 0
    for fragmento in fragmentos:
        bloque = f'[{fragmento["titulo"]}]\n{fragmento["texto"]}'
        if partes and usado + len(bloque) > max_chars:
            break
        partes.append(bloque)
        usado += len(bloque)
    return '\n\n---\n\n'.join(partes)
