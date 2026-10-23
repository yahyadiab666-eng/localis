"""Asistente de IA de Localis (atencion al cliente, SOLO informativo).

Modulo autocontenido y apagable por variable de entorno (``LOCALIS_ASISTENTE``).
Por ahora NO toca la base de datos: responde con la documentacion indexada en
memoria y deriva a WhatsApp cuando hace falta una intervencion humana. El
contexto de la conversacion lo aporta el navegador y no se persiste.

Piezas:
  - ``proveedor``: cliente del LLM (compatible OpenAI) sobre httpx.
  - ``conocimiento``: recuperacion lexica (BM25) sobre la documentacion.
  - ``prompts``: system prompt por rol y armado de mensajes.
  - ``seguridad``: saneado de entrada y redaccion de secretos.
  - ``semaforo``: tope de consultas simultaneas al LLM.
  - ``orquestador``: une todo y devuelve la respuesta final.

Futuro (fuera de este alcance): herramientas para consultar/editar con permiso,
verificar pagos y actuar sobre comercios, siempre de forma segura.
"""
