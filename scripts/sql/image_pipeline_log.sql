-- Auditoría por producto del pipeline de imágenes.
-- Ejecutar en Supabase → SQL Editor (también lo crea init_db).

CREATE TABLE IF NOT EXISTS image_pipeline_log (
    id SERIAL PRIMARY KEY,
    ean TEXT,
    producto_id INTEGER,
    resultado TEXT NOT NULL,
    fuente TEXT,
    motivo_descarte TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_image_pipeline_log_ean
    ON image_pipeline_log (ean);
CREATE INDEX IF NOT EXISTS idx_image_pipeline_log_timestamp
    ON image_pipeline_log (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_image_pipeline_log_resultado
    ON image_pipeline_log (resultado);

-- Resumen de la última corrida (ajustar el intervalo si hace falta):
-- SELECT resultado, fuente, COUNT(*) AS total
-- FROM image_pipeline_log
-- WHERE timestamp >= NOW() - INTERVAL '1 day'
-- GROUP BY resultado, fuente
-- ORDER BY total DESC;
