import { Component, OnDestroy, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { FormsModule } from '@angular/forms';
import {
  ReactiveFormsModule,
  FormBuilder,
  FormGroup,
  Validators,
} from '@angular/forms';
import { finalize } from 'rxjs';
import { HttpErrorResponse } from '@angular/common/http';
import { AuthService } from '../../services/auth.service';
import { AdminUsersService } from '../../services/admin-users.service';
import type {
  AdminUserRow,
  MercadistaAsignacionRow,
  MercadistasAsignacionExcelContext,
} from '../../models/admin-user.model';
import { etiquetaRol as etiquetaRolUtil } from '../../utils/roles';

type AdminToastVariant = 'success' | 'error';

interface AdminToast {
  id: number;
  message: string;
  variant: AdminToastVariant;
}

@Component({
  selector: 'app-admin',
  standalone: true,
  imports: [CommonModule, RouterLink, ReactiveFormsModule, FormsModule],
  templateUrl: './admin.component.html',
  styleUrls: [
    './admin.component.pagina.css',
    './admin.component.formularios.css',
    './admin.component.tablas.css',
    './admin.component.modal.css',
  ],
})
export class AdminComponent implements OnInit, OnDestroy {
  users: AdminUserRow[] = [];
  loadingList = false;
  savingCreate = false;
  savingEdit = false;
  deletingId: number | null = null;

  /** Notificaciones flotantes (esquina inferior derecha). */
  toasts: AdminToast[] = [];
  private toastSeq = 0;
  private readonly toastTimers = new Map<number, ReturnType<typeof setTimeout>>();
  private readonly toastDurationMs = 6500;

  createForm: FormGroup;
  editForm: FormGroup;
  editingUser: AdminUserRow | null = null;
  /** Usuario pendiente de confirmar eliminación (modal flotante). */
  pendingDelete: AdminUserRow | null = null;

  asignacionRows: MercadistaAsignacionRow[] = [];
  /** Excel (dataset) asociado a la tabla de asignación mercadista. */
  excelContextoAsignacion: MercadistasAsignacionExcelContext | null = null;
  loadingAsignacion = false;
  guardandoMercadista: string | null = null;
  downloadingHorariosExcel = false;

  /** Solo admin: filtro por editor que creó la cuenta. */
  editorFiltroCreadorId: number | null = null;
  usuariosCreadosPorEditor: AdminUserRow[] = [];
  loadingUsuariosPorEditor = false;

  /**
   * Regla de contraseña (idéntica al backend en schemas/admin_user_schemas):
   * >=8 caracteres, al menos una mayúscula, una minúscula y un carácter
   * especial (cualquier carácter no alfanumérico ASCII).
   */
  readonly passwordPattern = /^(?=.*[a-z])(?=.*[A-Z])(?=.*[^A-Za-z0-9]).{8,}$/;

  constructor(
    public auth: AuthService,
    private readonly adminUsers: AdminUsersService,
    private readonly fb: FormBuilder
  ) {
    this.createForm = this.fb.group({
      full_name: ['', [Validators.required, Validators.maxLength(255)]],
      email: ['', [Validators.required, Validators.email]],
      password: ['', [Validators.required, Validators.pattern(this.passwordPattern)]],
      role: [
        'USER' as 'ADMIN' | 'USER' | 'EDITOR' | 'VISUALIZADOR',
        [Validators.required],
      ],
    });

    this.editForm = this.fb.group({
      full_name: ['', [Validators.required, Validators.maxLength(255)]],
      email: ['', [Validators.required, Validators.email]],
      // Opcional al editar: Validators.pattern no valida cadena vacía, así que
      // solo se exige el formato cuando el admin escribe una nueva contraseña.
      password: ['', [Validators.pattern(this.passwordPattern)]],
      role: ['USER' as 'ADMIN' | 'USER' | 'EDITOR' | 'VISUALIZADOR', [Validators.required]],
      is_active: [true],
    });
  }

  /** Valor actual de la contraseña del formulario de creación. */
  get createPassword(): string {
    return (this.createForm.get('password')?.value as string) ?? '';
  }

  /** Requisitos de contraseña evaluados en vivo (para el checklist del form de creación). */
  get pwHasMinLen(): boolean {
    return this.createPassword.length >= 8;
  }
  get pwHasUpper(): boolean {
    return /[A-Z]/.test(this.createPassword);
  }
  get pwHasLower(): boolean {
    return /[a-z]/.test(this.createPassword);
  }
  get pwHasSpecial(): boolean {
    return /[^A-Za-z0-9]/.test(this.createPassword);
  }

  /** Editor sin rol admin: formularios y lista acotados por el backend. */
  get esVistaEditor(): boolean {
    return this.auth.isEditor() && !this.auth.isAdmin();
  }

  ngOnDestroy(): void {
    for (const t of this.toastTimers.values()) {
      clearTimeout(t);
    }
    this.toastTimers.clear();
  }

  showToast(message: string, variant: AdminToastVariant = 'success'): void {
    const id = ++this.toastSeq;
    this.toasts = [...this.toasts, { id, message, variant }];
    const handle = setTimeout(() => this.dismissToast(id), this.toastDurationMs);
    this.toastTimers.set(id, handle);
  }

  dismissToast(id: number): void {
    const t = this.toastTimers.get(id);
    if (t) {
      clearTimeout(t);
      this.toastTimers.delete(id);
    }
    this.toasts = this.toasts.filter(x => x.id !== id);
  }

  /** Solo usuarios activos con rol Usuario (aparecen en «Asignación de Usuario»). */
  get usuariosParaRuta(): AdminUserRow[] {
    return this.users.filter(u => u.role === 'USER' && u.is_active);
  }

  /** Editores para el desplegable de auditoría (solo admin). */
  get editoresParaFiltroCreador(): AdminUserRow[] {
    return [...this.users.filter(u => u.role === 'EDITOR')].sort((a, b) => a.id - b.id);
  }

  ngOnInit(): void {
    if (this.esVistaEditor) {
      this.createForm.patchValue({ role: 'USER' });
    }
    this.loadUsers();
    this.loadAsignacionMercadistas();
  }

  /** Texto del desplegable en Asignación de Usuario: solo nombre completo (sin correo). */
  etiquetaUsuario(u: AdminUserRow): string {
    const n = (u.full_name ?? '').trim();
    if (n) return n;
    return `Usuario #${u.id}`;
  }

  etiquetaEditorFiltro(e: AdminUserRow): string {
    const n = (e.full_name ?? '').trim();
    if (n) return `${n} (${e.email})`;
    return `${e.email} · #${e.id}`;
  }

  /** Etiqueta en español para la columna Rol (la API sigue usando USER / ADMIN). */
  etiquetaRol(role: string): string {
    return etiquetaRolUtil(role);
  }

  /** True si el usuario tiene ruta (mercadista) en BD: no se puede eliminar hasta quitarla. */
  tieneRutaAsignada(u: AdminUserRow): boolean {
    const r = (u.assigned_mercadista ?? '').trim();
    return r.length > 0;
  }

  /** No permitir desactivar si sigue siendo Usuario y tiene ruta (el formulario puede cambiar el rol a Admin). */
  bloquearDesactivarPorRuta(): boolean {
    const u = this.editingUser;
    if (!u || !this.tieneRutaAsignada(u)) return false;
    const rol = this.editForm.get('role')?.value;
    return rol === 'USER';
  }

  onIsActiveChange(): void {
    if (!this.bloquearDesactivarPorRuta()) return;
    const active = this.editForm.get('is_active')?.value;
    if (!active) {
      this.editForm.patchValue({ is_active: true }, { emitEvent: false });
      this.showToast(
        'No puedes desactivar este usuario mientras tenga una ruta asignada. ' +
          'Quítale la ruta en «Asignación de Usuario» primero.',
        'error'
      );
    }
  }

  loadAsignacionMercadistas(): void {
    this.loadingAsignacion = true;
    this.adminUsers.getMercadistasAsignacion().subscribe({
      next: res => {
        this.asignacionRows = res.rows ?? [];
        this.excelContextoAsignacion = res.excel_context ?? null;
        this.loadingAsignacion = false;
      },
      error: err => {
        this.loadingAsignacion = false;
        this.excelContextoAsignacion = null;
        this.showToast(
          err?.error?.error ?? 'No se pudo cargar la lista de mercadistas',
          'error'
        );
      },
    });
  }

  descargarExcelHorariosActualizado(): void {
    this.downloadingHorariosExcel = true;
    this.adminUsers
      .downloadHorariosExcelActualizado()
      .pipe(finalize(() => (this.downloadingHorariosExcel = false)))
      .subscribe({
        next: resp => {
          const blob = resp.body as Blob;
          const filename =
            this.extractFilenameFromContentDisposition(
              resp.headers.get('Content-Disposition')
            ) ?? 'minoristas_horarios_actualizado.xlsx';
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = filename;
          a.click();
          URL.revokeObjectURL(url);
          this.showToast('Excel descargado.', 'success');
        },
        error: (err: HttpErrorResponse) => {
          const body = err.error;
          if (body instanceof Blob) {
            void body.text().then(text => {
              try {
                const j = JSON.parse(text) as { error?: string };
                this.showToast(j.error ?? 'No se pudo descargar el Excel', 'error');
              } catch {
                this.showToast('No se pudo descargar el Excel', 'error');
              }
            });
          } else {
            const e = err.error as { error?: string } | undefined;
            this.showToast(e?.error ?? 'No se pudo descargar el Excel', 'error');
          }
        },
      });
  }

  private extractFilenameFromContentDisposition(header: string | null): string | null {
    if (!header) return null;
    const utf8Match = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
    if (utf8Match?.[1]) {
      try {
        return decodeURIComponent(utf8Match[1].trim());
      } catch {
        return utf8Match[1].trim();
      }
    }
    const asciiMatch = /filename\s*=\s*"?([^";]+)"?/i.exec(header);
    if (asciiMatch?.[1]) return asciiMatch[1].trim();
    return null;
  }

  guardarAsignacionMercadista(row: MercadistaAsignacionRow): void {
    this.guardandoMercadista = row.mercadista;
    this.adminUsers
      .putMercadistaAsignacion({
        mercadista_actual: row.mercadista,
        user_id: row.user_id,
      })
      .pipe(finalize(() => (this.guardandoMercadista = null)))
      .subscribe({
        next: res => {
          if (res?.success === false) {
            this.showToast(res.error ?? 'No se pudo guardar.', 'error');
            return;
          }
          if (res.user_id == null) {
            this.showToast(`Asignación quitada para «${row.mercadista}».`, 'success');
          } else if (res.nuevo_nombre_excel) {
            this.showToast(
              `Excel actualizado: «${res.mercadista_anterior ?? row.mercadista}» → «${res.nuevo_nombre_excel}».`,
              'success'
            );
          } else {
            this.showToast('Cambios guardados.', 'success');
          }
          this.loadUsers();
          this.loadAsignacionMercadistas();
        },
        error: err => {
          this.showToast(
            err?.error?.error ??
              'Error al guardar. Comprueba que el Excel no esté abierto en otra aplicación.',
            'error'
          );
        },
      });
  }

  loadUsers(): void {
    this.loadingList = true;
    this.adminUsers.listUsers().subscribe({
      next: res => {
        this.users = res.users ?? [];
        this.loadingList = false;
        if (this.auth.isAdmin() && this.editorFiltroCreadorId != null) {
          this.cargarUsuariosCreadosPorEditor();
        }
      },
      error: err => {
        this.loadingList = false;
        this.showToast(
          err?.error?.error ?? 'No se pudo cargar la lista de usuarios',
          'error'
        );
      },
    });
  }

  /** Tabla solo admin: usuarios cuyo alta registró el editor elegido. */
  cargarUsuariosCreadosPorEditor(): void {
    if (!this.auth.isAdmin()) {
      this.usuariosCreadosPorEditor = [];
      return;
    }
    const id = this.editorFiltroCreadorId;
    if (id == null) {
      this.usuariosCreadosPorEditor = [];
      return;
    }
    this.loadingUsuariosPorEditor = true;
    this.adminUsers
      .listUsers({ createdByEditor: id })
      .pipe(finalize(() => (this.loadingUsuariosPorEditor = false)))
      .subscribe({
        next: res => {
          this.usuariosCreadosPorEditor = res.users ?? [];
        },
        error: err => {
          this.usuariosCreadosPorEditor = [];
          this.showToast(
            err?.error?.error ?? 'No se pudo cargar los usuarios de ese editor',
            'error'
          );
        },
      });
  }

  submitCreate(): void {
    if (this.createForm.invalid) {
      this.createForm.markAllAsTouched();
      this.showToast(
        'Revisa el formulario: nombre completo, un correo válido y una contraseña con ' +
          'mayúscula, minúscula, carácter especial y mínimo 8 caracteres.',
        'error'
      );
      return;
    }
    const v = this.createForm.getRawValue();
    const roleCrear = this.auth.isAdmin() ? v.role : 'USER';
    this.savingCreate = true;
    this.adminUsers
      .createUser({
        full_name: v.full_name.trim(),
        email: v.email.trim(),
        password: v.password,
        role: roleCrear as 'ADMIN' | 'USER' | 'EDITOR' | 'VISUALIZADOR',
      })
      .pipe(finalize(() => (this.savingCreate = false)))
      .subscribe({
        next: res => {
          if (res?.success === false) {
            this.showToast(res.error ?? 'No se pudo crear el usuario.', 'error');
            return;
          }
          const creadoViz = this.auth.isAdmin() && roleCrear === 'VISUALIZADOR';
          this.showToast(
            creadoViz
              ? 'Usuario visualizador creado. Asigna su Excel en «Excels / Rutas» para que vea rutas en el mapa.'
              : 'Usuario creado correctamente.',
            'success'
          );
          this.createForm.reset({ role: 'USER' });
          this.loadUsers();
          this.loadAsignacionMercadistas();
        },
        error: err => {
          const msg =
            err?.error?.error ??
            (typeof err?.error === 'string' ? err.error : null) ??
            (err.status === 0
              ? 'Sin conexión con el servidor. ¿Está la API en marcha y el proxy configurado?'
              : 'Error al crear el usuario');
          this.showToast(msg, 'error');
        },
      });
  }

  openEdit(u: AdminUserRow): void {
    this.editingUser = u;
    const r =
      u.role === 'ADMIN' ||
      u.role === 'USER' ||
      u.role === 'EDITOR' ||
      u.role === 'VISUALIZADOR'
        ? u.role
        : 'USER';
    this.editForm.patchValue({
      full_name: u.full_name ?? '',
      email: u.email,
      password: '',
      role: r as 'ADMIN' | 'USER' | 'EDITOR' | 'VISUALIZADOR',
      is_active: u.is_active,
    });
  }

  closeEdit(): void {
    this.editingUser = null;
  }

  submitEdit(): void {
    if (!this.editingUser || this.editForm.invalid) {
      this.editForm.markAllAsTouched();
      return;
    }
    const v = this.editForm.getRawValue();
    const pwd = (v.password as string)?.trim();
    if (pwd && !this.passwordPattern.test(pwd)) {
      this.showToast(
        'La nueva contraseña debe incluir mayúscula, minúscula, carácter especial y mínimo 8 caracteres.',
        'error'
      );
      return;
    }
    if (
      !v.is_active &&
      this.editingUser &&
      this.tieneRutaAsignada(this.editingUser) &&
      v.role === 'USER'
    ) {
      this.showToast(
        'No puedes desactivar este usuario mientras tenga una ruta asignada. ' +
          'Quítale la ruta en «Asignación de Usuario» primero.',
        'error'
      );
      return;
    }
    const body: Record<string, unknown> = {
      full_name: v.full_name.trim(),
      email: v.email.trim(),
      is_active: v.is_active,
    };
    if (!this.esVistaEditor) {
      body['role'] = v.role as string;
    }
    if (pwd) {
      body['password'] = pwd;
    }

    const pasoARolVisualizador =
      this.auth.isAdmin() &&
      this.editingUser.role !== 'VISUALIZADOR' &&
      v.role === 'VISUALIZADOR';

    this.savingEdit = true;
    this.adminUsers.updateUser(this.editingUser.id, body).subscribe({
      next: () => {
        this.savingEdit = false;
        this.showToast(
          pasoARolVisualizador
            ? 'Rol actualizado a visualizador. Asigna el Excel en «Excels / Rutas» antes de que vea datos en el mapa.'
            : 'Usuario actualizado.',
          'success'
        );
        this.closeEdit();
        this.loadUsers();
        this.loadAsignacionMercadistas();
      },
      error: err => {
        this.savingEdit = false;
        this.showToast(err?.error?.error ?? 'Error al actualizar el usuario', 'error');
      },
    });
  }

  /** Abre el modal flotante de confirmación (sustituye al confirm() del navegador). */
  openDeleteConfirm(u: AdminUserRow): void {
    if (this.tieneRutaAsignada(u)) {
      this.showToast(
        'No puedes eliminar este usuario mientras tenga una ruta asignada. ' +
          'Quítale la ruta en «Asignación de Usuario» primero.',
        'error'
      );
      return;
    }
    this.pendingDelete = u;
  }

  closeDeleteConfirm(): void {
    this.pendingDelete = null;
  }

  confirmDeleteExecute(): void {
    const u = this.pendingDelete;
    if (!u) return;
    this.deletingId = u.id;
    this.pendingDelete = null;
    this.adminUsers.deleteUser(u.id).subscribe({
      next: () => {
        this.deletingId = null;
        this.showToast('Usuario eliminado.', 'success');
        if (this.editingUser?.id === u.id) {
          this.closeEdit();
        }
        this.loadUsers();
        this.loadAsignacionMercadistas();
      },
      error: err => {
        this.deletingId = null;
        this.showToast(err?.error?.error ?? 'Error al eliminar el usuario', 'error');
      },
    });
  }
}
