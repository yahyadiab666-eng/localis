"""Reporte de tráfico y PWA → correo de los administradores.

Agrega los contadores persistidos (tabla ``metricas_diarias``) y envía el
resumen a todos los ``LOCALIS_ADMIN_EMAILS``. Idempotente por día.

Uso:
    python scripts/reporte_trafico_diario.py                 # envía (idempotente)
    python scripts/reporte_trafico_diario.py --dias 7        # período de 7 días
    python scripts/reporte_trafico_diario.py --sin-correo    # imprime, NO envía
    python scripts/reporte_trafico_diario.py --forzar        # reenvía aunque ya se envió hoy

Notas:
    - Requiere ``DATABASE_URL``. Para enviar, también SMTP_USER/SMTP_PASSWORD.
    - En producción lo dispara un cron externo (GitHub Actions) contra el
      endpoint ``/tareas/reporte-trafico``; este script sirve para uso manual,
      local o como cron de respaldo.

Devuelve 0 si el reporte se generó (y envió, salvo ``--sin-correo``); 1 si falló.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass


def _parsear(argv):
    dias = 1
    sin_correo = False
    forzar = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == '--dias' and i + 1 < len(argv):
            try:
                dias = max(1, int(argv[i + 1]))
            except (TypeError, ValueError):
                dias = 1
            i += 2
            continue
        if arg.startswith('--dias='):
            try:
                dias = max(1, int(arg.split('=', 1)[1]))
            except (TypeError, ValueError):
                dias = 1
        elif arg == '--sin-correo':
            sin_correo = True
        elif arg == '--forzar':
            forzar = True
        i += 1
    return dias, sin_correo, forzar


def main(argv=None) -> int:
    dias, sin_correo, forzar = _parsear(list(argv or sys.argv[1:]))

    from backend.reporte_trafico import construir_reporte, enviar_reporte_admin

    if sin_correo:
        print(construir_reporte(dias=dias)['texto'])
        return 0

    ok, detalle, texto = enviar_reporte_admin(dias=dias, forzar=forzar)
    print(f'[reporte_trafico] ok={ok} detalle={detalle}')
    if not ok:
        # No se envió: imprimir el reporte para que quede en los logs.
        print('\n' + texto)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
