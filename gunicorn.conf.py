"""Gunicorn: Render inyecta PORT; hay que escuchar en 0.0.0.0:$PORT."""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get('WEB_CONCURRENCY', '1'))
# Hilos por worker: cada petición pasa la mayor parte del tiempo esperando a la
# base de datos (~0,7 s de ida y vuelta), así que varios hilos por worker
# multiplican la capacidad sin necesitar más CPU. Un worker + N hilos comparte
# el mismo proceso (coherente con las colas y cachés en memoria del proyecto).
threads = int(os.environ.get('GUNICORN_THREADS', '8'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
graceful_timeout = 30
keepalive = 5
# La app ya emite su propia linea de acceso (localis.acceso) con MAS informacion
# (request-id, duracion). Con el accesslog de gunicorn activo se escriben DOS
# lineas por peticion, y ese I/O extra se paga en cada request. Por defecto solo
# la de la app; para depurar, define GUNICORN_ACCESSLOG=1.
_accesslog_on = str(os.environ.get('GUNICORN_ACCESSLOG', '0')).strip().lower() not in (
    '0', 'false', 'no', 'off',
)
accesslog = '-' if _accesslog_on else None
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOGLEVEL', 'info')
# No preload: el master enlaza el puerto antes de que cada worker importe main.
preload_app = False
