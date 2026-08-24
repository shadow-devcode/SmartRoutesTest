"""
Modelo de rol (ADMIN, USER).
"""
from sqlalchemy import String, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Role(Base):
    __tablename__ = "role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relación: un rol tiene muchos usuarios
    users: Mapped[list["User"]] = relationship("User", back_populates="role", lazy="selectin")

    def __repr__(self) -> str:
        return f"<Role(id={self.id}, name={self.name!r})>"
