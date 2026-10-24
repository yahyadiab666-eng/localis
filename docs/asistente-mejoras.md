# Mejoras del asistente de IA

Auditoría y plan de evolución del asistente (`backend/asistente/`). Sigue la
convención de `docs/pendientes.md`: aquí queda la especificación, no solo ideas.

## Cómo está estructurado hoy

Paquete autocontenido y apagable por `LOCALIS_ASISTENTE`:

| Módulo | Rol |
|---|---|
| `proveedor.py` | Cliente LLM compatible OpenAI sobre `httpx` (OpenRouter por defecto). Modelo + respaldo. Acepta `herramientas` (function calling), hoy sin usar. |
| `conocimiento.py` | RAG **léxico** (BM25 en Python puro) sobre documentación curada. Índice en memoria del worker. |
| `prompts.py` | System prompt base + variante por rol (cliente/invitado/comerciante/admin). |
| `seguridad.py` | `sanear_entrada` + `redactar` (tachado de secretos). Segunda capa de defensa. |
| `semaforo.py` | Tope de consultas simultáneas al LLM. |
| `presupuesto.py` | Tope diario de tokens en memoria (desactivado por defecto). |
| `orquestador.py` | Une RAG + datos en vivo + proveedor. Detección determinística de acciones. |
| `datos_vivos.py` | Datos **solo lectura**: precios, tasa BCV, plan/límites del comercio, última importación, imágenes, tiendas, categorías. |
| `acciones.py` | Whitelist cerrada de acciones reversibles + confirmación en 2 pasos (nonce de un solo uso) + auditoría. |
| `pagos.py` | Revalidación en Binance (solo lectura) + reintento automático en segundo plano. |
| `venta.py` | Tono comercial (modo venta) para planes/beneficios. |
| `observacion.py` | Contadores en memoria (latencia, 👍/👎). |

Capa HTTP `backend/blueprints/asistente.py`: `/estado`, `/csrf`, `/chat`,
`/feedback`, `/escalar`, `/accion/confirmar`, `/accion/cancelar`.
Front: `templates/partials/asistente_widget.html`, `static/js/asistente.js`,
`static/css/asistente.css`. El historial vive en el navegador (no se persiste).

## Fortalezas (no romper)

- Diseño defensivo y apagable en todo el camino (`nunca lanza`, fallbacks).
- El modelo **no decide** acciones: whitelist + nonce + revalidación + auditoría.
- Datos del comercio verificados por propiedad de sesión.
- Coste/latencia acotados (tokens, semáforo, timeout, temperatura baja).

## Problemas detectados

**Correctitud / negocio**

1. Cifras de planes, destacados y puntos **duplicadas en el código** (podían
   quedar viejas respecto a su fuente real).
2. El markdown del modelo se mostraba **crudo** (el usuario veía `**asteriscos**`).

**Capacidad de respuesta**

3. RAG léxico y pequeño (sin sinónimos, embeddings ni rerank).
4. **Sin streaming** (espera completa antes de mostrar nada).
5. **Sin memoria ni aprendizaje**: no se guarda ninguna conversación; el feedback
   son solo contadores.

**Operación / escala**

6. Observabilidad y presupuesto volátiles y por proceso (×N con varias instancias).
7. Rate limit solo por IP; presupuesto de tokens apagado por defecto.
8. Cliente `httpx` nuevo por llamada (handshake TLS cada vez), sin reintentos.

**Calidad / producto**

9. Sin set dorado de evaluación ni regresión de prompts.
10. `herramientas` (function calling) sin usar.
11. Escalado a humano solo crea ticket si hay correo; sin auto-escalado.
12. Sin resumen de conversaciones largas.

## Plan por fases

### Fase 0 — Quick wins (hecha)

