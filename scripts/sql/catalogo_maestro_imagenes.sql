-- Catálogo maestro de imágenes (cache global por código de barras).
-- Ejecutar en el SQL Editor de Supabase si la tabla no existe aún.

CREATE TABLE IF NOT EXISTS catalogo_maestro_imagenes (
    codigo_barras TEXT PRIMARY KEY,
    url_imagen TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Opcional: permitir lectura pública y escritura solo con service_role (ajusta según tu política).
-- ALTER TABLE catalogo_maestro_imagenes ENABLE ROW LEVEL SECURITY;
