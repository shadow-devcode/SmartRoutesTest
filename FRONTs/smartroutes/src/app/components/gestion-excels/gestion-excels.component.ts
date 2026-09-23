import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { finalize } from 'rxjs/operators';
import { AdminUsersService } from '../../services/admin-users.service';
import { ExcelProcesamientoService } from '../../services/excel-procesamiento.service';
import { descargarBlob, nombreDesdeContentDisposition } from '../../utils/archivos';
import { PuntosSinCoordenadasApiService } from '../../services/api/puntos-sin-coordenadas-api.service';
import type { AdminUserRow } from '../../models/admin-user.model';
import type { RouteDatasetRow } from '../../models/route-dataset.model';
import type { PuntoSinCoordenada } from '../../models/puntos-sin-coordenadas.model';
import { etiquetaRolStaff as etiquetaRolStaffUtil } from '../../utils/roles';

/** Fila: editor/visualizador y qué Excel (dataset) debe ver en mapa/dashboard/comparativa. */
export interface StaffExcelAssignRow {
  userId: number;
  full_name: string | null;
  email: string;
  role: 'EDITOR' | 'VISUALIZADOR';
  savedDatasetId: number | null;
  draftDatasetId: number | null;
  /** ID en BD que ya no está en la lista de datasets (p. ej. eliminado). */
  orphanDatasetId: number | null;
}

@Component({
  selector: 'app-gestion-excels',
  standalone: true,
  imports: [CommonModule, RouterLink, FormsModule],
  templateUrl: './gestion-excels.component.html',
  styleUrl: './gestion-excels.component.css',
})
export class GestionExcelsComponent implements OnInit {
  datasets: RouteDatasetRow[] = [];
  staffRows: StaffExcelAssignRow[] = [];
  loading = false;
  activatingId: number | null = null;
  deletingId: number | null = null;
  descargandoId: number | null = null;
  savingStaffUserId: number | null = null;
  errorMsg = '';
  successMsg = '';

  puntosSinCoords: PuntoSinCoordenada[] = [];
  loadingPuntos = false;
  editandoPunto: PuntoSinCoordenada | null = null;
  editLat = '';
  editLon = '';
  guardandoPunto = false;
  puntosError = '';
  puntosSuccess = '';

  /**
   * Excel cuyo borrado está esperando confirmación.
   *
   * La pregunta se dibuja como el resto de los cuadros de esta página. El
   * `window.confirm` del navegador la sacaba fuera de la app, encabezada por la
   * URL del servidor y sin poder destacar que el borrado no tiene vuelta atrás.
   */
  aEliminar: RouteDatasetRow | null = null;
  aRenombrar: RouteDatasetRow | null = null;
  nombreNuevo = '';
  renamingId: number | null = null;

