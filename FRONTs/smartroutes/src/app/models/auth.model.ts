/**
 * Modelos para autenticación (login, usuario, tokens).
 */
export interface LoginRequest {
  email: string;
  password: string;
}

export interface AuthUser {
  id: number;
  email: string;
  full_name: string | null;
  role: string;
  /** Nombre exacto del mercadista en el Excel (solo rol USER). */
  assigned_mercadista?: string | null;
  /** Dataset de rutas (EDITOR / VISUALIZADOR / USER con Excel concreto). */
  assigned_route_dataset_id?: number | null;
}

export interface LoginResponse {
  success: boolean;
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export interface RefreshResponse {
  success: boolean;
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export interface ApiErrorResponse {
  success: false;
  error: string;
}
