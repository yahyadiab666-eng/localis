# Arquitectura de Localis

Documento de referencia rápido. La fuente de verdad operativa es `AGENTS.md`
(cerca del código) y `.env.example`.

## Vista general

```
Navegador / TWA ──HTTPS──> Flask (main.py, 1 worker gunicorn)
                                │
                                ├─ Jinja2 (templates/)  + static/ directo
                                │
                                ├─ backend/   lógica de negocio (SQL crudo)
                                │
                                ├─ PostgreSQL (Supabase vía pgbouncer)
                                │
                                └─ Supabase Storage (imágenes)
                                   + servicios externos (Serper, BCV, ...)
```

- **Server-rendered**: no hay SPA ni bundler. La app global vive en `main.py`
  (no hay factory `create_app`).
- **Monolito modular**: `main.py` conserva la app, filtros y rutas públicas
  (portada, tienda, salud, sitemap); el resto está repartido en **blueprints**
  por dominio (`backend/blueprints/`) y en módulos de negocio (`backend/`),
  imágenes (`services/`) y helpers (`utils/`).

### Blueprints (`backend/blueprints/`)

| Blueprint | Prefijo | Responsabilidad |
|---|---|---|
| `api` | `/api` | endpoints JSON |
| `admin` | `/admin` | panel de administración |
| `comercio` | `/comercio` | panel del comerciante, catálogo, CSV, premios |
| `auth` | (raíz) | login Google, callback OAuth, perfil, logout |

Los endpoints pasan a llamarse `blueprint.endpoint` (p. ej.
`comercio.panel_comercio`). Los helpers de vista compartidos viven en
`backend/web_seguridad.py`, `backend/vistas_imagenes.py`,
`backend/vistas_comercio.py`, `backend/oauth_google.py`, `backend/rate_limit.py`
y `backend/observabilidad.py`.

## Flujo de una petición

1. `@app.before_request` sincroniza vencimientos de suscripción (en segundo
   plano, nunca bloquea la respuesta).
2. La ruta (protegida por sesión / `@admin_requerido` según corresponda).
3. Respuestas pasan por `@app.after_request`: caché de estáticos y **cabeceras
   de seguridad** (`main.py`).
4. Código defensivo: el camino de request captura excepciones y devuelve un
   fallback; **logging estructurado** (`logging`, `localis.<módulo>`), con
   request-id y salida JSON opt-in (`LOCALIS_LOG_JSON=1`).

## Base de datos

- **Solo PostgreSQL** vía `psycopg2`. `DATABASE_URL` es obligatoria.
- SQL con marcadores **`?`**; `database._adapt_sql` los convierte a `%s` y
  reescribe sintaxis SQLite heredada (`ON CONFLICT`, `datetime('now')`, ...).
- `init_db()` es **aditivo e idempotente**: crea tablas, añade columnas y
  reconcilia índices/estados. Usa un **advisory lock de transacción**
  (`pg_try_advisory_xact_lock`) para que un solo proceso migre a la vez; se
  libera solo y no queda "pegado" en pgbouncer.
- `schema.sql` es **referencia**, no la fuente de verdad (lo es `init_db`).

## Estado en memoria del worker (importante)

Viven **por proceso**: la cola de importación (`backend/import_queue.py`), el
índice del catálogo maestro (`backend/catalogo_maestro_index.py`), las cachés
diarias y el backfill de imágenes. Por eso:

> **`WEB_CONCURRENCY=1` es obligatorio** (`gunicorn.conf.py`). Subirlo rompe el
> polling de importaciones. Para varios procesos hace falta un broker
> compartido (Redis/RQ) y sacar el pipeline de imágenes a un worker aparte.

## Flujos de negocio destacados

### Pre-cuenta y transferencia de propiedad (`backend/pre_cuentas.py`)
- El admin crea una **pre-cuenta sin dueño ni correo**.
- **Transferir** = tratar la tienda como nueva: plan gratis, publicada, slug;
  el correo del dueño se guarda y se enlaza en el primer login (Google).
- **Reasignar** = cambiar el dueño de una tienda ya normal, **sin** tocar plan
  ni fechas.
- Regla **1 tienda por usuario**; el rol se recalcula (`user` ↔ `comerciante`).
- Códigos públicos visibles (`USR-`, `COM-`, `PROD-`) vía contador atómico
  (`backend/codigos_publicos.py`).

### Imágenes (`services/professional_image_pipeline.py`)
Prioridad: **manual del comerciante > catálogo maestro en memoria > placeholder
por categoría**. Corre en segundo plano, nunca en el request. Detalle en
`docs/README-imagenes.md`.

### Importación CSV/Excel (`backend/inventory_import.py` + `import_queue.py`)
Parser ERP con cabecera dinámica, UPSERT por código o nombre, 202 + polling.

### Categorías (`backend/categorias_producto.py`)
**Única fuente de verdad** (`CLAVES_CATEGORIA`, fallback `otros`). No inventar
nombres de categoría.

## Seguridad

- Login **solo por Google OAuth** (no se almacenan contraseñas).
- **CSRF global** (`CSRFProtect`); exenciones puntuales en rutas públicas.
- Rutas admin tras `@admin_requerido` / `@admin_requerido_api`.
- Secretos por entorno; `LOCALIS_SECRET_KEY` obligatoria en producción.
- Cabeceras `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Permissions-Policy` y HSTS (producción). CSP opt-in.
- Admins configurables con `LOCALIS_ADMIN_EMAILS`.

## Deuda técnica conocida

Ver `docs/auditoria-tecnica.md`. Resumen: **1 worker** obligatorio (cola de
importación en memoria → hace falta un broker compartido para escalar) y
pipeline de imágenes pesado acoplado al proceso web. **Resuelto en el Bloque 4**:
blueprints por dominio, migraciones versionadas (`schema_migrations`),
observabilidad con request-id y `logging` estructurado.
