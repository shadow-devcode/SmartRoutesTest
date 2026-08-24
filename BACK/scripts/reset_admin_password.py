"""
Restablece la contraseña del usuario admin a: Admin123!
Útil si el login no funciona (hash incorrecto o no recuerdas la contraseña).
Ejecutar desde BACK: python -m scripts.reset_admin_password
"""
import sys
from pathlib import Path

BACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACK_ROOT))

import bcrypt
from config.settings import settings
from database import connection as db_connection
from database.connection import init_db
from models import Base, User
from sqlalchemy import select

def main():
    engine = init_db(settings.DATABASE_URI, echo=False)
    Base.metadata.create_all(bind=engine)

    # init_db rellena db_connection.SessionLocal; el nombre importado al inicio
    # del módulo seguiría siendo None (mismo bug que en seed_admin.py).
    session = db_connection.SessionLocal()
    try:
        admin = session.execute(select(User).where(User.email == "admin@smartroutes.com")).scalar_one_or_none()
        if not admin:
            print("No existe el usuario admin@smartroutes.com en la base de datos.")
            print("Ejecuta primero: mysql -u root -p < BACK/scripts/init_db.sql")
            return
        new_hash = bcrypt.hashpw(b"Admin123!", bcrypt.gensalt(rounds=12)).decode("utf-8")
        admin.password_hash = new_hash
        session.commit()
        print("Contraseña del admin restablecida a: Admin123!")
        print("Prueba de nuevo en http://localhost:4200/login con:")
        print("  Email: admin@smartroutes.com")
        print("  Contraseña: Admin123!")
    except Exception as e:
        print("Error:", e)
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    main()
