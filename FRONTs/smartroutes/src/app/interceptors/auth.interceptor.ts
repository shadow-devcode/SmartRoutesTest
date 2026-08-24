import { HttpInterceptorFn, HttpErrorResponse } from '@angular/common/http';
import { inject } from '@angular/core';
import { catchError, switchMap, throwError } from 'rxjs';
import { AuthService } from '../services/auth.service';
import { Router } from '@angular/router';
import { environment } from '../../environments/environment';

/**
 * Ruta de la petición como pathname (p. ej. /api/processing-status).
 * Angular a veces entrega URLs relativas sin "/" inicial; sin esto no se añade el Bearer y el back responde 401.
 */
function requestPathname(url: string): string {
  if (url.startsWith('http://') || url.startsWith('https://')) {
    try {
      return new URL(url).pathname;
    } catch {
      /* continuar */
    }
  }
  return url.startsWith('/') ? url : `/${url}`;
}

/** Prefijo de ruta de la API (p. ej. /api), aunque en environment esté como URL absoluta. */
function configuredApiPathPrefix(): string {
  const raw = (environment.apiUrl || '/api').replace(/\/$/, '');
  if (raw.startsWith('http://') || raw.startsWith('https://')) {
    try {
      const p = new URL(raw.endsWith('/') ? raw : `${raw}/`).pathname;
      return p.replace(/\/$/, '') || '/api';
    } catch {
      return '/api';
    }
  }
  return raw.startsWith('/') ? raw : `/${raw}`;
}

/**
 * Interceptor de autenticación:
 * 1. Añade Authorization: Bearer <access_token> a todas las peticiones a /api.
 * 2. Añade withCredentials: true para que el navegador envíe la cookie HttpOnly
 *    del refresh_token en las llamadas a /api/auth/*.
 * 3. En caso de 401, intenta renovar el access_token usando la cookie
 *    (sin enviar el refresh_token en el body) y reintenta la petición original.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const apiBase = configuredApiPathPrefix();
  const path = requestPathname(req.url);
  const isApiRequest = path === apiBase || path.startsWith(`${apiBase}/`);

  if (!isApiRequest) {
    return next(req);
  }

  const token = auth.getToken();
  let modifiedReq = req.clone({ withCredentials: true });

  if (token) {
    modifiedReq = modifiedReq.clone({
      setHeaders: { Authorization: `Bearer ${token}` },
    });
  }

  return next(modifiedReq).pipe(
    catchError((err: HttpErrorResponse) => {
      // No reintentar si ya es una llamada de refresh (evitar bucle infinito)
      if (err.status === 401 && !requestPathname(req.url).includes('/auth/refresh')) {
        return auth.refreshToken().pipe(
          switchMap(() => {
            const newToken = auth.getToken();
            if (!newToken) {
              auth.clearStorage({ skipServer: true, skipBroadcast: true });
              router.navigate(['/login']);
              return throwError(() => err);
            }
            // Reconstruir desde `req` original: evita cabeceras perdidas al clonar encadenado.
            const retryReq = req.clone({
              setHeaders: { Authorization: `Bearer ${newToken}` },
              withCredentials: true,
            });
            return next(retryReq);
          }),
          catchError(() => {
            // refreshToken() ya limpió almacenamiento local; solo navegar
            router.navigate(['/login']);
            return throwError(() => err);
          })
        );
      }

      // 401 en POST /auth/refresh: sin cookie o token ya rotado; no forzar login aquí
      // (AuthService.tryRestoreSession / refreshToken ya gestionan estado y rutas).
      return throwError(() => err);
    })
  );
};
