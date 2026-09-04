# Imágenes de producto (Localis)

Hay **dos caminos**, sin mezclarlos.

## 1. Foto manual (prioridad, costo API = 0)

Si el comerciante sube un archivo desde el dispositivo, se comprime y se guarda en el bucket Supabase `imagenes` (`productos/…`) o, si Storage no está disponible, en `/static/uploads/productos/`.

No se consulta ninguna API externa.

## 2. Pipeline automático (pago por consumo)

Módulo: `services/smart_image_pipeline.py`.

Se activa **solo** cuando el producto queda sin foto manual (alta, edición o CSV diferido). El request HTTP no espera a la API: corre en un hilo daemon.

1. Lookup por EAN/UPC
2. Búsqueda por nombre + marca (tecnología / consumo estándar; nombres genéricos como “martillo” no se consultan)
3. Placeholder `/static/img/placeholder-producto.svg`

**No se descarga el archivo ni se usa Pillow** en este camino. Se persiste la URL HTTPS que devuelve la API. El navegador la consume directo. El bucket de Storage queda reservado a fotos de usuarios.

### Credenciales

En `.env` / Render, al menos una:

```
BARCODE_SPIDER_API_KEY=
UPCITEMDB_API_KEY=
BARCODE_LOOKUP_API_KEY=
```

Sin clave el pipeline no cobra nada y asigna placeholder. Proveedores con créditos prepagados, sin mensualidad fija en el código.

## Qué no hace el sistema

- No hay rembg / recorte por IA
- No hay scraping de Google/Bing ni Open Food Facts
- El listado público **no** dispara APIs (evita gastar créditos y saturar Render)
