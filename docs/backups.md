# Respaldos y recuperación (backups / restore)

> **Un respaldo que nunca restauraste no es un respaldo.** Haz el *drill* de
> restauración **una vez** antes de confiar en él.

## Qué protege
- Borrado/corrupción accidental de datos (tablas, comercios, productos).
- Un `UPDATE`/`DELETE` sin `WHERE`.
- Recuperación ante un fallo de la base gestionada.

## Estrategia recomendada (dos capas)

### Capa 1 — PITR gestionado por Supabase (la más robusta)
Supabase ofrece **Point‑In‑Time Recovery** (plan de pago) y backups diarios.
- Actívalo en el panel: *Database → Backups / PITR*.
- Da **RPO ≈ minutos** y recuperación a un instante concreto.
- No requiere infraestructura propia. Es la primera capa que deberías activar.

### Capa 2 — Respaldo propio con `pg_dump` (independiente del proveedor)
Script: `scripts/backup_bd.py`.

```bash
# Respalda en ./backups/ (retención 7 por defecto)
python scripts/backup_bd.py

# A otro directorio, conservando 14
python scripts/backup_bd.py --out D:\respaldos_localis --keep 14

# Ver qué haría, sin ejecutar
python scripts/backup_bd.py --dry-run

# Listar respaldos existentes
python scripts/backup_bd.py --list
```

- Usa la `DATABASE_URL` del entorno; la contraseña se pasa por `PGPASSWORD`
  (nunca en la línea de comandos).
- Formato `custom` comprimido (restaurable con `pg_restore`).
- Requiere `pg_dump` en el PATH (cliente de PostgreSQL).
- **Respalda solo el esquema `public`** (los datos de la app) por defecto.
  Un dump de TODA la base en Supabase incluye schemas gestionados (`auth`,
  `storage`, `realtime`, `vault`, `graphql`) que **no se pueden restaurar** sin
  superusuario: `pg_restore` daba 15 errores de `permission denied` y el drill
  resultaba en un **falso negativo**. Supabase protege esos schemas con sus
  propios backups/PITR. Usa `--schema=""` solo si sabes lo que haces.

**Programación (Windows):** Programador de tareas → diario 03:00 →
`python C:\ruta\scripts\backup_bd.py --out D:\respaldos_localis --keep 14`.
**Programación (Linux/macOS):** `cron` diario con la misma orden.

> Guarda los respaldos **fuera** del proveedor de la base (disco local, S3,
> Drive). Un respaldo en el mismo sitio que la base no protege de una caída del
> proveedor.

## Drill de restauración (obligatorio, en STAGING)

```bash
# 1) Restaura el respaldo más reciente sobre la base de STAGING
python scripts/restore_bd.py backups/localis_YYYYMMDD_HHMMSS.dump \
    --database-url "$STAGING_DATABASE_URL" --yes

# 2) Verifica que la app responde con datos
#    (arranca contra STAGING y comprueba /health/ready y una tienda)
```

`restore_bd.py` es destructivo (`pg_restore --clean`), por eso:
- exige `--yes`;
- **rechaza el host de producción** salvo `--force-prod`.

> **Drill verificado (producción → base dedicada)**: se respalda producción y se
> restaura en una base **dedicada** del proyecto de staging (`localis_drill`),
> sin tocar la base principal de staging. Resultado: **10/10 tablas con conteos
> idénticos** (usuarios, comercios, productos, interacciones…). Es el
> procedimiento recomendado: realista y no destruye nada en staging.

## Verificación tras restaurar
1. `GET /health/ready` → `ok: true`.
2. Abre una tienda conocida y un producto.
3. Cuenta filas clave (comercios, productos) vs. lo esperado.

## RPO / RTO objetivo
| Capa | RPO (pérdida máx.) | RTO (tiempo de recuperación) |
|---|---|---|
| PITR Supabase | minutos | minutos (panel) |
| `pg_dump` diario | hasta 24 h | ~minutos (restore manual) |

## Recuperación en producción (emergencia)
```bash
python scripts/restore_bd.py <archivo.dump> --yes --force-prod
# luego verificar /health/ready y la app
```
Hazlo con la app en mantenimiento si es posible, y avisa al equipo antes.
