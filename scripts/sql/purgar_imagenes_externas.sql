-- One-shot P0: purga URLs externas que envenenan el catálogo.
-- Conserva solo Supabase Storage y /static/uploads/.
-- Ejecutar una vez en PostgreSQL (Supabase SQL Editor o psql vía DATABASE_URL).

-- Diagnóstico previo (opcional):
-- SELECT COUNT(*) FROM productos
-- WHERE imagen_url IS NOT NULL
--   AND CAST(imagen_url AS TEXT) NOT LIKE '%/storage/v1/object/public/%'
--   AND CAST(imagen_url AS TEXT) NOT LIKE '/static/uploads/%';

BEGIN;

UPDATE productos
SET imagen_url = NULL
WHERE imagen_url IS NOT NULL
  AND (
    CAST(imagen_url AS TEXT) NOT LIKE '%/storage/v1/object/public/%'
    AND CAST(imagen_url AS TEXT) NOT LIKE '/static/uploads/%'
  );

-- Maestro: borrar filas cuyo host no es Storage ni upload local.
DELETE FROM catalogo_maestro_imagenes
WHERE url_imagen IS NOT NULL
  AND (
    CAST(url_imagen AS TEXT) NOT LIKE '%/storage/v1/object/public/%'
    AND CAST(url_imagen AS TEXT) NOT LIKE '/static/uploads/%'
  );

COMMIT;
