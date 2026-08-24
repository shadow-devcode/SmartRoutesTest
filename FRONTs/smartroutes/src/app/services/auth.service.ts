import { Injectable, NgZone, signal, computed } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Router } from '@angular/router';
import {
  Observable,
  ReplaySubject,
  catchError,
  finalize,
  of,
  shareReplay,
  tap,
  throwError,
} from 'rxjs';
import { environment } from '../../environments/environment';
import type { AuthUser, LoginResponse, RefreshResponse } from '../models/auth.model';

const API_AUTH = `${environment.apiUrl}/auth`;

/**
 * /refresh y /logout se autentican solo con la cookie HttpOnly `rt`. Como capa
 * extra de protección CSRF (más allá de SameSite), el backend exige esta
 * cabecera personalizada: un <form>/<img> cross-site no puede agregarla, y un
 * fetch cross-origin que la incluya dispara un preflight CORS que el backend
 * rechaza para orígenes no confiables.
 */
const CSRF_HEADERS = { 'X-Requested-With': 'XMLHttpRequest' };

/** Clave legada (JWT en localStorage); se borra al arrancar para no dejar tokens visibles en almacenamiento. */
const LEGACY_LS_SESSION_KEY = 'smartroutes_auth_session';

/**
 * Sesión al estilo “cookie HttpOnly + memoria”:
 * - El refresh token solo va en cookie (no accesible a JS).
 * - El access JWT vive en memoria (signals) y se renueva con POST /auth/refresh al cargar o antes de caducar.
 * - BroadcastChannel sincroniza el access entre pestañas cuando una de ellas renueva.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly _accessToken = signal<string | null>(null);
  private readonly _currentUser = signal<AuthUser | null>(null);

  private readonly _sessionReady$ = new ReplaySubject<void>(1);
  readonly sessionReady$ = this._sessionReady$.asObservable();

  readonly user = this._currentUser.asReadonly();
  readonly isAuthenticated = computed(() => !!this._accessToken());

  private _refreshTimer: ReturnType<typeof setTimeout> | null = null;
  /**
   * Una sola petición POST /refresh a la vez. Con rotación en servidor, dos refreshes paralelos
   * con la misma cookie revocan el token del otro → 401 al recargar si el interceptor y el arranque compiten.
   */
  private _inflightRefresh$: Observable<RefreshResponse> | null = null;
  /** Evita /refresh duplicado al arranque si visibilitychange dispara antes de terminar tryRestoreSession. */
  private _initialAuthRoundDone = false;
  private readonly _bc: BroadcastChannel | null =
    typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('smartroutes-auth') : null;

  constructor(
    private readonly http: HttpClient,
    private readonly router: Router,
    private readonly zone: NgZone
  ) {
    try {
      localStorage.removeItem(LEGACY_LS_SESSION_KEY);
    } catch {
      /* */
    }

    this._bc?.addEventListener('message', (ev: MessageEvent) => {
      const d = ev.data as { t?: string; access_token?: string; user?: AuthUser; expires_in?: number };
      if (d?.t === 'sr-out') {
        this.zone.run(() => {
          this.clearStorage({ skipServer: true, skipBroadcast: true });
          if (this.router.url !== '/login' && !this.router.url.startsWith('/login?')) {
            this.router.navigate(['/login']);
          }
        });
        return;
      }
      if (d?.t === 'sr-upd' && d.access_token && d.user) {
        if (d.access_token === this._accessToken()) return;
        this.zone.run(() => {
          this.applySession(d.access_token!, d.user!, d.expires_in, { broadcast: false });
        });
      }
    });

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      if (!this._initialAuthRoundDone) return;
      if (this.router.url.startsWith('/login')) return;
      if (this._accessToken()) return;
      this.refreshHttp$()
        .pipe(catchError((): Observable<RefreshResponse | null> => of(null)))
        .subscribe();
    });

    this.tryRestoreSession();
  }

  /**
   * POST /refresh compartido: un único HTTP aunque varios suscriptores (arranque + interceptor).
   */
  private refreshHttp$(): Observable<RefreshResponse> {
    if (!this._inflightRefresh$) {
      this._inflightRefresh$ = this.http
        .post<RefreshResponse>(`${API_AUTH}/refresh`, {}, { withCredentials: true, headers: CSRF_HEADERS })
        .pipe(
          tap((res) => {
            if (res.success && res.access_token && res.user) {
              this.applySession(res.access_token, res.user, res.expires_in, { broadcast: true });
            }
          }),
          shareReplay({ bufferSize: 1, refCount: true }),
          finalize(() => {
            this._inflightRefresh$ = null;
          })
        );
    }
    return this._inflightRefresh$;
  }

  /**
   * POST /refresh con cookie HttpOnly; emite sessionReady una vez (el authGuard espera esto).
   */
  private tryRestoreSession(): void {
    this.refreshHttp$()
      .pipe(catchError((): Observable<RefreshResponse | null> => of(null)))
      .subscribe((res) => {
        if (!res?.success || !res.access_token || !res.user) {
          this.clearStorage({ skipServer: true, skipBroadcast: true });
        }
        this._initialAuthRoundDone = true;
        this._sessionReady$.next();
      });
  }

  private applySession(
    token: string,
    user: AuthUser,
    expiresInSec?: number,
    opts?: { broadcast?: boolean }
  ): void {
    this._accessToken.set(token);
    this._currentUser.set(user);
    if (expiresInSec != null && expiresInSec > 0) {
      this.scheduleProactiveRefresh(expiresInSec);
    }
    if (opts?.broadcast !== false && this._bc) {
      this._bc.postMessage({
        t: 'sr-upd',
        access_token: token,
        user,
        expires_in: expiresInSec,
      });
    }
  }

  /** Renueva el access token ~2 min antes de que caduque (p. ej. 15 min en servidor). */
  private scheduleProactiveRefresh(expiresInSec: number): void {
    if (this._refreshTimer) {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = null;
    }
    const bufferSec = 120;
    const delayMs = Math.max((expiresInSec - bufferSec) * 1000, 45_000);
    this._refreshTimer = setTimeout(() => {
      this._refreshTimer = null;
      this.refreshToken().pipe(catchError(() => of(null))).subscribe();
    }, delayMs);
  }

  clearStorage(options?: { skipServer?: boolean; skipBroadcast?: boolean }): void {
    if (this._refreshTimer) {
      clearTimeout(this._refreshTimer);
      this._refreshTimer = null;
    }
    if (!options?.skipBroadcast) {
      this._bc?.postMessage({ t: 'sr-out' });
    }
    this._accessToken.set(null);
    this._currentUser.set(null);
    try {
      localStorage.removeItem(LEGACY_LS_SESSION_KEY);
    } catch {
      /* */
    }
  }

  getToken(): string | null {
    return this._accessToken();
  }

  getRefreshToken(): string | null {
    return null;
  }

  login(email: string, password: string): Observable<LoginResponse> {
    return this.http
      .post<LoginResponse>(`${API_AUTH}/login`, { email, password }, { withCredentials: true })
      .pipe(
        tap((res) => {
          if (res.success && res.access_token && res.user) {
            this.applySession(res.access_token, res.user, res.expires_in, { broadcast: true });
          }
        })
      );
  }

  logout(): void {
    this.http
      .post<{ success: boolean }>(`${API_AUTH}/logout`, {}, { withCredentials: true, headers: CSRF_HEADERS })
      .pipe(catchError(() => of({ success: false })))
      .subscribe(() => {
        this.clearStorage({ skipServer: true, skipBroadcast: false });
        this.router.navigate(['/login']);
      });
  }

  refreshToken(): Observable<RefreshResponse> {
    return this.refreshHttp$().pipe(
      catchError((err) => {
        this.clearStorage({ skipServer: true, skipBroadcast: true });
        return throwError(() => err);
      })
    );
  }

  me(): Observable<{ success: boolean; user: AuthUser }> {
    return this.http.get<{ success: boolean; user: AuthUser }>(`${API_AUTH}/me`).pipe(
      tap((res) => {
        if (res.success && res.user) {
          this._currentUser.set(res.user);
        }
      })
    );
  }

  hasRole(role: string): boolean {
    return this._currentUser()?.role === role;
  }

  isAdmin(): boolean {
    return this.hasRole('ADMIN');
  }

  isEditor(): boolean {
    return this.hasRole('EDITOR');
  }

  isVisualizador(): boolean {
    return this.hasRole('VISUALIZADOR');
  }

  /** EDITOR o ADMIN: edición de rutas y comparativa (no incluye carga global de Excel). */
  isStaff(): boolean {
    const r = this._currentUser()?.role;
    return r === 'ADMIN' || r === 'EDITOR';
  }

  /** Reordenar / mover visitas en el mapa (ADMIN o EDITOR). */
  canEditMapaRutas(): boolean {
    return this.isAdmin() || this.isEditor();
  }

  canAccessDashboard(): boolean {
    return this.isAdmin() || this.isEditor() || this.isVisualizador();
  }

  canAccessComparativa(): boolean {
    return this.isAdmin() || this.isEditor();
  }

  canAccessCargaExcel(): boolean {
    return this.isAdmin();
  }

  canAccessGestionExcels(): boolean {
    return this.isAdmin();
  }

  /**
   * Gestión de pendientes: mismo nivel que el panel de pendientes del mapa, que
   * es lo que autoriza el backend en /api/pendientes/*.
   */
  canAccessPendientes(): boolean {
    return this.isAdmin() || this.isEditor();
  }

  canAccessGestionUsuarios(): boolean {
    return this.isAdmin() || this.isEditor();
  }

  /** Solo usuarios con ruta asignada en BD (rol USER con filtro por mercadista). */
  isUserMercadistaScope(): boolean {
    return this.hasRole('USER');
  }
}
