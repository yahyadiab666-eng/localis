"""Datos estructurados (schema.org JSON-LD) para SEO.

Genera el JSON-LD de tienda, producto y sitio que los buscadores usan para
mostrar **resultados enriquecidos** (nombre, precio, disponibilidad, teléfono,
búsqueda con caja). Es la parte que faltaba del SEO: sitemap, robots y meta/OG ya
existían.

Nunca lanza: ante cualquier duda devuelve cadena vacía (no rompe la página).
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger('localis.seo')

_LOG = '[Localis SEO]'


def _base(base_url):
    return (base_url or '').rstrip('/')


def _img_abs(url, base):
    url = (url or '').strip()
    if not url:
        return None
    if url.startswith('http'):
        return url
    return f'{base}{url if url.startswith("/") else "/" + url}'


def json_ld_store(comercio, base_url, producto=None):
    """JSON-LD de la tienda (``Store``) y, si hay producto, del ``Product``.

    Devuelve una lista (grafo) serializada, o '' si algo falla.
    """
    try:
        comercio = dict(comercio or {})
        base = _base(base_url)
        slug = str(comercio.get('slug') or '').strip()
        url = f'{base}/tienda/{slug}' if slug else f'{base}/tienda/{comercio.get("id")}'

        tienda = {
            '@type': 'Store',
            'name': (comercio.get('nombre') or '').strip() or 'Tienda',
            'url': url,
        }
        desc = (comercio.get('descripcion') or '').strip()
        if desc:
            tienda['description'] = desc
        img = _img_abs(
            comercio.get('banner_completo')
            or comercio.get('imagen_url')
            or comercio.get('logo_completo'),
            base,
        )
        if img:
            tienda['image'] = img
        telefono = (comercio.get('telefono') or '').strip()
        if telefono:
            tienda['telephone'] = telefono

        direccion = {'@type': 'PostalAddress'}
        if (comercio.get('direccion') or '').strip():
            direccion['streetAddress'] = comercio['direccion'].strip()
        if (comercio.get('ciudad') or '').strip():
            direccion['addressLocality'] = comercio['ciudad'].strip()
        if (comercio.get('zona') or '').strip():
            direccion['addressRegion'] = comercio['zona'].strip()
        if len(direccion) > 1:
            tienda['address'] = direccion

        ciudad = (comercio.get('ciudad') or '').strip()
        if ciudad:
            tienda['areaServed'] = ciudad

        grafo = [tienda]

        if producto:
            p = dict(producto)
            prod = {
                '@type': 'Product',
                'name': (p.get('nombre') or '').strip() or 'Producto',
            }
            pdesc = (p.get('descripcion') or '').strip()
            if pdesc:
                prod['description'] = pdesc
            pimg = _img_abs(p.get('imagen_url'), base)
            if pimg:
                prod['image'] = pimg
            oferta = {
                '@type': 'Offer',
                'availability': 'https://schema.org/InStock',
                'url': url,
            }
            try:
                oferta['price'] = float(p.get('precio_usd'))
                oferta['priceCurrency'] = 'USD'
            except (TypeError, ValueError):
                pass
            if 'price' in oferta:
                prod['offers'] = oferta
            grafo.append(prod)

        return json.dumps(grafo, ensure_ascii=False)
    except Exception as error:
        logger.error(f'{_LOG} json_ld_store: {type(error).__name__}: {error}')
        return ''


def json_ld_website(base_url, nombre='Localis'):
    """JSON-LD del sitio con caja de búsqueda (``WebSite`` + ``SearchAction``)."""
    try:
        base = _base(base_url)
        datos = {
            '@context': 'https://schema.org',
            '@type': 'WebSite',
            'name': nombre,
            'url': base,
            'potentialAction': {
                '@type': 'SearchAction',
                'target': {
                    '@type': 'EntryPoint',
                    'urlTemplate': f'{base}/?q={{search_term_string}}',
                },
                'query-input': 'required name=search_term_string',
            },
        }
        return json.dumps(datos, ensure_ascii=False)
    except Exception as error:
        logger.error(f'{_LOG} json_ld_website: {error}')
        return ''
