"""
Gestión de usuarios: administradores (ámbito global) y editores (ámbito por dataset de rutas).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from exceptions.handlers import BadRequestError, ForbiddenError, NotFoundError
from models import User, Role
from repositories.user_repository import UserRepository
from repositories.refresh_token_repository import RefreshTokenRepository

_UNSET = object()


class UserAdminService:
    def __init__(self, session: Session):
        self.session = session
        self.user_repo = UserRepository(session)
        self.refresh_repo = RefreshTokenRepository(session)

    def _hash_password(self, password: str) -> str:
        import bcrypt

        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")

    def _user_to_dict(self, u: User) -> dict:
        return {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role.name,
            "is_active": u.is_active,
            "assigned_mercadista": u.assigned_mercadista,
            "assigned_route_dataset_id": u.assigned_route_dataset_id,
            "created_by_user_id": getattr(u, "created_by_user_id", None),
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None,
        }

    def _editor_dataset_id(self, actor_user_id: int) -> int:
        actor = self.session.get(User, actor_user_id)
        if not actor or actor.assigned_route_dataset_id is None:
            raise ForbiddenError(
                "No tienes un Excel de rutas asignado; no puedes gestionar usuarios hasta que un administrador te asigne un dataset."
            )
        return int(actor.assigned_route_dataset_id)

    def _assert_editor_can_touch_user(self, target: User, dataset_id: int) -> None:
        if target.role and target.role.name == "ADMIN":
            raise ForbiddenError("No puedes gestionar administradores")
        if target.role and target.role.name == "EDITOR":
            raise ForbiddenError("No puedes gestionar editores")
        tid = target.assigned_route_dataset_id
        if (tid or 0) != dataset_id:
            raise ForbiddenError("Ese usuario está fuera de tu ámbito")

    def _list_users_created_by_editor(self, editor_user_id: int) -> list[dict]:
        editor = self.user_repo.get_by_id_with_role(editor_user_id)
        if not editor or not editor.role or editor.role.name != "EDITOR":
            raise BadRequestError("El identificador no corresponde a un usuario con rol editor")
        out: list[dict] = []
        for u in self.user_repo.list_all_with_role():
            if (getattr(u, "created_by_user_id", None) or 0) == editor_user_id:
                out.append(self._user_to_dict(u))
        return out

    def list_users(
        self,
        *,
        actor_role: str | None,
        actor_user_id: int | None,
        created_by_editor_id: int | None = None,
    ) -> list[dict]:
        ar = (actor_role or "").strip().upper()
        if created_by_editor_id is not None:
            if ar != "ADMIN":
                raise ForbiddenError("Solo el administrador puede consultar usuarios por editor")
            return self._list_users_created_by_editor(created_by_editor_id)
        if ar == "ADMIN":
            users = self.user_repo.list_all_with_role()
            return [self._user_to_dict(u) for u in users]
        if ar != "EDITOR" or actor_user_id is None:
            raise ForbiddenError("Sin permiso para listar usuarios")
        ds_id = self._editor_dataset_id(actor_user_id)
        out: list[dict] = []
        for u in self.user_repo.list_all_with_role():
            if u.role and u.role.name in ("ADMIN", "EDITOR"):
                continue
            if (u.assigned_route_dataset_id or 0) != ds_id:
                continue
            out.append(self._user_to_dict(u))
        return out

    def create_user(
        self,
        full_name: str,
        email: str,
        password: str,
        *,
        role: str = "USER",
        actor_role: str | None = None,
        actor_user_id: int | None = None,
    ) -> dict:
        ar = (actor_role or "ADMIN").strip().upper()
        email_norm = email.strip().lower()
        if self.user_repo.get_by_email(email_norm):
            raise BadRequestError("Ya existe un usuario con ese correo")

        full_name_norm = full_name.strip()
        if not full_name_norm:
            raise BadRequestError("El nombre completo es obligatorio")
        if self.user_repo.get_by_full_name(full_name_norm):
            raise BadRequestError("Ya existe un usuario con ese nombre completo")

        role_row = self.user_repo.get_role_by_name(role)
        if not role_row:
            raise BadRequestError("Rol inválido o no configurado en la base de datos")

        assigned_ds: int | None = None
        created_by: int | None = None
        if ar == "EDITOR":
            if actor_user_id is None:
                raise ForbiddenError("Sesión inválida")
            if role != "USER":
                raise ForbiddenError("Solo puedes crear usuarios con rol Usuario (USER)")
            assigned_ds = self._editor_dataset_id(actor_user_id)
            created_by = int(actor_user_id)
        elif ar != "ADMIN":
            raise ForbiddenError("Sin permiso para crear usuarios")

        user = User(
            email=email_norm,
            password_hash=self._hash_password(password),
            full_name=full_name_norm,
            is_active=True,
            role_id=role_row.id,
            assigned_mercadista=None,
            assigned_route_dataset_id=assigned_ds,
            created_by_user_id=created_by,
        )
        self.user_repo.add(user)
        self.session.commit()
        self.session.refresh(user)
        user = self.user_repo.get_by_id_with_role(user.id)
        return self._user_to_dict(user)

    def update_user(
        self,
        user_id: int,
        *,
        actor_user_id: int,
        actor_role: str | None = None,
        full_name: str | None = None,
        email: str | None = None,
        password: str | None = None,
        role: str | None = None,
        is_active: bool | None = None,
        assigned_mercadista=_UNSET,
        assigned_route_dataset_id=_UNSET,
    ) -> dict:
        ar = (actor_role or "ADMIN").strip().upper()
        user = self.user_repo.get_by_id_with_role(user_id)
        if not user:
            raise NotFoundError("Usuario no encontrado")

        if ar == "EDITOR":
            ds_id = self._editor_dataset_id(actor_user_id)
            self._assert_editor_can_touch_user(user, ds_id)
            if user_id == actor_user_id:
                raise ForbiddenError("No puedes modificar tu propio usuario desde esta vista")
            if assigned_route_dataset_id is not _UNSET:
                raise ForbiddenError("No puedes cambiar el Excel asignado al usuario")
        elif ar != "ADMIN":
            raise ForbiddenError("Sin permiso")

        if full_name is not None:
            fn = full_name.strip()
            other = self.user_repo.get_by_full_name(fn)
            if other and other.id != user.id:
                raise BadRequestError("Ya existe otro usuario con ese nombre completo")
            user.full_name = fn
        if email is not None:
            email_norm = email.strip().lower()
            other = self.user_repo.get_by_email(email_norm)
            if other and other.id != user.id:
                raise BadRequestError("Ya existe otro usuario con ese correo")
            user.email = email_norm
        if password is not None:
            user.password_hash = self._hash_password(password)

        became_visualizador = False
        if role is not None:
            if ar == "EDITOR":
                raise ForbiddenError(
                    "No puedes cambiar el rol de los usuarios; solo un administrador puede asignar o quitar roles."
                )
            new_role = self.user_repo.get_role_by_name(role)
            if not new_role:
                raise BadRequestError("Rol inválido")
            prev_role_name = self.session.execute(
                select(Role.name).where(Role.id == user.role_id)
            ).scalar_one()
            if prev_role_name == "ADMIN" and role != "ADMIN":
                others = self.user_repo.count_active_admins_excluding_user(user.id)
                if user.is_active and others == 0:
                    raise ForbiddenError("Debe existir al menos un administrador activo")
            if role == "VISUALIZADOR" and prev_role_name != "VISUALIZADOR":
                became_visualizador = True
            user.role_id = new_role.id

        if is_active is not None:
            role_name_now = self.session.execute(
                select(Role.name).where(Role.id == user.role_id)
            ).scalar_one()
            if not is_active:
                ruta = (user.assigned_mercadista or "").strip()
                if ruta and role_name_now == "USER":
                    raise BadRequestError(
                        "No se puede desactivar el usuario mientras tenga una ruta asignada. "
                        "Quítale la ruta en «Asignación de Usuario» primero."
                    )
            if not is_active and role_name_now == "ADMIN":
                others = self.user_repo.count_active_admins_excluding_user(user.id)
                if others == 0:
                    raise ForbiddenError("No puedes desactivar al único administrador")
            user.is_active = is_active

        if assigned_mercadista is not _UNSET:
            role_name_now = self.session.execute(
                select(Role.name).where(Role.id == user.role_id)
            ).scalar_one()
            if role_name_now != "USER":
                raise BadRequestError(
                    "Solo los usuarios con rol Usuario (USER) pueden tener ruta (mercadista) asignada"
                )
            val = assigned_mercadista
            if isinstance(val, str):
                val = val.strip() or None
            user.assigned_mercadista = val

        if assigned_route_dataset_id is not _UNSET and ar == "ADMIN":
            val_ds = assigned_route_dataset_id
            if val_ds is not None and not isinstance(val_ds, int):
                try:
                    val_ds = int(val_ds)
                except (TypeError, ValueError):
                    raise BadRequestError("assigned_route_dataset_id inválido")
            user.assigned_route_dataset_id = val_ds
        elif became_visualizador and ar == "ADMIN":
            # No heredar el Excel del usuario/ámbito anterior: el admin debe elegirlo en «Excels / Rutas».
            user.assigned_route_dataset_id = None

        role_final = self.session.execute(
            select(Role.name).where(Role.id == user.role_id)
        ).scalar_one()
        if role_final != "USER":
            user.assigned_mercadista = None

        self.session.commit()
        self.session.refresh(user)
        user = self.user_repo.get_by_id_with_role(user.id)
        return self._user_to_dict(user)

    def delete_user(self, user_id: int, actor_user_id: int, *, actor_role: str | None = None) -> None:
        if user_id == actor_user_id:
            raise ForbiddenError("No puedes eliminar tu propio usuario")

        ar = (actor_role or "ADMIN").strip().upper()
        user = self.user_repo.get_by_id_with_role(user_id)
        if not user:
            raise NotFoundError("Usuario no encontrado")

        if ar == "EDITOR":
            ds_id = self._editor_dataset_id(actor_user_id)
            self._assert_editor_can_touch_user(user, ds_id)
        elif ar != "ADMIN":
            raise ForbiddenError("Sin permiso")

        if user.role.name == "ADMIN" and user.is_active:
            others = self.user_repo.count_active_admins_excluding_user(user.id)
            if others == 0:
                raise ForbiddenError("No puedes eliminar al único administrador activo")

        ruta = (user.assigned_mercadista or "").strip()
        if ruta:
            raise BadRequestError(
                "No se puede eliminar el usuario mientras tenga una ruta asignada. "
                "Quítale la ruta en «Asignación de Usuario» (o desasigna el mercadista en el Excel) "
                "y vuelve a intentarlo."
            )

        self.refresh_repo.revoke_all_for_user(user.id)
        self.user_repo.delete(user)
        self.session.commit()
