"""
Gestión de datasets de rutas (varios Excel) y resolución del archivo activo.
"""
from __future__ import annotations

import os
import re
import shutil
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from exceptions.handlers import BadRequestError, ForbiddenError, NotFoundError
from models.route_dataset import RouteDataset
from repositories.route_dataset_repository import RouteDatasetRepository

# Coincide con constantes legacy en api.py (mismo directorio de trabajo del servidor).
LEGACY_HORARIOS_REL = "minoristas_horarios.xlsx"
LEGACY_COMPARATIVA_REL = "minoristas_horarios_comparativa.xlsx"
DATASETS_DIR = "datasets"
HORARIOS_FILENAME = "minoristas_horarios.xlsx"
COMPARATIVA_FILENAME = "minoristas_horarios_comparativa.xlsx"

_DISPLAY_NAME_RE = re.compile(r"^[\w\s\-.áéíóúÁÉÍÓÚñÑ(),]+$")


def _normalize_relative_path(path: str) -> str:
    rel = os.path.normpath(path.strip()).replace("\\", "/")
    if rel.startswith("..") or rel.startswith("/"):
        raise BadRequestError("Ruta de dataset inválida")
    return rel


def _safe_display_name(name: str) -> str:
    s = (name or "").strip()
    if not s or len(s) > 255:
        raise BadRequestError("El nombre debe tener entre 1 y 255 caracteres")
    if not _DISPLAY_NAME_RE.match(s):
        raise BadRequestError(
            "El nombre solo puede incluir letras, números, espacios y .-_(),"
        )
    return s


def _sanitize_filename(name: str, fallback: str = "dataset") -> str:
    """
    Convierte un display_name en un nombre de archivo válido para cualquier SO.
    Quita caracteres ilegales y espacios repetidos, limita longitud, y retorna
    `fallback` si el resultado queda vacío.
    """
    base = (name or "").strip()
    base = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "", base)
    base = re.sub(r"\s+", "_", base)
    base = base.strip("._ ")
    if not base:
        base = fallback
    return base[:150]


def build_excel_download_name(
    session: Session | None = None,
    *,
    suffix: str = "",
    fallback: str = "rutas_generadas",
) -> str:
    """
    Devuelve el nombre de archivo Excel a usar en una descarga, tomando el
    display_name del dataset activo. Si no hay dataset, usa `fallback`.
    `suffix` se añade antes de la extensión (ej: "_actualizado").
    """
    close = False
    if session is None:
        from database.connection import SessionLocal

        session = SessionLocal()
        close = True
    try:
        svc = RouteDatasetService(session)
        active = svc.repo.get_active()
        base = _sanitize_filename(active.display_name if active else "", fallback=fallback)
        suf = _sanitize_filename(suffix, fallback="") if suffix else ""
        return f"{base}{('_' + suf) if suf else ''}.xlsx"
    finally:
        if close:
            session.close()


