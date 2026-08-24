"""
Repositorio de usuarios. Todas las consultas vía ORM (protección SQL Injection).
"""
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select, func

from models import User, Role


class UserRepository:
    """Acceso a datos de User."""

    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, user_id: int) -> User | None:
        """Obtiene usuario por ID."""
        return self.session.get(User, user_id)

    def get_by_id_with_role(self, user_id: int) -> User | None:
        """Obtiene usuario por ID con rol cargado."""
        stmt = select(User).where(User.id == user_id).options(joinedload(User.role))
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_email(self, email: str) -> User | None:
        """Obtiene usuario por email (parámetro bind, no concatenado)."""
        stmt = select(User).where(User.email == email)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_email_with_role(self, email: str) -> User | None:
        """Usuario por email con rol cargado."""
        stmt = select(User).where(User.email == email).options(joinedload(User.role))
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_full_name(self, full_name: str) -> User | None:
        """Usuario por nombre completo, comparación case-insensitive.

        Usa func.lower en ambos lados para no depender de la collation de la
        BD. `.first()` (no scalar_one) por si ya existieran duplicados previos
        a esta validación.
        """
        normalized = full_name.strip().lower()
        stmt = select(User).where(func.lower(User.full_name) == normalized).limit(1)
        return self.session.execute(stmt).scalars().first()

    def add(self, user: User) -> User:
        """Persiste un usuario."""
        self.session.add(user)
        self.session.flush()
        return user

    def list_all_with_role(self) -> list[User]:
        stmt = select(User).options(joinedload(User.role)).order_by(User.id.asc())
        return list(self.session.scalars(stmt).unique().all())

    def delete(self, user: User) -> None:
        self.session.delete(user)

    def get_role_by_name(self, name: str) -> Role | None:
        stmt = select(Role).where(Role.name == name)
        return self.session.execute(stmt).scalar_one_or_none()

    def count_active_admins_excluding_user(self, exclude_user_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(User)
            .join(Role, User.role_id == Role.id)
            .where(
                Role.name == "ADMIN",
                User.is_active.is_(True),
                User.id != exclude_user_id,
            )
        )
        return int(self.session.execute(stmt).scalar() or 0)