| # | Mejora | Estado |
|---|---|---|
| 0.1 | Render de markdown seguro en el widget (`escaparHtml` + subconjunto seguro) | Hecho |
| 0.2 | Cifras de planes/destacados/puntos derivadas de su fuente real (`datos_vivos.hechos_clave()`, `backend/plans.py`, `backend/boosts.py`, `backend/referidos.py`); eliminadas las cifras duplicadas de `venta.py` | Hecho |
| 0.3 | Cliente `httpx` reutilizable (por hilo) + un reintento con backoff en `proveedor.py` | Hecho |
| 0.4 | RAG ampliado (`templates/privacidad.html`, `templates/eliminar_cuenta.html`) y limpieza de sintaxis Jinja en el texto recuperable | Hecho |

Verificación: `ruff check .` + `scripts/test_asistente_{datos,conocimiento,rutas,guardarrailes}.py`.

### Fase 1 — UX y datos (en curso)

- **Flujo de presentación (SSE)** en `/api/asistente/chat/flujo` + render
  incremental en el widget. Hecho de forma **segura**: la respuesta se genera y
  se **redacta completa** antes de emitirla, así no se debilita el tachado de
  secretos; el flujo solo aporta el efecto de escritura.
  - *Pendiente*: streaming **token a token** real (reduce el tiempo hasta el
    primer token). Requiere una estrategia de redacción incremental con retención
    por disparadores para no partir un secreto entre fragmentos (decisión abierta).
- **Reactivación de lo pagado** al revalidar un pago (ver sección propia). Hecho.
- **Persistencia de conversaciones + feedback** (tablas nuevas) con retención y
  reglas de PII; `feedback` ligado a la conversación.
- **Métricas persistentes** y panel en `admin` (reusar `backend/metricas.py`).
- **Resumen incremental** de conversaciones largas.

### Reactivación de lo pagado (asistente)

Cuando el asistente **revalida un pago en Binance** (acción confirmada por el
comerciante, nonce de un solo uso) y Binance **confirma** la orden:

- Si el pago **no estaba aplicado** (p. ej. fue rechazado por un error del OCR),
  **reactiva el plan o el destacado** que se estaba pagando, reutilizando la
  lógica ya probada del panel admin (`confirmar_pago_suscripcion` /
  `destacar_producto_por_dias`).
- Si **ya estaba aplicado**, solo cierra la revisión: nunca duplica.
- La idempotencia se decide con ``solicitudes_pago`` (¿existe una solicitud
  aprobada para esa referencia?) y, ante error de BD, se asume aplicado
  (conservador: preferimos no reactivar a arriesgar una doble activación).
- Todo queda en ``logs_auditoria`` con el estado (reactivado / ya aplicado /
  fallo de reactivación).

Archivos: `backend/asistente/pagos.py` (`_revision_reverificable`,
`_ya_aplicado`, `_activar_concepto`, `_reverificar_revision`).

### Fase 2 — Recuperación y calidad (pendiente)

- **RAG híbrido**: BM25 + embeddings (pgvector/Supabase) con rerank y metadata
  por fragmento para citar la fuente.
- **Function calling de solo lectura** (`buscar_productos`, `buscar_tiendas`,
  `mi_plan`, `estado_pago`) manteniendo las **escrituras determinísticas**.
- **Set dorado de evaluación** (`scripts/evaluar_asistente.py`) con LLM-as-judge,
  integrable al runner del CI.
- **Auto-escalado** por baja confianza o 👎, con el contexto en el ticket.
- **Caché semántico** de FAQ para bajar coste y latencia.

### Fase 3 — Escala y ciclo cerrado (continuo)

- Presupuesto y semáforo **compartidos** (Redis o `pg_advisory`) si se pasa a más
  de una instancia.
- Ciclo cerrado: tickets resueltos → candidatos a FAQ con moderación.
- Ampliar el catálogo de acciones reversibles.
- Extender el widget a todas las plantillas y sugerencias contextuales.

## Decisiones abiertas

1. ¿Se puede tocar el esquema de BD (persistencia de conversaciones/métricas)?
2. ¿Hay `pgvector` disponible o se seguirá sin embeddings por ahora?
3. Presupuesto de coste mensual para el LLM (define modelo y caché semántico).
4. ¿Se acepta streaming (requiere ajustar `proveedor` y el blueprint)?
