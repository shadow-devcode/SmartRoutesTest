/**
 * Configuración de producción. El token de Mapbox se inyecta aquí por el
 * pipeline de CI antes de `ng build --configuration=production`.
 *
 * Si haces el build manualmente, edita esta línea ANTES del build pero
 * NUNCA commitees el token. El bundle resultante seguirá conteniendo el
 * token (es inherente a cualquier libreria JS de mapas) — protégelo con
 * URL restrictions en el dashboard de Mapbox.
 */
export const environment = {
  production: true,
  apiUrl: '/api',
  mapboxAccessToken: '',
};
