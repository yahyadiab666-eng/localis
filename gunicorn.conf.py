"""Gunicorn: Render inyecta PORT; hay que escuchar en 0.0.0.0:$PORT."""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get('WEB_CONCURRENCY', '1'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
graceful_timeout = 30
keepalive = 5
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOGLEVEL', 'info')
# No preload: el master enlaza el puerto antes de que cada worker importe main.
preload_app = False
