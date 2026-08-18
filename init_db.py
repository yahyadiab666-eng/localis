import os

from config import DATABASE_FILE
from database import init_db


def crear_base_de_datos():
    print(f'Inicializando base de datos en: {DATABASE_FILE}')

    carpeta_db = os.path.dirname(DATABASE_FILE)
    os.makedirs(carpeta_db, exist_ok=True)

    if os.path.exists(DATABASE_FILE):
        print('Base de datos existente detectada: se aplicarán migraciones incrementales.')

    if init_db():
        print('Base de datos localis.db inicializada/actualizada correctamente.')
    else:
        print('No se pudo completar la inicialización de la base de datos.')


if __name__ == '__main__':
    crear_base_de_datos()
