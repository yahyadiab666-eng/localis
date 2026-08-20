from database import DATABASE_KEY, DATABASE_URL, init_db


def crear_base_de_datos():
    if not DATABASE_URL:
        print('Error: DATABASE_URL no está configurada en el entorno.')
        return
    if not DATABASE_KEY:
        print('Error: DATABASE_KEY no está configurada en el entorno.')
        return
    print('Inicializando base de datos PostgreSQL...')

    if init_db():
        print('Base de datos PostgreSQL inicializada/actualizada correctamente.')
    else:
        print('No se pudo completar la inicialización de la base de datos.')


if __name__ == '__main__':
    crear_base_de_datos()
