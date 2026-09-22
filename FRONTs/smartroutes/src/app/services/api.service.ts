import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import {
  AsignarPendienteResponse,
  Estadisticas,
  FrecuenciaPunto,
  MercadistaDetalle,
  ProvinciaPorcentaje,
  UbicacionMapa,
  VisitaPendiente,
} from '../models/mercadista.model';
import {
  ComparativaApiService,
  DashboardApiService,
  MercadistasApiService,
  PendientesApiService,
  RutaEditApiService,
} from './api/index';

// Re-export para no romper imports existentes:
//   import { MercadistasListaPayload } from '../../services/api.service';
export type { MercadistasListaPayload } from './api/mercadistas-api.service';

/**
 * Fachada de back-compat. Los componentes que ya inyectan `ApiService` siguen
 * funcionando sin cambios; cada método delega al service de dominio
 * correspondiente bajo `services/api/`.
 *
 * En código nuevo inyecta directamente el service de dominio:
 *   `MercadistasApiService`, `DashboardApiService`, `RutaEditApiService`,
 *   `PendientesApiService`, `ComparativaApiService`
 *   (o `ExcelProcesamientoService` para el flujo de carga/procesamiento).
 *
 * @deprecated Inyecta los services de dominio en lugar de esta fachada.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  constructor(
    private readonly mercadistas: MercadistasApiService,
    private readonly dashboard: DashboardApiService,
    private readonly rutaEdit: RutaEditApiService,
    private readonly pendientes: PendientesApiService,
    private readonly comparativa: ComparativaApiService,
  ) {}

  // ─── Mercadistas / ubicaciones / stats ─────────────────────────────────────

  getMercadistas() {
    return this.mercadistas.getMercadistas();
  }

  getMercadistaDetalle(
    nombre: string,
    semana?: string,
  ): Observable<MercadistaDetalle | null> {
    return this.mercadistas.getMercadistaDetalle(nombre, semana);
  }

  getTodasUbicaciones(semana?: string): Observable<UbicacionMapa[]> {
    return this.mercadistas.getTodasUbicaciones(semana);
  }

  getEstadisticas(fuente?: string): Observable<Estadisticas | null> {
    return this.mercadistas.getEstadisticas(fuente);
  }

  getGruposMercadistas(fuente?: string) {
    return this.mercadistas.getGruposMercadistas(fuente);
  }

  // ─── Dashboard ─────────────────────────────────────────────────────────────

  getFrecuenciaPuntos(
    mercadista?: string,
    fuente?: string,
  ): Observable<{ frecuencia_puntos: FrecuenciaPunto[]; mercadistas: string[] }> {
    return this.dashboard.getFrecuenciaPuntos(mercadista, fuente);
  }

  getProvinciasPorcentaje(mercadista?: string, provincia?: string, fuente?: string) {
    return this.dashboard.getProvinciasPorcentaje(mercadista, provincia, fuente);
  }

  unirMercadistas(
    mercadistaOrigen: string,
    mercadistaDestino: string,
    forzar: boolean = false,
  ) {
    return this.dashboard.unirMercadistas(mercadistaOrigen, mercadistaDestino, forzar);
  }

  // ─── Edición de rutas ──────────────────────────────────────────────────────

  actualizarOrdenRuta(
    mercadista: string,
    semana: string,
    dia: string,
    ubicaciones: {
      orden: number;
      descripcion: string;
      latitud: number;
      longitud: number;
    }[],
  ) {
    return this.rutaEdit.actualizarOrdenRuta(mercadista, semana, dia, ubicaciones);
  }

  moverAPendientes(
    semana: string,
    mercadistaOrigen: string,
    diaOrigen: string,
    visita: {
      descripcion: string;
      latitud: string | number;
      longitud: string | number;
    },
    todasLasVisitas: boolean = false,
  ) {
    return this.rutaEdit.moverAPendientes(
      semana,
      mercadistaOrigen,
      diaOrigen,
      visita,
      todasLasVisitas,
    );
  }

  moverVisita(
    semana: string,
    mercadistaOrigen: string,
    diaOrigen: string,
    mercadistaDestino: string,
    diaDestino: string,
    ordenDestino: number,
    visita: {
      descripcion: string;
      latitud: string | number;
      longitud: string | number;
    },
    semanaDestino?: string,
  ) {
    return this.rutaEdit.moverVisita(
      semana,
      mercadistaOrigen,
      diaOrigen,
      mercadistaDestino,
      diaDestino,
      ordenDestino,
      visita,
      semanaDestino,
    );
  }

  asignarPendiente(
    visita: VisitaPendiente,
    mercadistaDestino: string,
    diaDestino: string,
    semanaDestino: string,
    ordenDestino: number,
    forzar: boolean = false,
  ): Observable<AsignarPendienteResponse> {
    return this.rutaEdit.asignarPendiente(
      visita,
      mercadistaDestino,
      diaDestino,
      semanaDestino,
      ordenDestino,
      forzar,
    );
  }

  // ─── Pendientes ────────────────────────────────────────────────────────────

  getPendientes(opts?: { semana?: string; provincia?: string }) {
    return this.pendientes.getPendientes(opts);
  }

  // ─── Comparativa ───────────────────────────────────────────────────────────

  getMercadistasComparativa(): Observable<string[]> {
    return this.comparativa.getMercadistas();
  }

  getMercadistaDetalleComparativa(
    nombre: string,
    semana?: string,
  ): Observable<MercadistaDetalle | null> {
    return this.comparativa.getMercadistaDetalle(nombre, semana);
  }

  getTodasUbicacionesComparativa(semana?: string): Observable<UbicacionMapa[]> {
    return this.comparativa.getTodasUbicaciones(semana);
  }

  getEstadisticasComparativa(): Observable<Estadisticas | null> {
    return this.comparativa.getEstadisticas();
  }

  uploadExcelComparativa(file: File) {
    return this.comparativa.uploadExcel(file);
  }

  /** Plantilla semanal del cliente: el servidor la convierte antes de guardarla. */
  uploadPlantillaComparativa(file: File) {
    return this.comparativa.uploadPlantilla(file);
  }
}
