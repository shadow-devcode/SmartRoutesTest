/**
 * Validación de coordenadas geográficas para defensa contra datos
 * corruptos del Excel.
 *
 * El parser del backend puede dejar pasar lat/lng numéricos pero fuera de
 * rango (ej. `lat=-327020085467353` cuando el punto decimal se pierde en la
 * lectura del Excel). Mapbox 3.x intenta proyectar esos valores a píxeles y
 * bloquea el main thread, congelando la UI.
 *
 * Usar en todo filtro de UbicacionMapa antes de pasar al componente <app-mapa>.
 */

export function coordValida(lat: unknown, lng: unknown): boolean {
  if (lat == null || lng == null) return false;
  const la = Number(lat);
  const lo = Number(lng);
  if (!Number.isFinite(la) || !Number.isFinite(lo)) return false;
  if (la === 0 && lo === 0) return false;
  return la >= -90 && la <= 90 && lo >= -180 && lo <= 180;
}

/** Versión sobre objeto: acepta cualquier shape con latitud/longitud. */
export function ubicacionTieneCoordValida(
  ub: { latitud?: unknown; longitud?: unknown } | null | undefined,
): boolean {
  if (!ub) return false;
  return coordValida(ub.latitud, ub.longitud);
}
