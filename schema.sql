PRAGMA foreign_keys = ON;

-- ==========================================
-- 1. TABLA DE USUARIOS
-- ==========================================
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    correo TEXT UNIQUE NOT NULL,
    contrasena TEXT, -- Opcional para autenticación vía OAuth con Google
    foto_url TEXT,   -- Avatar provisto por Google
    rol TEXT DEFAULT 'comerciante' -- Opciones: 'cliente', 'comerciante', 'admin'
);

-- ==========================================
-- 2. TABLA DE PLANES
-- ==========================================
CREATE TABLE IF NOT EXISTS planes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    precio REAL NOT NULL DEFAULT 0,
    limite_productos INTEGER,
    soporte_prioritario INTEGER DEFAULT 0,
    dias_duracion INTEGER DEFAULT 30,
    destacado INTEGER DEFAULT 0,
    activo INTEGER DEFAULT 1
);

INSERT OR IGNORE INTO planes (codigo, nombre, precio, limite_productos, soporte_prioritario, dias_duracion, destacado, activo) VALUES
    ('gratis', 'Plan Gratis / Prueba', 0, 50, 0, 30, 0, 1),
    ('basica', 'Plan Básico', 10, 100, 0, 30, 0, 1),
    ('pro', 'Pro', 15, 300, 1, 30, 1, 1),
    ('business', 'Business', 35, NULL, 1, 30, 0, 1);

-- ==========================================
-- 3. TABLA DE CATEGORÍAS
-- ==========================================
CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT UNIQUE NOT NULL
);

-- Categorías por defecto del sistema
INSERT OR IGNORE INTO categorias (nombre) VALUES ('Alimentos'), ('Ropa'), ('Tecnología'), ('Otros');

-- ==========================================
-- 4. TABLA DE COMERCIOS
-- ==========================================
CREATE TABLE IF NOT EXISTS comercios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER,
    nombre TEXT NOT NULL,
    descripcion TEXT,
    telefono TEXT,
    documento_identidad TEXT,
    logo_url TEXT,
    banner_url TEXT,
    direccion TEXT,
    ciudad TEXT,
    zona TEXT,
    maps_url TEXT,
    ubicacion_maps_url TEXT,
    categoria_id INTEGER,

    -- Suscripción y planes
    plan_id INTEGER,
    plan_tipo TEXT DEFAULT 'gratis', -- gratis, basica, pro, business
    fecha_inicio_suscripcion TIMESTAMP,
    fecha_vencimiento TIMESTAMP,
    limite_productos INTEGER DEFAULT 50,
    estado_pago TEXT DEFAULT 'activo', -- activo, gratis, suspendido, vencido

    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    aviso_bienvenida_visto INTEGER DEFAULT 0,
    imagen_portada TEXT,
    visible INTEGER DEFAULT 1,         -- 0 = Oculto, 1 = Visible

    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
    FOREIGN KEY (categoria_id) REFERENCES categorias(id) ON DELETE SET NULL,
    FOREIGN KEY (plan_id) REFERENCES planes(id) ON DELETE SET NULL
);

-- ==========================================
-- 4. TABLA DE SUCURSALES
-- ==========================================
CREATE TABLE IF NOT EXISTS sucursales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comercio_id INTEGER,
    direccion TEXT NOT NULL,
    coordinates_maps TEXT, 
    FOREIGN KEY (comercio_id) REFERENCES comercios(id) ON DELETE CASCADE
);

-- ==========================================
-- 5. TABLA DE PRODUCTOS
-- ==========================================
CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comercio_id INTEGER,
    nombre TEXT NOT NULL,
    precio_usd REAL NOT NULL, 
    descripcion TEXT,
    imagen_url TEXT, -- URL pública externa, ruta /static/ o asset estático
    stock INTEGER DEFAULT 0,
    codigo_barras TEXT,
    FOREIGN KEY (comercio_id) REFERENCES comercios(id) ON DELETE CASCADE
);

-- ==========================================
-- 6. TABLA DE SOPORTE Y REPORTES
-- ==========================================
CREATE TABLE IF NOT EXISTS soporte_y_reportes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER, 
    tipo TEXT NOT NULL, -- 'soporte', 'reportar_tienda', 'reportar_articulo'
    correo TEXT NOT NULL,
    mensaje TEXT NOT NULL,
    referencia_id INTEGER, 
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    estado TEXT DEFAULT 'pendiente', -- 'pendiente', 'resuelto'
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
);

