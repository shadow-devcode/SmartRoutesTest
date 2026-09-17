import {
  Component,
  ElementRef,
  HostListener,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { RouterOutlet, RouterModule, Router } from '@angular/router';
import { CommonModule } from '@angular/common';
import { AuthService } from './services/auth.service';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    RouterOutlet,
    RouterModule,
    CommonModule
  ],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css'
})
export class AppComponent {
  title = 'Smart Routes';
  auth = inject(AuthService);
  private router = inject(Router);

  /** Menú de usuario (avatar + nombre). */
  readonly userMenuOpen = signal(false);

  /** Panel de enlaces desplegable: solo se usa en pantallas estrechas. */
  readonly navOpen = signal(false);

  /** Enlaces de la barra, en orden. `visible` decide por rol. */
  readonly enlaces: {
    ruta: string;
    texto: string;
    icono: string;
    exacto?: boolean;
    visible: () => boolean;
  }[] = [
    { ruta: '/rutas', texto: 'Mapa de rutas', icono: 'mapa', exacto: true, visible: () => true },
    { ruta: '/comparativa', texto: 'Comparativa', icono: 'comparativa', visible: () => this.auth.canAccessComparativa() },
    { ruta: '/dashboard', texto: 'Dashboard', icono: 'dashboard', visible: () => this.auth.canAccessDashboard() },
    {
      ruta: '/dashboard-comparativa',
      texto: 'Dashboard comparativa',
      icono: 'tendencia',
      visible: () => this.auth.canAccessDashboard() && this.auth.canAccessComparativa(),
    },
    { ruta: '/carga-excel', texto: 'Cargar Excel', icono: 'subir', visible: () => this.auth.canAccessCargaExcel() },
    { ruta: '/gestion-excels', texto: 'Excels / Rutas', icono: 'tabla', visible: () => this.auth.canAccessGestionExcels() },
    { ruta: '/gestion-pendientes', texto: 'Pendientes', icono: 'lista', visible: () => this.auth.canAccessPendientes() },
    { ruta: '/gestion-usuarios', texto: 'Usuarios', icono: 'usuarios', visible: () => this.auth.canAccessGestionUsuarios() },
  ];

  toggleNav(event: Event): void {
    event.stopPropagation();
    this.userMenuOpen.set(false);
    this.navOpen.update((open) => !open);
  }

  closeNav(): void {
    this.navOpen.set(false);
  }
  private readonly userMenuRoot = viewChild<ElementRef<HTMLElement>>('userMenuRoot');

  get isLoginPage(): boolean {
    return this.router.url.includes('/login');
  }

  toggleUserMenu(event: Event): void {
    event.stopPropagation();
    this.navOpen.set(false);
    this.userMenuOpen.update((open) => !open);
  }

  closeUserMenu(): void {
    this.userMenuOpen.set(false);
  }

  onLogoutClick(): void {
    this.closeUserMenu();
    this.auth.logout();
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    const destino = event.target as HTMLElement | null;
    if (this.navOpen() && !destino?.closest('.nav-links, .nav-burger')) {
      this.closeNav();
    }
    if (!this.userMenuOpen()) return;
    const root = this.userMenuRoot()?.nativeElement;
    if (root && !root.contains(event.target as Node)) {
      this.closeUserMenu();
    }
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.userMenuOpen()) {
      this.closeUserMenu();
    }
    this.closeNav();
  }

  /** Nombre visible junto a «Cerrar sesión» (full_name o email). */
  get loggedInDisplayName(): string {
    const u = this.auth.user();
    if (!u) return '';
    const n = u.full_name?.trim();
    return n || u.email;
  }

  /** Iniciales para el avatar del usuario. */
  get userInitials(): string {
    const u = this.auth.user();
    if (!u) return '?';
    const raw = (u.full_name?.trim() || u.email || '?').toUpperCase();
    const parts = raw.split(/\s+/).filter(Boolean);
    if (parts.length >= 2) {
      const a = parts[0][0] || '';
      const b = parts[parts.length - 1][0] || '';
      return (a + b).slice(0, 2);
    }
    return raw.slice(0, 2);
  }
}
