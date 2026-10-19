# Auditoría técnica y hoja de ruta

Evaluación hecha sobre el commit `35d8fcf` (rama `main_limpia`), actualizada
tras el bloque **P0** aplicado en `mejora/noche-autonoma`.

## Puntuación por dimensión

| Dimensión | Nota | Comentario |
|---|:---:|---|
| Seguridad | 7.5 | OAuth-only, CSRF global, `@admin_requerido` en todas las rutas admin, cabeceras de seguridad, admins por entorno. Falta rate limiting y CSP activa. |
| Arquitectura | 6.0 | Buena separación por carpetas; `main.py` monolítico sin Blueprints. |
| Calidad de código | 5.5 | Muchos `except Exception` y `print`; deuda acumulada. |
| Base de datos | 6.0 | Adaptador sólido, `init_db` aditivo con lock de transacción; sin migraciones versionadas. |
| Tests / verificación | 6.5 | 31 scripts; sin runner; ahora con CI para las pruebas sin red. |
| Documentación | 7.5 | `AGENTS.md`, `README.md`, `docs/`, `.env.example` completo. |
| Rendimiento / escalabilidad | 5.0 | 1 worker, ML en el proceso web, sin caché. |
| DevOps / observabilidad | 6.0 | Procfile, gunicorn, health checks, CI, deps fijadas. Logs por `print`. |
| UI/UX | 7.0 | Server-rendered coherente; PWA/TWA. |
| Móvil (TWA) | 6.5 | Capacitor alineado; riesgo de deriva de package id. |

**Global ponderada: ≈ 6.4 / 10 (C+) — MVP sólido, con deuda operativa.**

## Lo ya aplicado (P0, rama `mejora/noche-autonoma`)

- **Admins por entorno**: `LOCALIS_ADMIN_EMAILS` (varios), sin correo hardcodeado.
- **Dependencias fijadas** en `requirements.txt` (build reproducible).
- **Cabeceras de seguridad** + HSTS en producción; CSP opt-in (`LOCALIS_CSP`).
- **`init_db` sin fuga de lock**: `pg_try_advisory_xact_lock` en vez de lock de
  sesión (que quedaba pegado en pgbouncer y saltaba migraciones).
- **CI** (`\.github/workflows/ci.yml`): compila todo y corre las pruebas sin red.
- **Documentación**: `README.md`, `docs/arquitectura.md`.

## Riesgos pendientes (por severidad)

1. **Escalado a 1 worker** por estado en memoria (cola de importación, índice
   maestro, backfill). Subir `WEB_CONCURRENCY` rompe importaciones.
2. **Sin migraciones versionadas** (`schema.sql` como referencia puede divergir).
3. **Observabilidad débil**: `print` en vez de `logging`; sin métricas/trazas.
4. **Sin rate limiting** en API/import (abuso posible con 1 worker).
5. **Pipeline de imágenes pesado** (rembg/onnx/opencv) acoplado al proceso web.

## Hoja de ruta

### P1 (estructural, con red de tests ya existente)
- Logging estructurado (migrar `print` a `logging` con niveles).
- Migraciones versionadas (tabla `schema_migrations`/Alembic) conviviendo con
  `init_db`.
- Rate limiting (Flask-Limiter) en `/api/*` e importación.
- Dividir `main.py` en Blueprints, ruta por ruta y con CI en verde.

### P2 (escala / calidad)
- Sacar el pipeline de imágenes a un worker; broker compartido (Redis/RQ) para
  permitir varios workers.
- Métricas/trazas (`/metrics`) y alertas.
- Tests E2E (Playwright) y accesibilidad.

## Frontera de riesgo

Los pasos P1 de refactor y todo P2 requieren **validación humana y/o
infraestructura** (Redis). Se aplican en ramas con CI en verde y prueba manual
antes de tocar `main_limpia`.
