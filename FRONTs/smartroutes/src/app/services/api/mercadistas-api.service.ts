import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, catchError, map, of } from 'rxjs';

import { environment } from '../../../environments/environment';
import {
  Estadisticas,
  MercadistaDetalle,
  UbicacionMapa,
} from '../../models/mercadista.model';
import { RutasAsignadasResponse } from '../../models/ruta-asignada.model';

/** Respuesta tipada de GET /mercadistas. */
export interface MercadistasListaPayload {
  mercadistas: string[];
  provincias: string[];
  mercadistaProvincias: Record<string, string[]>;
}

/**
 * Endpoints de consulta del dataset principal:
 * /mercadistas, /mercadista/<>, /todas-ubicaciones, /dia/<>, /stats.
 *
 * Mantiene `catchError` con fallback vacío para que los componentes no rompan
 * si el backend devuelve error (estado idéntico al api.service.ts original).
 */
@Injectable({ providedIn: 'root' })
export class MercadistasApiService {
  private readonly apiUrl = environment.apiUrl;

  constructor(private readonly http: HttpClient) {}

  /**
   * Obtiene mercadistas y, si existe la columna PROVINCIA, el mapa
   * mercadista → provincias de visita.
   */
  getMercadistas(): Observable<MercadistasListaPayload> {
    return this.http
      .get<{
        success: boolean;
        mercadistas?: string[];
        provincias?: string[];
        mercadista_provincias?: Record<string, string[]>;
      }>(`${this.apiUrl}/mercadistas`)
      .pipe(
        map((response) => ({
          mercadistas: response.mercadistas ?? [],
          provincias: response.provincias ?? [],
          mercadistaProvincias: response.mercadista_provincias ?? {},
        })),
        catchError((error) => {
          console.error('Error al obtener mercadistas:', error);
          return of({ mercadistas: [], provincias: [], mercadistaProvincias: {} });
        }),
      );
  }

  /**
   * Detalle de un mercadista. Si `semana` viene, filtra por columna Fecha.
   */
  getMercadistaDetalle(
    nombre: string,
    semana?: string,
  ): Observable<MercadistaDetalle | null> {
    let url = `${this.apiUrl}/mercadista/${encodeURIComponent(nombre)}`;
    if (semana && semana.trim()) {
      url += `?semana=${encodeURIComponent(semana.trim())}`;
    }
    return this.http
      .get<{ success: boolean; mercadista: string; dias: any }>(url)
      .pipe(
        map((response) => ({
          mercadista: response.mercadista,
          dias: response.dias,
        })),
        catchError((error) => {
          console.error('Error al obtener detalle del mercadista:', error);
          return of(null);
        }),
      );
  }

  getTodasUbicaciones(semana?: string): Observable<UbicacionMapa[]> {
    let url = `${this.apiUrl}/todas-ubicaciones`;
    if (semana && semana.trim()) {
      url += `?semana=${encodeURIComponent(semana.trim())}`;
    }
    return this.http
      .get<{ success: boolean; ubicaciones: UbicacionMapa[] }>(url)
      .pipe(
        map((response) => response.ubicaciones || []),
        catchError((error) => {
          console.error('Error al obtener ubicaciones:', error);
          return of([]);
        }),
      );
  }

  /** Grupos de cadenas del dataset (reparto multicadena) y el grupo de cada mercaderista. */
  getGruposMercadistas(fuente?: string): Observable<GruposMercadistas> {
    const qs = fuente ? `?fuente=${encodeURIComponent(fuente)}` : '';
    return this.http.get<GruposMercadistas>(`${this.apiUrl}/grupos-mercadistas${qs}`).pipe(
      catchError(() => of({ success: false, grupos: [], grupo_por_mercadista: {}, grupo_por_punto: {} })),
    );
  }

  /** `fuente` = 'comparativa' devuelve las mismas cifras sobre el rutero armado a mano. */
  getEstadisticas(fuente?: string): Observable<Estadisticas | null> {
    const qs = fuente ? `?fuente=${encodeURIComponent(fuente)}` : '';
    return this.http
      .get<{ success: boolean; stats: Estadisticas }>(`${this.apiUrl}/stats${qs}`)
      .pipe(
        map((response) => response.stats),
        catchError((error) => {
          console.error('Error al obtener estadísticas:', error);
          return of(null);
        }),
      );
  }

  /**
   * Rutas ya asignadas, agrupadas por punto de venta (lista + mapa de la
   * pantalla de gestión de rutas).
   */
  getRutasAsignadas(opts?: {
    mercadista?: string;
    provincia?: string;
    ciudad?: string;
  }): Observable<RutasAsignadasResponse> {
    const params: string[] = [];
    if (opts?.mercadista?.trim()) {
      params.push(`mercadista=${encodeURIComponent(opts.mercadista.trim())}`);
    }
    if (opts?.provincia?.trim()) {
      params.push(`provincia=${encodeURIComponent(opts.provincia.trim())}`);
    }
    if (opts?.ciudad?.trim()) {
      params.push(`ciudad=${encodeURIComponent(opts.ciudad.trim())}`);
    }
    const qs = params.length ? `?${params.join('&')}` : '';
    return this.http
      .get<RutasAsignadasResponse>(`${this.apiUrl}/rutas-asignadas${qs}`)
      .pipe(
        catchError((error) => {
          console.error('Error al obtener las rutas asignadas:', error);
          return of({
            success: false,
            filas: [],
            total_filas: 0,
            puntos: [],
            total_puntos: 0,
            total_visitas: 0,
            minutos_asignados: 0,
            mercadistas: [],
            error:
              error?.error?.error ??
              'No se pudieron cargar las rutas. Revisa la conexión con el servidor.',
          } as RutasAsignadasResponse);
        }),
      );
  }
}

export interface GrupoMercadistas {
  nombre: string;
  cadenas: string[];
  mercadistas: string[];
}

export interface GruposMercadistas {
  success: boolean;
  tipo_carga?: string;
  grupos: GrupoMercadistas[];
  grupo_por_mercadista: Record<string, string>;
  /** Grupo de cada punto (por descripción en mayúsculas), para no mezclar pendientes. */
  grupo_por_punto?: Record<string, string>;
}