class RouteDatasetService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = RouteDatasetRepository(session)

    def to_public_dict(self, row: RouteDataset) -> dict:
        return {
            "id": row.id,
            "display_name": row.display_name,
            "storage_slug": row.storage_slug,
            "is_active": bool(row.is_active),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "horarios_exists": os.path.isfile(os.path.abspath(row.horarios_relative_path)),
            "comparativa_exists": bool(
                row.comparativa_relative_path
                and os.path.isfile(os.path.abspath(row.comparativa_relative_path))
            ),
        }

    def list_datasets(self) -> list[dict]:
        return [self.to_public_dict(r) for r in self.repo.list_all_ordered()]

    def prepare_new_output_paths(self) -> tuple[str, str]:
        """Genera carpeta datasets/{uuid}/ y rutas relativas del Excel principal."""
        slug = str(uuid.uuid4())
        out_dir = os.path.join(DATASETS_DIR, slug)
        os.makedirs(out_dir, exist_ok=True)
        rel = os.path.join(out_dir, HORARIOS_FILENAME)
        return slug, _normalize_relative_path(rel)

    # ------------------------------------------------------------------
    # Helpers de blob (persistencia binaria en BD)
    # ------------------------------------------------------------------

    @staticmethod
    def _read_file_bytes(path: str) -> bytes | None:
        """Lee un archivo de disco y devuelve sus bytes; None si no existe."""
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    @staticmethod
    def _restore_file_from_blob(blob: bytes, dest_path: str) -> bool:
        """Escribe bytes en dest_path (crea carpeta si hace falta). Devuelve True si OK."""
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            with open(dest_path, "wb") as fh:
                fh.write(blob)
            return True
        except OSError:
            return False

    def ensure_file_on_disk(self, row: "RouteDataset") -> bool:
        """
        Comprueba si el archivo principal existe en disco.
        Si no existe pero hay blob en BD, lo restaura automáticamente.
        Devuelve True si el archivo está disponible al terminar.
        """
        abs_path = os.path.abspath(row.horarios_relative_path)
        if os.path.isfile(abs_path):
            return True
        if row.horarios_blob:
            ok = self._restore_file_from_blob(row.horarios_blob, abs_path)
            if ok:
                print(
                    f"[RouteDataset] Archivo restaurado desde BD → {abs_path}",
                    flush=True,
                )
            return ok
        return False

    def ensure_comparativa_on_disk(self, row: "RouteDataset") -> bool:
        """Lo mismo que ensure_file_on_disk pero para la comparativa."""
        if not row.comparativa_relative_path:
            return False
        abs_path = os.path.abspath(row.comparativa_relative_path)
        if os.path.isfile(abs_path):
            return True
        if row.comparativa_blob:
            ok = self._restore_file_from_blob(row.comparativa_blob, abs_path)
            if ok:
                print(
                    f"[RouteDataset] Comparativa restaurada desde BD → {abs_path}",
                    flush=True,
                )
            return ok
        return False

    def sync_blob_for_dataset(self, dataset_id: int) -> None:
        """
        Lee el archivo de disco del dataset indicado y actualiza su blob en BD.
        Útil para sincronizar datasets creados antes de esta característica.
        """
        row = self.repo.get_by_id(dataset_id)
        if not row:
            raise NotFoundError("Dataset no encontrado")
        blob_h = self._read_file_bytes(os.path.abspath(row.horarios_relative_path))
        if blob_h:
            row.horarios_blob = blob_h
        if row.comparativa_relative_path:
            blob_c = self._read_file_bytes(os.path.abspath(row.comparativa_relative_path))
            if blob_c:
                row.comparativa_blob = blob_c
        self.session.commit()

    # ------------------------------------------------------------------

    def register_after_processing(
        self,
        *,
        horarios_relative_path: str,
        display_name: str,
        created_by_user_id: int | None,
    ) -> RouteDataset:
        """
        Tras procesar_minoristas con éxito: inserta fila y guarda el blob del Excel en BD.
        Activa este dataset solo si no hay ningún activo (primer carga o sistema vacío).
        """
        rel = _normalize_relative_path(horarios_relative_path)
        abs_path = os.path.abspath(rel)
        if not os.path.isfile(abs_path):
            raise BadRequestError("El archivo de horarios generado no existe en disco")

        name = _safe_display_name(display_name)
        rel_norm = rel.replace("\\", "/")
        path_parts = [p for p in rel_norm.split("/") if p]
        if len(path_parts) >= 2 and path_parts[0] == DATASETS_DIR:
            slug = path_parts[1]
            try:
                uuid.UUID(slug)
            except ValueError as e:
                raise BadRequestError("Identificador de carpeta de dataset inválido") from e
        else:
            slug = str(uuid.uuid4())

        if self.repo.get_active() is None:
            self.repo.deactivate_all()
            make_active = True
        else:
            make_active = False

        # Leer el Excel como bytes para guardar en BD
        horarios_bytes = self._read_file_bytes(abs_path)

        row = RouteDataset(
            storage_slug=slug,
            display_name=name,
            horarios_relative_path=rel,
            comparativa_relative_path=None,
            is_active=make_active,
            created_at=datetime.utcnow(),
            created_by_user_id=created_by_user_id,
            horarios_blob=horarios_bytes,
            comparativa_blob=None,
        )
        self.repo.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def set_active(self, dataset_id: int) -> RouteDataset:
        row = self.repo.get_by_id(dataset_id)
        if not row:
            raise NotFoundError("Dataset no encontrado")
        # Intenta restaurar desde blob si el archivo falta en disco
        if not self.ensure_file_on_disk(row):
            raise BadRequestError(
                "El archivo principal de este dataset no existe en disco ni en la base de datos"
            )

        self.repo.deactivate_all()
        row.is_active = True
        self.session.commit()
        self.session.refresh(row)
        return row

    def delete_dataset(self, dataset_id: int) -> None:
        row = self.repo.get_by_id(dataset_id)
        if not row:
            raise NotFoundError("Dataset no encontrado")
        if self.repo.count_all() <= 1:
            raise BadRequestError("No se puede eliminar el único dataset del sistema")

        was_active = row.is_active
        rel_h = row.horarios_relative_path
        rel_norm = rel_h.replace("\\", "/")
        path_parts = [p for p in rel_norm.split("/") if p]
        under_datasets = len(path_parts) >= 2 and path_parts[0] == DATASETS_DIR
        folder = os.path.join(DATASETS_DIR, row.storage_slug) if under_datasets else None

        self.repo.delete(row)
        self.session.commit()

        if folder and os.path.isdir(folder) and ".." not in row.storage_slug:
            try:
                shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass

        if was_active:
            self._activate_fallback_most_recent()

    def _activate_fallback_most_recent(self) -> None:
        rows = self.repo.list_all_ordered()
        if not rows:
            return
        self.repo.deactivate_all()
        for r in rows:
            if self.ensure_file_on_disk(r):
                r.is_active = True
                break
        self.session.commit()

    def bootstrap_legacy_if_empty(self) -> None:
        """
        Si no hay filas y existe minoristas_horarios.xlsx en la raíz del proyecto,
        registra un dataset legacy y lo deja activo.
        """
        if self.repo.count_all() > 0:
            return
        abs_legacy = os.path.abspath(LEGACY_HORARIOS_REL)
        if not os.path.isfile(abs_legacy):
            return
        rel = _normalize_relative_path(LEGACY_HORARIOS_REL)
        comp_rel = None
        if os.path.isfile(os.path.abspath(LEGACY_COMPARATIVA_REL)):
            comp_rel = _normalize_relative_path(LEGACY_COMPARATIVA_REL)

        row = RouteDataset(
            storage_slug=str(uuid.uuid4()),
            display_name="Dataset principal (legacy)",
            horarios_relative_path=rel,
            comparativa_relative_path=comp_rel,
            is_active=True,
            created_at=datetime.utcnow(),
            created_by_user_id=None,
        )
        self.repo.add(row)
        self.session.commit()

    def comparativa_path_for_active(self) -> str | None:
        active = self.repo.get_active()
        if not active:
            return None
        if active.comparativa_relative_path:
            p = os.path.abspath(active.comparativa_relative_path)
            if os.path.isfile(p):
                return p
        return None

    def ensure_comparativa_path_for_active_write(self) -> str:
        """Ruta absoluta donde guardar la comparativa del dataset activo (crea relación si hace falta)."""
        active = self.repo.get_active()
        if not active:
            raise NotFoundError("No hay dataset activo. Procesa un Excel o activa uno existente.")
        if not os.path.isfile(os.path.abspath(active.horarios_relative_path)):
            raise BadRequestError("El dataset activo no tiene archivo principal válido")

        if active.comparativa_relative_path:
            ap = os.path.abspath(active.comparativa_relative_path)
            d = os.path.dirname(ap)
            os.makedirs(d, exist_ok=True)
            return ap

        base_dir = os.path.dirname(os.path.abspath(active.horarios_relative_path))
        rel_comp = os.path.join(os.path.relpath(base_dir, os.getcwd()), COMPARATIVA_FILENAME)
        rel_comp = _normalize_relative_path(rel_comp)
        active.comparativa_relative_path = rel_comp
        self.session.commit()
        return os.path.abspath(rel_comp)


