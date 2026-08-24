/**
 * Plantilla de `environment.local.ts`. Copiar a `environment.local.ts`
 * (gitignored) y reemplazar el token por el real.
 *
 * Token de Mapbox: https://account.mapbox.com/access-tokens/
 *   - Es un token PÚBLICO (pk.xxx); cualquier usuario del sitio lo verá en
 *     DevTools → Network. La protección real es:
 *       1. URL restrictions en el dashboard de Mapbox (limita a tu dominio)
 *       2. Scopes mínimos: styles:read, fonts:read, tiles:read,
 *          directions:read, geocoding:read
 *       3. Rotación periódica
 */
export const environment = {
  production: false,
  apiUrl: '/api',
  mapboxAccessToken: 'pk.YOUR_MAPBOX_TOKEN_HERE',
};
