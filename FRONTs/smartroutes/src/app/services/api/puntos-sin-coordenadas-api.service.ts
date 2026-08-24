import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, catchError, map, of } from 'rxjs';

import { environment } from '../../../environments/environment';
import type {
  ActualizarCoordenadasResponse,
  PuntoSinCoordenada,
} from '../../models/puntos-sin-coordenadas.model';

@Injectable({ providedIn: 'root' })
export class PuntosSinCoordenadasApiService {
  private readonly apiUrl = environment.apiUrl;

  constructor(private readonly http: HttpClient) {}

  getPuntosSinCoordenadas(): Observable<{ puntos: PuntoSinCoordenada[]; error?: string }> {
    return this.http
      .get<{ success: boolean; puntos: PuntoSinCoordenada[]; error?: string }>(
        `${this.apiUrl}/puntos-sin-coordenadas`,
      )
      .pipe(
        map((res) => ({ puntos: res.puntos ?? [] })),
        catchError((err) => {
          console.error('Error al obtener puntos sin coordenadas:', err);
          const msg = err?.error?.error ?? `Error ${err?.status ?? ''}: no se pudo cargar los puntos sin coordenadas.`;
          return of({ puntos: [] as PuntoSinCoordenada[], error: msg });
        }),
      );
  }

  actualizarCoordenadas(
    descripcion: string,
    latitud: number,
    longitud: number,
  ): Observable<ActualizarCoordenadasResponse> {
    return this.http.put<ActualizarCoordenadasResponse>(
      `${this.apiUrl}/puntos-sin-coordenadas/actualizar-coordenadas`,
      { descripcion, latitud, longitud },
    );
  }
}
