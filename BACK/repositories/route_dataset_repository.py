"""
Acceso a datos de route_dataset (sin lógica de negocio).
"""
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from models.route_dataset import RouteDataset


class RouteDatasetRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_by_id(self, dataset_id: int) -> RouteDataset | None:
        return self.session.get(RouteDataset, dataset_id)

    def get_active(self) -> RouteDataset | None:
        stmt = select(RouteDataset).where(RouteDataset.is_active.is_(True)).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_all_ordered(self) -> list[RouteDataset]:
        stmt = select(RouteDataset).order_by(RouteDataset.created_at.desc(), RouteDataset.id.desc())
        return list(self.session.scalars(stmt).all())

    def count_all(self) -> int:
        return int(self.session.scalar(select(func.count()).select_from(RouteDataset)) or 0)

    def add(self, row: RouteDataset) -> RouteDataset:
        self.session.add(row)
        self.session.flush()
        return row

    def deactivate_all(self) -> None:
        self.session.execute(update(RouteDataset).values(is_active=False))

    def delete(self, row: RouteDataset) -> None:
        self.session.delete(row)
