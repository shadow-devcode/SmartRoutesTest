import { Routes } from '@angular/router';
import { authGuard } from './guards/auth.guard';
import {
  adminGuard,
  adminOrEditorGuard,
  comparativaGuard,
  dashboardGuard,
  pendientesGuard,
} from './guards/role.guard';

export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./components/login/login.component').then(m => m.LoginComponent),
    title: 'Iniciar sesión - Smart Routes',
  },
  {
    path: '',
    redirectTo: 'rutas',
    pathMatch: 'full',
  },
  {
    path: 'rutas',
    loadComponent: () =>
      import('./components/vista-mapa/vista-mapa.component').then(m => m.VistaMapaComponent),
    title: 'Mapa de Rutas - Smart Routes',
    canActivate: [authGuard],
  },
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./components/nueva-vista/nueva-vista.component').then(m => m.NuevaVistaComponent),
    title: 'Dashboard - Smart Routes',
    canActivate: [authGuard, dashboardGuard],
  },
  {
    path: 'comparativa',
    loadComponent: () =>
      import('./components/vista-comparativa/vista-comparativa.component').then(
        m => m.VistaComparativaComponent
      ),
    title: 'Vista Comparativa - Smart Routes',
    canActivate: [authGuard, comparativaGuard],
  },
  {
    path: 'dashboard-comparativa',
    loadComponent: () =>
      import('./components/nueva-vista/nueva-vista.component').then(m => m.NuevaVistaComponent),
    title: 'Dashboard comparativa - Smart Routes',
    canActivate: [authGuard, comparativaGuard],
    data: { fuente: 'comparativa' },
  },
  {
    path: 'carga-excel',
    loadComponent: () =>
      import('./components/carga-excel/carga-excel.component').then(
        (m) => m.CargaExcelComponent
      ),
    title: 'Carga de Datos - Smart Routes',
    canActivate: [authGuard, adminGuard],
  },
  {
    path: 'gestion-excels',
    loadComponent: () =>
      import('./components/gestion-excels/gestion-excels.component').then(
        (m) => m.GestionExcelsComponent
      ),
    title: 'Gestión de Excels - Smart Routes',
    canActivate: [authGuard, adminGuard],
  },
  {
    path: 'gestion-pendientes',
    loadComponent: () =>
      import('./components/gestion-pendientes/gestion-pendientes.component').then(
        (m) => m.GestionPendientesComponent
      ),
    title: 'Gestión de pendientes - Smart Routes',
    canActivate: [authGuard, pendientesGuard],
  },
  {
    path: 'admin',
    redirectTo: '/gestion-usuarios',
    pathMatch: 'full',
  },
  {
    path: 'gestion-usuarios',
    loadComponent: () => import('./components/admin/admin.component').then(m => m.AdminComponent),
    title: 'Gestión de usuarios - Smart Routes',
    canActivate: [authGuard, adminOrEditorGuard],
  },
  {
    path: '**',
    redirectTo: 'rutas',
    pathMatch: 'full',
  },
];
