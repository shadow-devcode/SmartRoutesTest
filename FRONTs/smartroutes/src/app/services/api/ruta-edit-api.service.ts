import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

import { environment } from '../../../environments/environment';
import {
  AsignarPendienteResponse,
  VisitaPendiente,
} from '../../models/mercadista.model';

/**
 * Endpoints de edición interactiva de rutas:
 * /ruta/orden, /ruta/mover-a-pendientes, /ruta/mover-visita, /ruta/asignar-pendiente.
 *
 * No envuelve los errores en catchError: los componentes manejan códigos 403/409
 * (CAMBIO_MERCADISTA_PROHIBIDO, tope_excedido) directamente desde HttpErrorResponse.
 */
@Injectable({ providedIn: 'root' })
export class RutaEditApiService {
  private readonly apiUrl = environment.apiUrl;

  constructor(private readonly http: HttpClient) {}

  /**
   * Reordena las visitas de un (mercadista, semana, día). El backend recalcula
   * tiempos, km y horarios. `ubicaciones` se renumera 1..n por posición.
   */
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
  ): Observable<{ success: boolean; message?: string; error?: string }> {
    const body = {
      mercadista,
      semana: semana?.trim() || 'semana 1',
      dia,
      ubicaciones: ubicaciones.map((u, i) => ({
        descripcion: u.descripcion,
        latitud: u.latitud,
        longitud: u.longitud,
        orden: i + 1,
      })),
    };
    return this.http.put<{ success: boolean; message?: string; error?: string }>(
      `${this.apiUrl}/ruta/orden`,
      body,
    );
  }

  /**
   * Quita una visita (o todas las del mismo punto) de la ruta y la pasa a
   * la hoja Pendientes_Sin_Asignar.
   */
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
  ): Observable<{
    success: boolean;
    message?: string;
    error?: string;
    visitas_movidas?: number;
  }> {
    return this.http.post<{
      success: boolean;
      message?: string;
      error?: string;
      visitas_movidas?: number;
    }>(`${this.apiUrl}/ruta/mover-a-pendientes`, {
      semana: semana?.trim() || 'semana 1',
      mercadista_origen: mercadistaOrigen,
      dia_origen: diaOrigen,
      todas_las_visitas: todasLasVisitas,
      visita: {
        descripcion: visita.descripcion,
        latitud: visita.latitud,
        longitud: visita.longitud,
      },
    });
  }

  /**
   * Mueve una visita a otro día/semana del MISMO mercadista. El backend recalcula
   * Orden Ruta, Tiempo entre sucursal, km y Horario en origen y destino.
   * Devuelve `codigo: CAMBIO_MERCADISTA_PROHIBIDO` (403) si se intenta cambiar
   * el mercadista (debe pasar por pendientes).
   */
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
  ): Observable<{
    success: boolean;
    message?: string;
    error?: string;
    codigo?: string;
  }> {
    const body: Record<string, unknown> = {
      semana: semana?.trim() || 'semana 1',
      mercadista_origen: mercadistaOrigen,
      dia_origen: diaOrigen,
      mercadista_destino: mercadistaDestino,
      dia_destino: diaDestino,
      orden_destino: ordenDestino,
      visita: {
        descripcion: visita.descripcion,
        latitud: visita.latitud,
        longitud: visita.longitud,
      },
    };
    if (semanaDestino?.trim()) {
      body['semana_destino'] = semanaDestino.trim();
    }
    return this.http.put<{
      success: boolean;
      message?: string;
      error?: string;
      codigo?: string;
    }>(`${this.apiUrl}/ruta/mover-visita`, body);
  }

  /**
   * Intercambia las jornadas completas de dos días del mismo mercaderista:
   * lo del lunes pasa al miércoles y viceversa, en una sola operación.
   */
  intercambiarDias(
    semana: string,
    mercadista: string,
    diaA: string,
    diaB: string,
  ): Observable<{ success: boolean; message?: string; error?: string; visitas_movidas?: number }> {
    return this.http.put<{
      success: boolean;
      message?: string;
      error?: string;
      visitas_movidas?: number;
    }>(`${this.apiUrl}/ruta/intercambiar-dias`, {
      semana: semana?.trim() || 'semana 1',
      mercadista,
      dia_a: diaA,
      dia_b: diaB,
    });
  }

  /**
   * Asigna una visita pendiente a un (mercadista, día, semana) destino.
   * Si el destino excede 480 min combinados y `forzar=false`, el backend
   * devuelve 409; el frontend debe pedir confirmación al usuario y reintentar
   * con `forzar=true`.
   */
  asignarPendiente(
    visita: VisitaPendiente,
    mercadistaDestino: string,
    diaDestino: string,
    semanaDestino: string,
    ordenDestino: number,
    forzar: boolean = false,
  ): Observable<AsignarPendienteResponse> {
    const body = {
      mercadista_destino: mercadistaDestino,
      dia_destino: diaDestino,
      semana_destino: semanaDestino?.trim() || visita.semana || 'semana 1',
      orden_destino: ordenDestino,
      forzar,
      visita: {
        descripcion: visita.descripcion,
        latitud: visita.latitud,
        longitud: visita.longitud,
        semana: visita.semana,
        tiempo_servicio: visita.tiempo_servicio,
        provincia: visita.provincia,
      },
    };
    return this.http.post<AsignarPendienteResponse>(
      `${this.apiUrl}/ruta/asignar-pendiente`,
      body,
    );
  }

  /** Deja ese día de las demás semanas igual que en `semanaOrigen`. */
  replicarDia(
    mercadista: string,
    dia: string,
    semanaOrigen: string,
  ): Observable<ReplicarDiaResponse> {
    return this.http.post<ReplicarDiaResponse>(`${this.apiUrl}/ruta/replicar-dia`, {
      mercadista,
      dia,
      semana_origen: semanaOrigen,
    });
  }

  /** Elimina un mercaderista. El servidor lo rechaza (409) si tiene algún punto. */
  eliminarMercadistaVacio(mercadista: string): Observable<MercadistaNuevoResponse> {
    return this.http.post<MercadistaNuevoResponse>(`${this.apiUrl}/ruta/mercadista-eliminar`, {
      mercadista,
    });
  }

  /** Da de alta un mercaderista sin puntos para poder arrastrarle pendientes. */
  crearMercadistaVacio(finDeSemana: boolean): Observable<MercadistaNuevoResponse> {
    return this.http.post<MercadistaNuevoResponse>(`${this.apiUrl}/ruta/mercadista-nuevo`, {
      fin_de_semana: finDeSemana,
    });
  }
}

export interface ReplicarDiaResponse {
  success: boolean;
  message?: string;
  semanas?: string[];
  agregadas?: number;
  movidas?: number;
  a_pendientes?: number;
  /** Visitas del día modelo que no se pudieron traer: «Punto (semana N)». */
  omitidas?: string[];
  error?: string;
}

export interface MercadistaNuevoResponse {
  success: boolean;
  mercadista?: string;
  dias?: string[];
  message?: string;
  error?: string;
}
