-- Catálogo maestro de imágenes (cache global por código de barras).
-- Ejecutar en Supabase → SQL Editor.

CREATE TABLE IF NOT EXISTS catalogo_maestro_imagenes (
    codigo_barras TEXT PRIMARY KEY,
    url_imagen TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Si la tabla ya existía sin PRIMARY KEY, añadirla (ajusta si hay duplicados).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'catalogo_maestro_imagenes_pkey'
    ) THEN
        ALTER TABLE catalogo_maestro_imagenes
            ADD CONSTRAINT catalogo_maestro_imagenes_pkey PRIMARY KEY (codigo_barras);
    END IF;
EXCEPTION
    WHEN duplicate_table THEN NULL;
    WHEN invalid_table_definition THEN
        RAISE NOTICE 'Revisa duplicados en codigo_barras antes de añadir PRIMARY KEY.';
END $$;

-- En Supabase el PK suele ser id (uuid). El upsert de Localis usa codigo_barras.
CREATE UNIQUE INDEX IF NOT EXISTS idx_catalogo_maestro_codigo
    ON catalogo_maestro_imagenes (codigo_barras);

-- Quitar semillas de prueba (Open Food Facts / wsrv / Pexels).
DELETE FROM catalogo_maestro_imagenes
WHERE LOWER(CAST(url_imagen AS TEXT)) LIKE '%openfoodfacts%'
   OR LOWER(CAST(url_imagen AS TEXT)) LIKE '%wsrv.nl%'
   OR LOWER(CAST(url_imagen AS TEXT)) LIKE '%pexels.com%';
