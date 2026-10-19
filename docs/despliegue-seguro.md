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
- [ ] Ventana de **baja actividad** (madrugada).
- [ ] Saber el commit anterior desplegado (para rollback).

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