def resolve_effective_horarios_absolute(
    session: Session,
    *,
    user_id: int | None,
    role: str | None,
) -> str | None:
    """
    Excel de horarios según rol: ADMIN → activo global; EDITOR/VISUALIZADOR → dataset del usuario;
    USER → dataset asignado si existe, si no activo global (compatibilidad).
    """
    r = (role or "").strip().upper()
    if r == "ADMIN":
        return resolve_active_horarios_absolute(session)

    if user_id is None:
        return resolve_active_horarios_absolute(session)

    from models.user import User as UserModel

    u = session.get(UserModel, user_id)
    if not u:
        return resolve_active_horarios_absolute(session)

    svc = RouteDatasetService(session)

    if r in ("EDITOR", "VISUALIZADOR"):
        if u.assigned_route_dataset_id:
            rd = session.get(RouteDataset, u.assigned_route_dataset_id)
            if rd and svc.ensure_file_on_disk(rd):
                return os.path.abspath(rd.horarios_relative_path)
        return None

    if r == "USER":
        if u.assigned_route_dataset_id:
            rd = session.get(RouteDataset, u.assigned_route_dataset_id)
            if rd and svc.ensure_file_on_disk(rd):
                return os.path.abspath(rd.horarios_relative_path)
        return resolve_active_horarios_absolute(session)

    return resolve_active_horarios_absolute(session)


