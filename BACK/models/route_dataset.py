"""
Dataset de rutas: un Excel procesado (principal + comparativa opcional) identificado en disco.

Los campos *_blob almacenan una copia binaria del .xlsx en la BD para garantizar
persistencia ante pérdida de la carpeta datasets/.  Si el archivo en disco no
existe pero el blob sí, el servicio lo restaura automáticamente.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.dialects.mysql import INTEGER, LONGBLOB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class RouteDataset(Base):
    __tablename__ = "route_dataset"

    id: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True, autoincrement=True)
    storage_slug: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    horarios_relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    comparativa_relative_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(
        INTEGER(unsigned=True),
        ForeignKey("user.id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )

    # Copia binaria del Excel procesado para restauración ante pérdida de disco
    horarios_blob: Mapped[bytes | None] = mapped_column(LONGBLOB, nullable=True)
    comparativa_blob: Mapped[bytes | None] = mapped_column(LONGBLOB, nullable=True)

    def __repr__(self) -> str:
        return f"<RouteDataset(id={self.id}, name={self.display_name!r}, active={self.is_active})>"
