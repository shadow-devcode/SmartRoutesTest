import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, catchError, map, of } from 'rxjs';

import { environment } from '../../../environments/environment';
import { VisitaPendiente } from '../../models/mercadista.model';
import { ResumenPendientesResponse } from '../../models/pendiente-gestion.model';

/** Endpoint /pendientes (lista de visitas sin asignar). */
@Injectable({ providedIn: 'root' })
export class PendientesApiService {
  private readonly apiUrl = environment.apiUrl;

  constructor(private readonly http: HttpClient) {}

  /**
   * Visitas que el motor no pudo asignar (hoja "Pendientes_Sin_Asignar").
   * Filtros opcionales: semana, provincia.
   */
  getPendientes(opts?: {
    semana?: string;
    provincia?: string;
  }): Observable<VisitaPendiente[]> {
    const params: string[] = [];
    if (opts?.semana?.trim()) {
      params.push(`semana=${encodeURIComponent(opts.semana.trim())}`);
    }
    if (opts?.provincia?.trim()) {
      params.push(`provincia=${encodeURIComponent(opts.provincia.trim())}`);
    }
    const qs = params.length ? `?${params.join('&')}` : '';
    return this.http
      .get<{ success: boolean; pendientes: VisitaPendiente[] }>(
        `${this.apiUrl}/pendientes${qs}`,
      )
      .pipe(
        map((response) => response.pendientes || []),
        catchError((error) => {
          console.error('Error al obtener pendientes:', error);
          return of([]);
        }),
      );
  }

  /**
   * Pendientes agrupados por punto de venta, para la pantalla de gestión.
   * Trae frecuencia mensual y los días en los que el punto ya se visita.
   */
  getResumen(opts?: {
    provincia?: string;
    ciudad?: string;
  }): Observable<ResumenPendientesResponse> {
    const params: string[] = [];
    if (opts?.provincia?.trim()) {
      params.push(`provincia=${encodeURIComponent(opts.provincia.trim())}`);
    }
    if (opts?.ciudad?.trim()) {
      params.push(`ciudad=${encodeURIComponent(opts.ciudad.trim())}`);
    }
    const qs = params.length ? `?${params.join('&')}` : '';
    return this.http
      .get<ResumenPendientesResponse>(`${this.apiUrl}/pendientes/resumen${qs}`)
      .pipe(
        catchError((error) => {
          console.error('Error al obtener el resumen de pendientes:', error);
          return of({
            success: false,
            puntos: [],
            total_puntos: 0,
            total_visitas_pendientes: 0,
            minutos_pendientes: 0,
            error:
              error?.error?.error ??
              'No se pudo cargar la lista de pendientes. Revisa la conexión con el servidor.',
          } as ResumenPendientesResponse);
        }),
      );
  }
}
