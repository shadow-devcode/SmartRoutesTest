"""
Servicio de autenticación: login, refresh, logout, verificación de fuerza bruta.
Contraseñas con BCrypt; tokens JWT.
"""
import hashlib
import secrets
import sys
from datetime import datetime, timedelta
from typing import Any

import bcrypt
import jwt
from sqlalchemy.orm import Session

from config.settings import settings
from exceptions.handlers import (
    UnauthorizedError,
    BadRequestError,
    TooManyRequestsError,
)
from models import User
from repositories.user_repository import UserRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from repositories.login_attempt_repository import LoginAttemptRepository


class AuthService:
    """Lógica de autenticación y tokens."""

    def __init__(self, session: Session):
        self.session = session
        self.user_repo = UserRepository(session)
        self.refresh_repo = RefreshTokenRepository(session)
        self.login_attempt_repo = LoginAttemptRepository(session)

    def _hash_password(self, password: str) -> str:
        """Hash con BCrypt (cost 12)."""
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")

    def _check_password(self, plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

    def _lockout_cutoff(self) -> datetime:
        """Fecha desde la cual contar intentos fallidos."""
        return datetime.utcnow() - timedelta(minutes=settings.LOCKOUT_MINUTES)

    def _record_failed_attempt(self, email: str, ip_address: str | None) -> None:
        """
        Registra y PERSISTE un intento fallido.

        El commit es imprescindible: el endpoint de login lanza una excepción
        justo después y el `session.close()` del controlador haría rollback,
        descartando el INSERT. Sin este commit, `count_failed_since` siempre
        devolvía 0 y el bloqueo por fuerza bruta nunca se activaba.
        """
        self.login_attempt_repo.record_attempt(email, ip_address, success=False)
        self.session.commit()

    def _check_brute_force(self, email: str, ip_address: str | None = None) -> None:
        """
        Si hay demasiados intentos fallidos, lanza TooManyRequestsError.

        Dos gates independientes:
        - Por email: protege una cuenta concreta (umbral bajo).
        - Por IP: frena un atacante que prueba muchas cuentas desde un mismo
          origen (umbral más alto por NAT). Se omite si no hay IP o está a 0.
        """
        since = self._lockout_cutoff()
        count = self.login_attempt_repo.count_failed_since(email, since)
        if count >= settings.MAX_LOGIN_ATTEMPTS:
            raise TooManyRequestsError(
                f"Demasiados intentos de acceso. Espera {settings.LOCKOUT_MINUTES} minutos."
            )

        ip_limit = settings.MAX_LOGIN_ATTEMPTS_PER_IP
        if ip_address and ip_limit > 0:
            ip_count = self.login_attempt_repo.count_failed_by_ip_since(ip_address, since)
            if ip_count >= ip_limit:
                raise TooManyRequestsError(
                    f"Demasiados intentos desde esta red. Espera {settings.LOCKOUT_MINUTES} minutos."
                )

    def _create_access_token(self, user_id: int, role: str) -> tuple[str, int]:
        """Genera JWT access token. Retorna (token, expires_in segundos)."""
        expire_min = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        expires_at = datetime.utcnow() + timedelta(minutes=expire_min)
        # PyJWT 2.8+ exige que "sub" sea str (InvalidSubjectError si es int).
        payload = {
            "sub": str(user_id),
            "role": role,
            "exp": expires_at,
            "iat": datetime.utcnow(),
            "type": "access",
        }
        token = jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        return token, expire_min * 60

    def _create_refresh_token(self, user_id: int) -> tuple[str, str, datetime]:
        """Genera refresh token (string aleatorio), guarda hash en BD. Retorna (token, hash, expires_at)."""
        raw = secrets.token_urlsafe(64)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        expires_at = datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        self.refresh_repo.create(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        return raw, token_hash, expires_at

    def login(self, email: str, password: str, ip_address: str | None = None) -> dict[str, Any]:
        """
        Login: verifica fuerza bruta, contraseña, genera access + refresh token.
        Registra intento (éxito o fallo).
        """
        self._check_brute_force(email, ip_address)

        user = self.user_repo.get_by_email_with_role(email)
        if not user:
            self._record_failed_attempt(email, ip_address)
            raise UnauthorizedError("Credenciales inválidas")

        if not user.is_active:
            self._record_failed_attempt(email, ip_address)
            raise UnauthorizedError("Usuario desactivado")

        if not self._check_password(password, user.password_hash):
            self._record_failed_attempt(email, ip_address)
            raise UnauthorizedError("Credenciales inválidas")

        self.login_attempt_repo.record_attempt(email, ip_address, success=True)
        self.session.commit()

        access_token, expires_in = self._create_access_token(user.id, user.role.name)
        refresh_raw, _, _ = self._create_refresh_token(user.id)
        self.session.commit()

        return {
            "access_token": access_token,
            "refresh_token": refresh_raw,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role.name,
                "assigned_mercadista": user.assigned_mercadista,
                "assigned_route_dataset_id": getattr(user, "assigned_route_dataset_id", None),
            },
        }

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Intercambia refresh token por nuevo access token (y opcionalmente nuevo refresh)."""
        if not refresh_token:
            raise BadRequestError("refresh_token requerido")

        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        rt = self.refresh_repo.find_valid_by_hash_for_update(token_hash)
        if not rt:
            # Detección de reuse/replay: si el hash coincide con un token YA
            # revocado (rotado en un refresh anterior), alguien está
            # reutilizando un refresh token viejo -> probable robo. Se revoca
            # toda la familia de sesiones del usuario para forzar relogin en
            # todos los dispositivos.
            reused = self.refresh_repo.find_by_hash(token_hash)
            if reused and reused.revoked:
                self.refresh_repo.revoke_all_for_user(reused.user_id)
                self.session.commit()
                print(
                    f"[SECURITY] Refresh token reuse detectado para user_id={reused.user_id}: "
                    "se revocaron todas sus sesiones",
                    file=sys.stderr,
                )
            raise UnauthorizedError("Refresh token inválido o expirado")

        user = self.user_repo.get_by_id_with_role(rt.user_id)
        if not user or not user.is_active:
            raise UnauthorizedError("Usuario no válido")

        # Opcional: revocar el refresh usado (rotación) y emitir uno nuevo
        rt.revoked = True
        access_token, expires_in = self._create_access_token(user.id, user.role.name)
        refresh_raw, _, _ = self._create_refresh_token(user.id)
        self.session.commit()

        return {
            "access_token": access_token,
            "refresh_token": refresh_raw,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role.name,
                "assigned_mercadista": user.assigned_mercadista,
                "assigned_route_dataset_id": getattr(user, "assigned_route_dataset_id", None),
            },
        }

    def logout(self, refresh_token: str | None, user_id: int | None) -> None:
        """
        Logout: si se envía refresh_token se revoca; si se envía user_id se revocan todos sus tokens.
        """
        if refresh_token:
            token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
            self.refresh_repo.revoke_by_hash(token_hash)
        if user_id:
            self.refresh_repo.revoke_all_for_user(user_id)
        self.session.commit()

    def verify_access_token(self, token: str) -> dict[str, Any]:
        """Decodifica y valida el JWT access token. Retorna payload (sub, role, exp, ...)."""
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
            if payload.get("type") != "access":
                raise UnauthorizedError("Token inválido")
            return payload
        except jwt.ExpiredSignatureError:
            raise UnauthorizedError("Token expirado")
        except jwt.InvalidTokenError:
            raise UnauthorizedError("Token inválido")
