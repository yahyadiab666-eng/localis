-- Overrides manuales de imágenes oficiales.
-- Ejecutar en Supabase → SQL Editor (también lo crea init_db).

CREATE TABLE IF NOT EXISTS product_image_overrides (
    ean TEXT PRIMARY KEY,
    image_url TEXT NOT NULL,
    marca TEXT,
    verificado_por TEXT,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_product_image_overrides_ean
    ON product_image_overrides (ean);

-- Ejemplo (reemplazar por EAN real y URL de un dominio ya en whitelist
-- o por una URL pública del bucket imagenes):
-- INSERT INTO product_image_overrides (ean, image_url, marca, verificado_por)
-- VALUES (
--   '7591001000011',
--   'https://<proyecto>.supabase.co/storage/v1/object/public/imagenes/productos/7591001000011.webp',
--   'Harina PAN',
--   'equipo-localis'
-- )
-- ON CONFLICT (ean) DO UPDATE SET
--   image_url = EXCLUDED.image_url,
--   marca = EXCLUDED.marca,
--   verificado_por = EXCLUDED.verificado_por,
--   fecha = CURRENT_TIMESTAMP;
