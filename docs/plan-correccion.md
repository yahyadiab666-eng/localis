# Plan de corrección hacia B+ (≥ 8.0)

Objetivo: llevar Localis de **6.4 (C+)** a **≥ 8.0 (B+)** sin corromper el
sistema, trabajando por bloques y **verificando cada bloque al 100% antes de
pasar al siguiente**.

## Reglas de ejecución

1. Cada bloque va en su **propia rama/commit**, con verificación documentada.
2. Nada se marca "hecho" sin pasar sus **criterios de verificación**.
3. Solo **cambios aditivos** hasta que existan staging/rollback (Bloques 3 y 4).
4. `main_limpia` no se toca hasta que un bloque esté verificado y aprobado.

## Bloques

| # | Bloque | Objetivo | Verificación (100%) | Riesgo | Estado |
|:-:|---|---|---|:--:|:--:|
| 0 | Red de seguridad (CI + runner local) | Poder cambiar sin miedo | CI verde reproducible + runner local corre todas las pruebas | 🟢 | ✅ hecho |
| 1 | Observabilidad y logging | Ver y diagnosticar en prod | App arranca, `/health` 200, logs con request-id, pruebas verdes | 🟢 | ✅ hecho |
| 6a | Calidad: ruff + pre-commit | Bajar bugs, estilo consistente | `ruff check` exit 0 y en CI | 🟠 | ✅ hecho |
| 5a | Seguridad aditiva | Endurecer sin romper | Cabeceras (ya), CSP report-only, pruebas verdes | 🟡 | ⏳ |
| 2 | Migraciones versionadas | Esquema auditable y reversible | `schema_migrations` + init_db idempotente contra BD real | 🟠 | ✅ hecho |
| 1b | Migrar `print` → `logging` (núcleo) | Trazas estructuradas | Módulos clave sin `print`, pruebas verdes | 🟠 | ⏳ |
| 7 | Rendimiento y caché | Latencia y coste | p95 catálogo con caché; invalidación correcta | 🟡 | ⏳ |
| 8 | E2E + accesibilidad | Contrato de los refactors | Flujos críticos verdes | 🟢 | ⏳ |
| — | **Frontera de riesgo (requiere staging/infra)** | | | | |
| 4 | Blueprints (`main.py`) | Mantener y crecer | E2E verdes, mismo comportamiento | 🔴 | ⛔ gated |
| 3 | Worker imágenes + Redis | Escalar a N workers | `WEB_CONCURRENCY=4` estable + carga | 🔴 | ⛔ gated |
| 9 | Docker/staging/rollback/backups | Operar con garantías | Rollback < 5 min, restore probado | 🟠 | ⛔ gated |

## Criterios de "100% verificado" (definition of done)

Un bloque se considera verificado cuando:
- `python -m compileall` **sin errores**.
- Pruebas **sin red** verdes (`scripts/test_config_seguridad.py`,
  `test_clasificador_categorias.py`, `test_cobertura_universal.py`).
- Pruebas de **integración** verdes contra la BD (las que apliquen).
- La app **arranca** y `/health/live` responde **200**.
- El criterio específico del bloque (columna de la tabla) se cumple.

## Estado actual (acumulado)

- P0 aplicado: admins por env, deps fijadas, cabeceras, lock `init_db`.
- CI base + documentación (README, arquitectura, auditoría).
- Rama de trabajo: `mejora/noche-autonoma`.
