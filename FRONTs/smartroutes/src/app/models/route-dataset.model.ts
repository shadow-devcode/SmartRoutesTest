export interface RouteDatasetRow {
  id: number;
  display_name: string;
  storage_slug: string;
  is_active: boolean;
  created_at: string | null;
  horarios_exists: boolean;
  comparativa_exists: boolean;
}

export interface RouteDatasetsListResponse {
  success: boolean;
  datasets: RouteDatasetRow[];
}

export interface RouteDatasetActivateResponse {
  success: boolean;
  dataset?: RouteDatasetRow;
  error?: string;
}
