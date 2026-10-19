# Despliegue seguro y rollback

Guía para llevar los bloques verificados de `mejora/noche-autonoma` a
producción **sin corromper el sistema**, y para revertir rápido si algo falla.

## Antes de desplegar (checklist)

- [ ] **Backup / PITR de PostgreSQL activo** en Supabase. Probar una
      restauración de prueba. *(Sin esto, NO se aplica ningún cambio de esquema.)*
- [ ] CI en verde en la rama (jobs `sin-red` e `integracion`).
- [ ] Variables nuevas definidas en el panel de Render:
      `LOCALIS_ADMIN_EMAILS` (correos admin) y, opcional, `LOCALIS_LOG_JSON=1`,
      `LOCALIS_LOG_LEVEL=INFO`.
- [ ] **Versión de Python:** el repo fija `3.12.8` en `.python-version`.
      Si en Render hay una variable `PYTHON_VERSION` definida, debe ser `3.12.8`
      (tiene **más prioridad** que el archivo). Con Python 3.14 el build falla
      porque `numpy==2.3.1` no tiene wheel para esa versión.
- [ ] Ventana de **baja actividad** (madrugada).
- [ ] Saber el commit anterior desplegado (para rollback).

## Diagnóstico de un despliegue fallido

- **Falla el BUILD:** revisar el log de pip. Causa típica: versión de Python
  distinta de 3.12 → wheels faltantes (numpy/onnxruntime). Solución: fijar
  `PYTHON_VERSION=3.12.8` o eliminar esa variable y dejar `.python-version`.
- **Falla el HEALTH CHECK:** Render debe apuntar a `/health/live` (no a
  `/health`, que devuelve 503 si la BD aún calienta).
- **Saber qué quedó desplegado:** `GET /health/live` devuelve `commit` y
  `branch` (variables de Render). Si están vacíos, el servicio no está
  desplegando desde este repo/rama.

## Despliegue recomendado (incremental)

1. **Fusionar o desplegar solo el primer tramo** (P0 + Bloques 0/1/6a). Todos
   son **aditivos**: admins por env, deps fijadas, cabeceras, lock de `init_db`,
   logging/request-id, lint. Bajo riesgo.
2. Observar 24–48 h: revisar logs (`[Localis]`, líneas `localis.acceso`),
   `/health`, errores 5xx.
3. Si todo bien, continuar con los siguientes bloques seguros (5a, 2, 1b, 7, 8).

## Rollback

- **Código:** Render → "Rollback" al despliegue anterior, o `git revert` del
  merge y push. Tiempo objetivo: **< 5 min**.
- **Esquema:** `init_db` es **aditivo** (no borra datos); un rollback de código
  no rompe columnas nuevas. Si una migración versionada futura falla, revertir
  la migración o restaurar desde el backup/PITR.
- **Variables:** revertir `LOCALIS_ADMIN_EMAILS` restaura el admin histórico.

## Qué NO se despliega sin staging

- Bloque **4** (Blueprints) y Bloque **3** (Redis/worker): requieren entorno de
  pruebas y/o infraestructura. **No** van a producción directamente.

## Cómo revertir el propio despliegue en Render

Dashboard Render → servicio → *Deploys* → elegir deploy anterior → *Redeploy*.
Es una operación de un clic y no requiere recompilar en local.

## Disponibilidad: evitar el spin-down (plan gratuito)

Render duerme la instancia tras ~15 min sin tráfico; el primer usuario espera
30–60 s. Para evitarlo:

- **Incluido en el repo:** `.github/workflows/keepalive.yml` hace ping a
  `/health/live` cada 10 minutos (GitHub Actions). Limitación: los cron de
  GitHub pueden retrasarse y se desactivan tras 60 días sin actividad del repo.
- **Recomendado:** monitor externo (UptimeRobot / cron-job.org) cada 5–10 min
  a `https://localis.onrender.com/health/live`. Más fiable que el cron de GH.
- **Definitivo:** plan pago *always-on* de Render.

## E2E (contrato de los refactors)

`python scripts/e2e_smoke.py --base-url https://localis.onrender.com` valida las
rutas críticas por HTTP (salud, portada, login, tasa, admin sin sesión, endpoint
de tasa endurecido). Debe pasar **antes y después** del refactor de Blueprints.