-- ==========================================
-- 7. TABLA DE CONFIGURACIÓN DEL SISTEMA
-- ==========================================
CREATE TABLE IF NOT EXISTS configuracion_sistema (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

-- ==========================================
-- 8. TABLA DE LOGS DE AUDITORÍA ADMINISTRATIVA
-- ==========================================
CREATE TABLE IF NOT EXISTS logs_auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER, 
    accion TEXT NOT NULL, 
    detalles TEXT, 
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE SET NULL
);

-- ==========================================
-- 9. TABLA DE INTENTOS DE LOGIN
-- ==========================================
CREATE TABLE IF NOT EXISTS intentos_login (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    correo_intentado TEXT NOT NULL,
    ip_direccion TEXT DEFAULT '127.0.0.1',
    intentos INTEGER DEFAULT 1,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (correo_intentado) REFERENCES usuarios(correo) ON DELETE CASCADE
);

-- ==========================================
-- INSERCIONES POR DEFECTO E ÍNDICES
-- ==========================================
INSERT OR IGNORE INTO configuracion_sistema (clave, valor) VALUES ('tasa_dolar', '36.50');
INSERT OR IGNORE INTO configuracion_sistema (clave, valor) VALUES ('banner_principal', '');
INSERT OR IGNORE INTO configuracion_sistema (clave, valor) VALUES ('whatsapp_soporte', '584125970507');

CREATE INDEX IF NOT EXISTS idx_intentos_correo ON intentos_login(correo_intentado);
CREATE INDEX IF NOT EXISTS idx_productos_nombre ON productos(nombre);
CREATE INDEX IF NOT EXISTS idx_productos_comercio_id ON productos(comercio_id);
CREATE INDEX IF NOT EXISTS idx_productos_codigo_barras ON productos(codigo_barras);
CREATE INDEX IF NOT EXISTS idx_comercios_nombre ON comercios(nombre);
CREATE INDEX IF NOT EXISTS idx_comercios_ciudad ON comercios(ciudad);
CREATE INDEX IF NOT EXISTS idx_comercios_categoria ON comercios(categoria_id);
CREATE INDEX IF NOT EXISTS idx_comercios_visible ON comercios(visible);
CREATE INDEX IF NOT EXISTS idx_comercios_estado_plan ON comercios(estado_pago, plan_tipo);
CREATE INDEX IF NOT EXISTS idx_comercios_plan_id ON comercios(plan_id);

-- ==========================================
-- 10. TABLA DE PAGOS (C2P / Manual / Admin)
-- ==========================================
CREATE TABLE IF NOT EXISTS pagos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tienda_id INTEGER NOT NULL,
    plan_id INTEGER NOT NULL,
    monto REAL NOT NULL,
    metodo TEXT NOT NULL,
    referencia TEXT,
    banco_origen TEXT,
    cedula_pagador TEXT,
    telefono_pagador TEXT,
    estado TEXT DEFAULT 'pendiente',
    fecha_pago DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tienda_id) REFERENCES comercios(id),
    FOREIGN KEY (plan_id) REFERENCES planes(id)
);

-- ==========================================
-- 11. TABLA DE SOLICITUDES DE PAGO
-- ==========================================
CREATE TABLE IF NOT EXISTS solicitudes_pago (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comercio_id INTEGER NOT NULL,
    plan_tipo TEXT NOT NULL,
    referencia TEXT NOT NULL,
    fecha_transferencia TEXT NOT NULL,
    estado TEXT DEFAULT 'pendiente',
    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (comercio_id) REFERENCES comercios(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_productos_tienda ON productos(comercio_id);
CREATE INDEX IF NOT EXISTS idx_tiendas_estado ON comercios(estado_pago, plan_id);
CREATE INDEX IF NOT EXISTS idx_pagos_tienda ON pagos(tienda_id);
CREATE INDEX IF NOT EXISTS idx_solicitudes_pago_comercio ON solicitudes_pago(comercio_id);