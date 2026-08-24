"""
Modelo de usuario. Contraseña almacenada como hash (BCrypt).
"""
from sqlalchemy import String, Integer, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from .base import Base


class User(Base):
    __tablename__ = "user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("role.id", ondelete="RESTRICT"), nullable=False)
    assigned_route_dataset_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("route_dataset.id", ondelete="SET NULL"), nullable=True, default=None
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None
    )
    assigned_mercadista: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    role: Mapped["Role"] = relationship("Role", back_populates="users", lazy="joined")
    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        "RefreshToken", back_populates="user", lazy="noload"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email!r})>"
