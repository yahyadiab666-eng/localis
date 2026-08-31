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

-- Semilla de prueba: Harina PAN y Aceite Vatel (códigos de demo + EAN reales).
INSERT INTO catalogo_maestro_imagenes (codigo_barras, url_imagen)
VALUES
    (
        '7591001000011',
        'https://wsrv.nl/?url=https%3A%2F%2Fimages.openfoodfacts.org%2Fimages%2Fproducts%2F759%2F100%2F200%2F0547%2Ffront_es.24.400.jpg&w=300&h=300&fit=cover&output=webp&q=80'
    ),
    (
        '7591002000011',
        'https://wsrv.nl/?url=https%3A%2F%2Fimages.openfoodfacts.org%2Fimages%2Fproducts%2F759%2F100%2F200%2F0547%2Ffront_es.24.400.jpg&w=300&h=300&fit=cover&output=webp&q=80'
    ),
    (
        '7591002000547',
        'https://wsrv.nl/?url=https%3A%2F%2Fimages.openfoodfacts.org%2Fimages%2Fproducts%2F759%2F100%2F200%2F0547%2Ffront_es.24.400.jpg&w=300&h=300&fit=cover&output=webp&q=80'
    ),
    (
        '7591001000035',
        'https://wsrv.nl/?url=https%3A%2F%2Fimages.openfoodfacts.org%2Fimages%2Fproducts%2F759%2F104%2F900%2F1903%2Ffront_es.3.400.jpg&w=300&h=300&fit=cover&output=webp&q=80'
    ),
    (
        '7591049001903',
        'https://wsrv.nl/?url=https%3A%2F%2Fimages.openfoodfacts.org%2Fimages%2Fproducts%2F759%2F104%2F900%2F1903%2Ffront_es.3.400.jpg&w=300&h=300&fit=cover&output=webp&q=80'
    )
ON CONFLICT (codigo_barras)
DO UPDATE SET url_imagen = EXCLUDED.url_imagen;
