import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, catchError, map, of } from 'rxjs';

import { environment } from '../../../environments/environment';
import {
  Estadisticas,
  MercadistaDetalle,
  UbicacionMapa,
} from '../../models/mercadista.model';

/**
 * Endpoints del dataset de comparativa:
 * /comparativa/{mercadistas,mercadista/<>,todas-ubicaciones,dia/<>,stats,upload-excel}.
 *
 * Mismo contrato que MercadistasApiService pero contra el Excel comparativo
 * (sin filtros por rol USER en el backend).
 */
@Injectable({ providedIn: 'root' })
export class ComparativaApiService {
  private readonly apiUrl = environment.apiUrl;

  constructor(private readonly http: HttpClient) {}

  getMercadistas(): Observable<string[]> {
    return this.http
      .get<{ success: boolean; mercadistas: string[] }>(
        `${this.apiUrl}/comparativa/mercadistas`,
      )
      .pipe(
        map((response) => response.mercadistas || []),
        catchError((error) => {
          console.error('Error al obtener mercadistas comparativa:', error);
          return of([]);
        }),
      );
  }

  getMercadistaDetalle(
    nombre: string,
    semana?: string,
  ): Observable<MercadistaDetalle | null> {
    let url = `${this.apiUrl}/comparativa/mercadista/${encodeURIComponent(nombre)}`;
    if (semana?.trim()) {
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
          console.error('Error al obtener detalle del mercadista comparativa:', error);
          return of(null);
        }),
      );
  }

  getTodasUbicaciones(semana?: string): Observable<UbicacionMapa[]> {
    let url = `${this.apiUrl}/comparativa/todas-ubicaciones`;
    if (semana?.trim()) {
      url += `?semana=${encodeURIComponent(semana.trim())}`;
    }
    return this.http
      .get<{ success: boolean; ubicaciones: UbicacionMapa[] }>(url)
      .pipe(
        map((response) => response.ubicaciones || []),
        catchError((error) => {
          console.error('Error al obtener ubicaciones comparativa:', error);
          return of([]);
        }),
      );
  }

  getEstadisticas(): Observable<Estadisticas | null> {
    return this.http
      .get<{ success: boolean; stats: Estadisticas }>(
        `${this.apiUrl}/comparativa/stats`,
      )
      .pipe(
        map((response) => response.stats),
        catchError((error) => {
          console.error('Error al obtener estadísticas comparativa:', error);
          return of(null);
        }),
      );
  }

  /**
   * Sube un Excel ya procesado y lo guarda directamente como archivo
   * comparativo del dataset activo (sin reprocesarlo).
   */
  uploadExcel(
    file: File,
  ): Observable<{ success: boolean; message?: string; error?: string }> {
    const formData = new FormData();
    formData.append('file', file);
    return this.http
      .post<{ success: boolean; message?: string; error?: string }>(
        `${this.apiUrl}/comparativa/upload-excel`,
        formData,
      )
      .pipe(
        catchError((error) =>
          of({
            success: false,
            error:
              error.error?.error ?? 'Error al subir el archivo comparativo',
          }),
        ),
      );
  }
}
