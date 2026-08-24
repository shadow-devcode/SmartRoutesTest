"""Validación de cuerpos JSON para gestión de datasets de rutas."""
from pydantic import BaseModel, Field


class RouteDatasetActivateRequest(BaseModel):
    dataset_id: int = Field(..., ge=1)
