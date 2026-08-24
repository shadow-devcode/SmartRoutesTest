/**
 * Default fallback. No contiene secretos.
 *
 * En desarrollo se sustituye por `environment.local.ts` (gitignored) vía
 * `fileReplacements` en angular.json (configuración `local`, usada por
 * `npm start`).
 *
 * En producción se sustituye por `environment.prod.ts`. Si despliegas, el
 * pipeline de CI debe inyectar el token de Mapbox en `environment.prod.ts`
 * antes de `ng build --configuration=production`.
 */
export const environment = {
  production: false,
  apiUrl: '/api',
  mapboxAccessToken: '',
};
