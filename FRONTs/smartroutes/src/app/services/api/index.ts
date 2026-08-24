/**
 * Barrel re-export para los services HTTP por dominio.
 * Permite `import { MercadistasApiService, ... } from '../../services/api';`
 * en vez de tener que conocer la ruta de cada archivo.
 */
export { ComparativaApiService } from './comparativa-api.service';
export { DashboardApiService } from './dashboard-api.service';
export { MercadistasApiService, type MercadistasListaPayload } from './mercadistas-api.service';
export { PendientesApiService } from './pendientes-api.service';
export { RutaEditApiService } from './ruta-edit-api.service';
