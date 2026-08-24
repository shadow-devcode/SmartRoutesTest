import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import type {
  AdminUserMutationResponse,
  AdminUsersListResponse,
  MercadistaAsignacionPutResponse,
  MercadistasAsignacionResponse,
} from '../models/admin-user.model';
import type {
  RouteDatasetActivateResponse,
  RouteDatasetsListResponse,
} from '../models/route-dataset.model';

@Injectable({ providedIn: 'root' })
export class AdminUsersService {
  private readonly base = `${environment.apiUrl}/admin`;

  constructor(private readonly http: HttpClient) {}

  /**
   * Lista usuarios. Solo administrador puede usar `createdByEditor` (cuentas dadas de alta por ese editor).
   */
  listUsers(opts?: { createdByEditor?: number }): Observable<AdminUsersListResponse> {
    const id = opts?.createdByEditor;
    const q =
      id != null && id > 0
        ? `?created_by_editor=${encodeURIComponent(String(id))}`
        : '';
    return this.http.get<AdminUsersListResponse>(`${this.base}/users${q}`);
  }

  createUser(body: {
    full_name: string;
    email: string;
    password: string;
    role: 'ADMIN' | 'USER' | 'EDITOR' | 'VISUALIZADOR';
  }): Observable<AdminUserMutationResponse> {
    return this.http.post<AdminUserMutationResponse>(`${this.base}/users`, body);
  }

  updateUser(
    id: number,
    body: Partial<{
      full_name: string;
      email: string;
      password: string;
      role: 'ADMIN' | 'USER' | 'EDITOR' | 'VISUALIZADOR';
      is_active: boolean;
      assigned_mercadista: string | null;
      assigned_route_dataset_id: number | null;
    }>
  ): Observable<AdminUserMutationResponse> {
    return this.http.put<AdminUserMutationResponse>(`${this.base}/users/${id}`, body);
  }

  deleteUser(id: number): Observable<{ success: boolean; message?: string; error?: string }> {
    return this.http.delete<{ success: boolean; message?: string; error?: string }>(
      `${this.base}/users/${id}`
    );
  }

  getMercadistasAsignacion(): Observable<MercadistasAsignacionResponse> {
    return this.http.get<MercadistasAsignacionResponse>(`${this.base}/mercadistas-asignacion`);
  }

  putMercadistaAsignacion(body: {
    mercadista_actual: string;
    user_id: number | null;
  }): Observable<MercadistaAsignacionPutResponse> {
    return this.http.put<MercadistaAsignacionPutResponse>(
      `${this.base}/mercadistas-asignacion`,
      body
    );
  }

  /**
   * Excel de rutas con columna Mercadista actual (incluye nombres de usuario asignados).
   * Devuelve la respuesta completa para poder leer el filename del header Content-Disposition.
   */
  downloadHorariosExcelActualizado() {
    return this.http.get(`${this.base}/horarios-excel`, {
      responseType: 'blob',
      observe: 'response',
    });
  }

  listRouteDatasets(): Observable<RouteDatasetsListResponse> {
    return this.http.get<RouteDatasetsListResponse>(`${this.base}/route-datasets`);
  }

  activateRouteDataset(datasetId: number): Observable<RouteDatasetActivateResponse> {
    return this.http.post<RouteDatasetActivateResponse>(`${this.base}/route-datasets/activate`, {
      dataset_id: datasetId,
    });
  }

  deleteRouteDataset(datasetId: number): Observable<{ success: boolean; message?: string; error?: string }> {
    return this.http.delete<{ success: boolean; message?: string; error?: string }>(
      `${this.base}/route-datasets/${datasetId}`
    );
  }
}
