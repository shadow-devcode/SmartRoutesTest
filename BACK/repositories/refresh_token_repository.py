"""
Repositorio de refresh tokens.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select

from models import RefreshToken, User


class RefreshTokenRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self, user_id: int, token_hash: str, expires_at: datetime) -> RefreshToken:
        token = RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(token)
        self.session.flush()
        return token

    def find_by_hash(self, token_hash: str) -> RefreshToken | None:
        """
        Busca un token por hash sin filtrar por revocado/expirado.
        Se usa para detectar *reuse*: si alguien presenta un hash que coincide
        con un token ya revocado (rotado previamente), es señal de robo
        (replay de un refresh token interceptado).
        """
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        return self.session.execute(stmt).scalar_one_or_none()

    def find_valid_by_hash(self, token_hash: str) -> RefreshToken | None:
        """Busca un token por hash que no esté revocado ni expirado."""
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .where(RefreshToken.revoked == False)
            .where(RefreshToken.expires_at > datetime.utcnow())
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def find_valid_by_hash_for_update(self, token_hash: str) -> RefreshToken | None:
        """
        Igual que find_valid_by_hash pero bloquea la fila (FOR UPDATE) para serializar
        refreshes concurrentes (varias pestañas con la misma cookie).
        """
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .where(RefreshToken.revoked == False)
            .where(RefreshToken.expires_at > datetime.utcnow())
            .with_for_update()
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def revoke_by_hash(self, token_hash: str) -> int:
        """Revoca un token por hash. Retorna cantidad de filas actualizadas."""
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        token = self.session.execute(stmt).scalar_one_or_none()
        if token:
            token.revoked = True
            return 1
        return 0

    def revoke_all_for_user(self, user_id: int) -> int:
        """Revoca todos los refresh tokens de un usuario (logout)."""
        tokens = self.session.execute(
            select(RefreshToken).where(RefreshToken.user_id == user_id)
        ).scalars().all()
        for t in tokens:
            t.revoked = True
        return len(tokens)
