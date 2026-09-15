import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, catchError, map, of } from 'rxjs';

import { environment } from '../../../environments/environment';
import {
  FrecuenciaPunto,
  ProvinciaPorcentaje,
  UnirMercadistasResponse,
} from '../../models/mercadista.model';

/**
 * Endpoints del dashboard:
 * /dashboard/frecuencia-puntos, /dashboard/provincias-porcentaje.
 */
@Injectable({ providedIn: 'root' })
export class DashboardApiService {
  private readonly apiUrl = environment.apiUrl;

  constructor(private readonly http: HttpClient) {}

  /**
   * Frecuencia de puntos por mercadista. Filtro opcional por mercadista
   * (parcial o exacto).
   */
  getFrecuenciaPuntos(
    mercadista?: string,
  ): Observable<{ frecuencia_puntos: FrecuenciaPunto[]; mercadistas: string[] }> {
    let url = `${this.apiUrl}/dashboard/frecuencia-puntos`;
    if (mercadista?.trim()) {
      url += `?mercadista=${encodeURIComponent(mercadista.trim())}`;
    }
    return this.http
      .get<{
        success: boolean;
        frecuencia_puntos: FrecuenciaPunto[];
        mercadistas: string[];
      }>(url)
      .pipe(
        map((res) => ({
          frecuencia_puntos: res.frecuencia_puntos || [],
          mercadistas: res.mercadistas || [],
        })),
        catchError(() => of({ frecuencia_puntos: [], mercadistas: [] })),
      );
  }

  /**
   * Provincias por mercadista con % del total (9960 min = 100%).
   * Filtros opcionales: mercadista, provincia.
   */
  getProvinciasPorcentaje(
    mercadista?: string,
    provincia?: string,
  ): Observable<{
    provincias_porcentaje: ProvinciaPorcentaje[];
    mercadistas: string[];
    provincias: string[];
  }> {
    const params = new URLSearchParams();
    if (mercadista?.trim()) params.set('mercadista', mercadista.trim());
    if (provincia?.trim()) params.set('provincia', provincia.trim());
    const qs = params.toString();
    const url = `${this.apiUrl}/dashboard/provincias-porcentaje${qs ? '?' + qs : ''}`;
    return this.http
      .get<{
        success: boolean;
        provincias_porcentaje: ProvinciaPorcentaje[];
        mercadistas: string[];
        provincias: string[];
      }>(url)
      .pipe(
        map((res) => ({
          provincias_porcentaje: res.provincias_porcentaje || [],
          mercadistas: res.mercadistas || [],
          provincias: res.provincias || [],
        })),
        catchError(() =>
          of({ provincias_porcentaje: [], mercadistas: [], provincias: [] }),
        ),
      );
  }

  /**
   * Une las rutas de mercadistaOrigen en mercadistaDestino.
   *
   * Si la unión rompe las reglas de rutas (dispersión > 60 km o carga mensual
   * > 9960 min combinados) el backend responde 409 con `union_invalida: true`;
   * el error NO se captura aquí a propósito, para que el componente pueda leer
   * el detalle del HttpErrorResponse y ofrecer reintentar con `forzar = true`.
   */
  unirMercadistas(
    mercadistaOrigen: string,
    mercadistaDestino: string,
    forzar: boolean = false,
  ): Observable<UnirMercadistasResponse> {
    const url = `${this.apiUrl}/dashboard/unir-mercadistas`;
    return this.http.post<UnirMercadistasResponse>(url, {
      mercadista_origen: mercadistaOrigen,
      mercadista_destino: mercadistaDestino,
      forzar,
    });
  }
}
