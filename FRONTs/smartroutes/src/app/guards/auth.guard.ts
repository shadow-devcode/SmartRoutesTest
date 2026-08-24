import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map, take } from 'rxjs/operators';
import { AuthService } from '../services/auth.service';

/**
 * Protege rutas que requieren autenticación.
 *
 * Espera a que la restauración de sesión (cookie HttpOnly → access token)
 * termine antes de decidir si redirige al login o no.
 * Devuelve un UrlTree para que la redirección sea atómica (sin flash de pantalla).
 */
export const authGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);

  return auth.sessionReady$.pipe(
    take(1),
    map(() => auth.isAuthenticated() ? true : router.createUrlTree(['/login']))
  );
};
