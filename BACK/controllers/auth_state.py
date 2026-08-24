"""
Estado de carga de autenticación a nivel de aplicación.

Sin .env / sin MySQL / sin dependencias, la API de rutas/Excel sigue
funcionando pero sin filtros por rol ni multi-dataset. `set_auth_loaded` se
invoca desde `app.create_app()` cuando se completa el bootstrap de auth.

Vive en controllers/ (y no en config/) porque solo los controllers y los
servicios de path/access lo consultan.
"""
from __future__ import annotations

_AUTH_LOADED = False


def set_auth_loaded(value: bool) -> None:
    global _AUTH_LOADED
    _AUTH_LOADED = bool(value)


def is_auth_loaded() -> bool:
    return _AUTH_LOADED
