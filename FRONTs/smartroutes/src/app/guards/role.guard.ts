import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map, take } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';

/**
 * Protege rutas que requieren rol ADMIN.
 * Espera sessionReady y redirige al mapa con UrlTree si el usuario no es administrador.
 */
export const adminGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.sessionReady$.pipe(
    take(1),
    map(() => (auth.isAdmin() ? true : router.createUrlTree(['/rutas'])))
  );
};

/** ADMIN o EDITOR: gestión de usuarios acotada en servidor para el editor. */
export const adminOrEditorGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.sessionReady$.pipe(
    take(1),
    map(() =>
      auth.canAccessGestionUsuarios() ? true : router.createUrlTree(['/rutas'])
    )
  );
};

/** ADMIN, EDITOR o VISUALIZADOR: dashboard de estadísticas (solo lectura en UI). */
export const dashboardGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.sessionReady$.pipe(
    take(1),
    map(() =>
      auth.canAccessDashboard() ? true : router.createUrlTree(['/rutas'])
    )
  );
};

/** ADMIN o EDITOR: vista comparativa / carga de Excel comparativo. */
export const comparativaGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.sessionReady$.pipe(
    take(1),
    map(() =>
      auth.canAccessComparativa() ? true : router.createUrlTree(['/rutas'])
    )
  );
};

/** ADMIN o EDITOR: gestión de pendientes (mismo nivel que /api/pendientes). */
export const pendientesGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.sessionReady$.pipe(
    take(1),
    map(() => (auth.canAccessPendientes() ? true : router.createUrlTree(['/rutas'])))
  );
};
