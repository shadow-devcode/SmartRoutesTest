"""
Esquemas Pydantic para login, refresh y respuestas.
"""
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Body del endpoint de login."""

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    """Body del endpoint de refresh token."""

    refresh_token: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    """Respuesta con access_token y opcional refresh_token."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int  # segundos


class UserResponse(BaseModel):
    """Usuario para respuesta (sin contraseña)."""

    id: int
    email: str
    full_name: str | None
    role: str

    class Config:
        from_attributes = True