def resolve_effective_comparativa_absolute(
    session: Session,
    *,
    user_id: int | None,
    role: str | None,
) -> str | None:
    r = (role or "").strip().upper()
    if r == "ADMIN":
        return resolve_active_comparativa_absolute(session)
    if user_id is None:
        return resolve_active_comparativa_absolute(session)
    from models.user import User as UserModel

    u = session.get(UserModel, user_id)
    if not u:
        return resolve_active_comparativa_absolute(session)
    if r in ("EDITOR", "VISUALIZADOR", "USER") and u.assigned_route_dataset_id:
        rd = session.get(RouteDataset, u.assigned_route_dataset_id)
        if rd and rd.comparativa_relative_path:
            p = os.path.abspath(rd.comparativa_relative_path)
            if os.path.isfile(p):
                return p
    if r in ("EDITOR", "VISUALIZADOR"):
        return None
    return resolve_active_comparativa_absolute(session)


def resolve_active_horarios_absolute(session: Session | None = None) -> str | None:
    """
    Ruta absoluta del Excel principal activo; None si no hay datos.
    Si el archivo no está en disco pero hay blob en BD, lo restaura automáticamente.
    """
    close = False
    if session is None:
        from database.connection import SessionLocal

        session = SessionLocal()
        close = True
    try:
        svc = RouteDatasetService(session)
        active = svc.repo.get_active()
        if active and svc.ensure_file_on_disk(active):
            return os.path.abspath(active.horarios_relative_path)
        legacy = os.path.abspath(LEGACY_HORARIOS_REL)
        if os.path.isfile(legacy):
            return legacy
        return None
    finally:
        if close:
            session.close()


def resolve_active_comparativa_absolute(session: Session | None = None) -> str | None:
    close = False
    if session is None:
        from database.connection import SessionLocal

        session = SessionLocal()
        close = True
    try:
        svc = RouteDatasetService(session)
        p = svc.comparativa_path_for_active()
        if p:
            return p
        leg = os.path.abspath(LEGACY_COMPARATIVA_REL)
        if os.path.isfile(leg):
            return leg
        return None
    finally:
        if close:
            session.close()


def get_active_horarios_excel_path_for_admin(session: Session) -> str:
    """Para servicios que requieren un path existente (asignación mercadista)."""
    p = resolve_active_horarios_absolute(session)
    if not p:
        raise NotFoundError("No hay archivo de rutas activo en el servidor")
    return p


def get_horarios_excel_path_for_management(
    session: Session, *, actor_user_id: int | None, actor_role: str | None
) -> str:
    """
    Excel con el que opera el actor en administración (mercadistas, descarga).
    ADMIN → activo global; EDITOR → horarios del dataset asignado al editor.
    """
    r = (actor_role or "").strip().upper()
    if r == "ADMIN":
        return get_active_horarios_excel_path_for_admin(session)
    if r == "EDITOR" and actor_user_id is not None:
        from models.user import User as UserModel

        u = session.get(UserModel, actor_user_id)
        if not u or u.assigned_route_dataset_id is None:
            raise ForbiddenError(
                "El editor no tiene un Excel de rutas asignado. Pídele a un administrador que te asigne un dataset."
            )
        rd = session.get(RouteDataset, u.assigned_route_dataset_id)
        if not rd:
            raise NotFoundError("Dataset del editor no encontrado")
        p = os.path.abspath(rd.horarios_relative_path)
        if not os.path.isfile(p):
            raise NotFoundError("No hay archivo de rutas para el Excel asignado al editor")
        return p
    raise ForbiddenError("Sin permiso para esta operación")
