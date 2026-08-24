"""
Registro de intentos de login para protección contra fuerza bruta.
"""
from sqlalchemy import String, Integer, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from .base import Base


class LoginAttempt(Base):
    __tablename__ = "login_attempt"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Indexada: el rate-limit por IP filtra por esta columna (evita full scan).
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True, index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Indexada: ambas consultas de rate-limit filtran por ventana temporal.
    attempted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self) -> str:
        return f"<LoginAttempt(id={self.id}, email={self.email!r}, success={self.success})>"
