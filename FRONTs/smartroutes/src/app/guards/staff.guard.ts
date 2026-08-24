import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map, take } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';

/** Rutas que pueden usar ADMIN o EDITOR (carga, dashboard, comparativa). */
export const staffGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.sessionReady$.pipe(
    take(1),
    map(() => (auth.isStaff() ? true : router.createUrlTree(['/rutas'])))
  );
};