  constructor(
    private readonly adminUsers: AdminUsersService,
    private readonly puntosApi: PuntosSinCoordenadasApiService,
    private readonly excelProcesamiento: ExcelProcesamientoService,
  ) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading = true;
    this.errorMsg = '';
    forkJoin({
      ds: this.adminUsers.listRouteDatasets(),
      users: this.adminUsers.listUsers(),
    })
      .pipe(finalize(() => (this.loading = false)))
      .subscribe({
        next: ({ ds, users }) => {
          this.datasets = ds.datasets ?? [];
          this.rebuildStaffRows(users.users ?? []);
          this.loadPuntosSinCoordenadas();
        },
        error: (err) => {
          this.errorMsg =
            err?.error?.error ?? 'No se pudo cargar los datos. ¿Está la API en marcha?';
        },
      });
  }

  private rebuildStaffRows(users: AdminUserRow[]): void {
    const ids = new Set(this.datasets.map((d) => d.id));
    this.staffRows = users
      .filter((u): u is AdminUserRow & { role: 'EDITOR' | 'VISUALIZADOR' } =>
        u.role === 'EDITOR' || u.role === 'VISUALIZADOR'
      )
      .map((u) => {
        const sid = u.assigned_route_dataset_id ?? null;
        const orphan = sid != null && !ids.has(sid) ? sid : null;
        return {
          userId: u.id,
          full_name: u.full_name,
          email: u.email,
          role: u.role as 'EDITOR' | 'VISUALIZADOR',
          savedDatasetId: sid,
          draftDatasetId: sid,
          orphanDatasetId: orphan,
        };
      });
  }

  etiquetaRolStaff(role: string): string {
    return etiquetaRolStaffUtil(role);
  }

  filaStaffTieneCambios(row: StaffExcelAssignRow): boolean {
    return row.draftDatasetId !== row.savedDatasetId;
  }

  guardarExcelStaff(row: StaffExcelAssignRow): void {
    if (!this.filaStaffTieneCambios(row)) return;
    this.successMsg = '';
    this.errorMsg = '';
    this.savingStaffUserId = row.userId;
    this.adminUsers
      .updateUser(row.userId, { assigned_route_dataset_id: row.draftDatasetId })
      .pipe(finalize(() => (this.savingStaffUserId = null)))
      .subscribe({
        next: (res) => {
          if (res.success === false) {
            this.errorMsg = res.error ?? 'No se pudo guardar la asignación.';
            return;
          }
          row.savedDatasetId = row.draftDatasetId;
          const ids = new Set(this.datasets.map((d) => d.id));
          const sid = row.savedDatasetId;
          row.orphanDatasetId =
            sid != null && !ids.has(sid) ? sid : null;
          this.successMsg = `Excel actualizado para ${row.email}.`;
        },
        error: (err) => {
          this.errorMsg = err?.error?.error ?? 'Error al guardar la asignación.';
        },
      });
  }

  activar(row: RouteDatasetRow): void {
    if (row.is_active || !row.horarios_exists) return;
    this.successMsg = '';
    this.errorMsg = '';
    this.activatingId = row.id;
    this.adminUsers
      .activateRouteDataset(row.id)
      .pipe(finalize(() => (this.activatingId = null)))
      .subscribe({
        next: (res) => {
          if (res.success === false) {
            this.errorMsg = res.error ?? 'No se pudo activar el dataset.';
            return;
          }
          this.successMsg = `Visualización activa: «${row.display_name}». El mapa usará estos datos.`;
          this.load();
        },
        error: (err) => {
          this.errorMsg = err?.error?.error ?? 'Error al activar el Excel seleccionado.';
        },
      });
  }

  /**
   * Descarga el Excel de ese rutero sin tener que activarlo antes.
   *
   * Va por XHR y no por un enlace: el backend exige el JWT y un `<a href>` no
   * lo manda, así que respondería 401. Por eso el nombre del archivo se saca de
   * la cabecera y la descarga se dispara desde aquí.
   */
  descargar(row: RouteDatasetRow): void {
    if (!row.horarios_exists || this.descargandoId !== null) return;
    this.successMsg = '';
    this.errorMsg = '';
    this.descargandoId = row.id;
    this.excelProcesamiento
      .descargarResultadoExcel(row.id)
      .pipe(finalize(() => (this.descargandoId = null)))
      .subscribe({
        next: (resp) => {
          const blob = resp.body;
          if (!blob) {
            this.errorMsg = 'El servidor no devolvió el archivo.';
            return;
          }
          const nombre =
            nombreDesdeContentDisposition(resp.headers.get('Content-Disposition')) ??
            `${row.display_name || 'rutas_generadas'}.xlsx`;
          descargarBlob(blob, nombre);
        },
        error: (err) => {
          // El error llega como Blob porque la petición pide responseType blob.
          const cuerpo = err?.error;
          if (cuerpo instanceof Blob) {
            void cuerpo.text().then((texto) => {
              try {
                this.errorMsg =
                  (JSON.parse(texto) as { error?: string }).error ??
                  'No se pudo descargar el Excel.';
              } catch {
                this.errorMsg = 'No se pudo descargar el Excel.';
              }
            });
            return;
          }
          this.errorMsg = cuerpo?.error ?? 'No se pudo descargar el Excel.';
        },
      });
  }

  abrirRenombrar(row: RouteDatasetRow): void {
    this.aRenombrar = row;
    this.nombreNuevo = row.display_name;
  }

  cancelarRenombrar(): void {
    this.aRenombrar = null;
    this.nombreNuevo = '';
  }

  confirmarRenombrar(): void {
    const row = this.aRenombrar;
    const nombre = this.nombreNuevo.trim();
    if (!row || !nombre || nombre === row.display_name) {
      this.cancelarRenombrar();
      return;
    }
    this.aRenombrar = null;
    this.successMsg = '';
    this.errorMsg = '';
    this.renamingId = row.id;
    this.adminUsers
      .renameRouteDataset(row.id, nombre)
      .pipe(finalize(() => (this.renamingId = null)))
      .subscribe({
        next: (res) => {
          if (res.success === false) {
            this.errorMsg = res.error ?? 'No se pudo cambiar el nombre.';
            return;
          }
          this.successMsg = `«${row.display_name}» ahora se llama «${nombre}».`;
          this.nombreNuevo = '';
          this.load();
        },
        error: (err) => {
          this.errorMsg = err?.error?.error ?? 'No se pudo cambiar el nombre.';
        },
      });
  }

  confirmarEliminar(row: RouteDatasetRow): void {
    if (this.datasets.length <= 1) {
      this.errorMsg = 'Debe existir al menos un Excel en el sistema.';
      return;
    }
    this.aEliminar = row;
  }

  cancelarEliminar(): void {
    this.aEliminar = null;
  }

  confirmarBorrado(): void {
    const row = this.aEliminar;
    this.aEliminar = null;
    if (row) this.eliminar(row);
  }

  loadPuntosSinCoordenadas(): void {
    this.loadingPuntos = true;
    this.puntosError = '';
    this.puntosApi
      .getPuntosSinCoordenadas()
      .pipe(finalize(() => (this.loadingPuntos = false)))
      .subscribe({
        next: (res) => {
          this.puntosSinCoords = res.puntos;
          if (res.error) this.puntosError = res.error;
        },
      });
  }

  abrirEditar(punto: PuntoSinCoordenada): void {
    this.editandoPunto = punto;
    this.editLat = punto.latitud?.toString() ?? '';
    this.editLon = punto.longitud?.toString() ?? '';
    this.puntosError = '';
    this.puntosSuccess = '';
  }

  cerrarModal(): void {
    this.editandoPunto = null;
    this.editLat = '';
    this.editLon = '';
    this.puntosError = '';
  }

  guardarCoordenadas(): void {
    const lat = parseFloat(this.editLat);
    const lon = parseFloat(this.editLon);

    if (isNaN(lat) || isNaN(lon)) {
      this.puntosError = 'Ingresa valores numéricos válidos para latitud y longitud.';
      return;
    }
    if (lat < -90 || lat > 90) {
      this.puntosError = `Latitud ${lat} fuera del rango válido (-90 a 90).`;
      return;
    }
    if (lon < -180 || lon > 180) {
      this.puntosError = `Longitud ${lon} fuera del rango válido (-180 a 180).`;
      return;
    }
    if (lat === 0 && lon === 0) {
      this.puntosError = 'Las coordenadas no pueden ser ambas 0.';
      return;
    }

    const desc = this.editandoPunto!.descripcion;
    this.guardandoPunto = true;
    this.puntosError = '';

    this.puntosApi
      .actualizarCoordenadas(desc, lat, lon)
      .pipe(finalize(() => (this.guardandoPunto = false)))
      .subscribe({
        next: (res) => {
          if (!res.success) {
            this.puntosError = res.error ?? 'No se pudo actualizar el punto.';
            return;
          }
          this.puntosSinCoords = this.puntosSinCoords.filter(
            (p) => p.descripcion !== desc,
          );
          this.puntosSuccess = `«${desc}» movido a Pendientes${res.provincia ? ' · ' + res.provincia : ''}${res.ciudad ? ', ' + res.ciudad : ''}.`;
          this.cerrarModal();
        },
        error: (err) => {
          this.puntosError = err?.error?.error ?? 'Error al guardar las coordenadas.';
        },
      });
  }

  private eliminar(row: RouteDatasetRow): void {
    this.successMsg = '';
    this.errorMsg = '';
    this.deletingId = row.id;
    this.adminUsers
      .deleteRouteDataset(row.id)
      .pipe(finalize(() => (this.deletingId = null)))
      .subscribe({
        next: (res) => {
          if (res.success === false) {
            this.errorMsg = res.error ?? 'No se pudo eliminar.';
            return;
          }
          this.successMsg = `Se eliminó «${row.display_name}».`;
          this.load();
        },
        error: (err) => {
          this.errorMsg = err?.error?.error ?? 'Error al eliminar el dataset.';
        },
      });
  }
}
