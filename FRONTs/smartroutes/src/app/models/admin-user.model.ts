export interface AdminUserRow {
  id: number;
  email: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  /** Nombre en columna Mercadista del Excel (solo aplica a rol USER). */
  assigned_mercadista?: string | null;
  assigned_route_dataset_id?: number | null;
  /** ID del editor que creó la cuenta (si aplica). */
  created_by_user_id?: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminUsersListResponse {
  success: boolean;
  users: AdminUserRow[];
}

export interface AdminUserMutationResponse {
  success: boolean;
  user?: AdminUserRow;
  message?: string;
  error?: string;
}

/** Fila de la tabla «Mercadistas del Excel» en administración. */
export interface MercadistaAsignacionRow {
  mercadista: string;
  user_id: number | null;
}

/** Dataset cuyo Excel de horarios se está usando en la tabla de asignación. */
export interface MercadistasAsignacionExcelContext {
  dataset_id: number | null;
  display_name: string | null;
}

export interface MercadistasAsignacionResponse {
  success: boolean;
  rows: MercadistaAsignacionRow[];
  excel_context?: MercadistasAsignacionExcelContext;
}

export interface MercadistaAsignacionPutResponse {
  success: boolean;
  mercadista?: string;
  user_id?: number | null;
  nuevo_nombre_excel?: string;
  mercadista_anterior?: string;
  error?: string;
}
