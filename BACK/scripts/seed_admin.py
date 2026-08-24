"""
Crea el primer usuario administrador si no existe ninguno.
Uso (desde la raíz BACK): python -m scripts.seed_admin
Contraseña por defecto: Admin123!
"""
import sys
from pathlib import Path

# Añadir raíz del backend al path
BACK_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACK_ROOT))

from config.settings import settings
from database import connection as db_connection
from database.connection import init_db
from models import Base, User, Role
from sqlalchemy import select, func

import bcrypt


def main():
    engine = init_db(settings.DATABASE_URI, echo=False)
    Base.metadata.create_all(bind=engine)

    # init_db rellena db_connection.SessionLocal; el nombre importado al inicio
    # del módulo seguiría siendo None, así que se accede vía el módulo.
    session = db_connection.SessionLocal()
    try:
        # ¿Ya hay usuarios?
        count_stmt = select(func.count()).select_from(User)
        count = session.execute(count_stmt).scalar() or 0
        if count > 0:
            print("Ya existen usuarios. No se crea admin por defecto.")
            return

        role_admin = session.execute(select(Role).where(Role.name == "ADMIN")).scalar_one_or_none()
        if not role_admin:
            print("Crea primero la base de datos con scripts/init_db.sql (tabla role con ADMIN).")
            return

        password_hash = bcrypt.hashpw(b"Admin123!", bcrypt.gensalt(rounds=12)).decode("utf-8")
        admin = User(
            email="admin@smartroutes.com",
            password_hash=password_hash,
            full_name="Administrador",
            is_active=True,
            role_id=role_admin.id,
        )
        session.add(admin)
        session.commit()
        print("Usuario admin creado: admin@smartroutes.com / Admin123!")
    finally:
        session.close()


if __name__ == "__main__":
    main()
