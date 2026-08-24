"""
Esquemas Pydantic para administración de usuarios (solo rol ADMIN en API).
"""
import re
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

RoleName = Literal["ADMIN", "USER", "EDITOR", "VISUALIZADOR"]

# Regla de contraseña: >=8 chars, al menos una mayúscula, una minúscula y un
# carácter especial (cualquier carácter no alfanumérico ASCII). Las mismas
# comprobaciones se replican en el frontend (admin.component) para feedback
# inmediato; esta es la fuente de verdad y la única que protege la API.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
PASSWORD_RULE_MSG = (
    f"La contraseña debe tener al menos {PASSWORD_MIN_LENGTH} caracteres e incluir "
    "una mayúscula, una minúscula y un carácter especial."
)


def validar_password_fuerte(password: str) -> str:
    """Valida la robustez de la contraseña; lanza ValueError si no cumple."""
    if (
        len(password) < PASSWORD_MIN_LENGTH
        or not re.search(r"[A-Z]", password)
        or not re.search(r"[a-z]", password)
        or not re.search(r"[^A-Za-z0-9]", password)
    ):
        raise ValueError(PASSWORD_RULE_MSG)
    return password


class AdminUserCreateRequest(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    # min_length se valida en validar_password_fuerte para emitir un único
    # mensaje en español (Field.min_length dispararía antes con texto inglés).
    password: str = Field(..., max_length=PASSWORD_MAX_LENGTH)
    role: RoleName = "USER"

    @field_validator("password")
    @classmethod
    def _password_fuerte(cls, v: str) -> str:
        return validar_password_fuerte(v)


class AdminUserUpdateRequest(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=255)
    email: EmailStr | None = None
    password: str | None = Field(None, max_length=PASSWORD_MAX_LENGTH)
    role: RoleName | None = None
    is_active: bool | None = None
    assigned_mercadista: str | None = Field(None, max_length=255)

    @field_validator("password")
    @classmethod
    def _password_fuerte(cls, v: str | None) -> str | None:
        # En edición la contraseña es opcional: solo se valida si viene.
        return v if v is None else validar_password_fuerte(v)
