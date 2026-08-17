import { Routes } from '@angular/router';

import { requireRole } from './core/role.guard';

// spec/07-ADMIN-UI-ANGULAR.md "Routing: rutas y componentes cargados
// mediante lazy-loading"; "Guards: CanActivate basados en los roles
// locales". Lectura de cada seccion = viewer (spec/08); las paginas
// gatean sus propias acciones con un rol mayor.
export const routes: Routes = [
  {
    path: 'login',
    loadComponent: () => import('./features/login/login.component').then((m) => m.LoginComponent),
  },
  {
    path: 'forbidden',
    loadComponent: () => import('./layout/forbidden.component').then((m) => m.ForbiddenComponent),
  },
  {
    path: '',
    loadComponent: () => import('./layout/shell.component').then((m) => m.ShellComponent),
    canActivate: [requireRole('viewer')],
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'overview' },
      {
        path: 'overview',
        loadComponent: () => import('./features/overview/overview.component').then((m) => m.OverviewComponent),
      },
      {
        path: 'observability',
        loadComponent: () =>
          import('./features/observability/observability.component').then((m) => m.ObservabilityComponent),
      },
      {
        path: 'operations',
        loadComponent: () =>
          import('./features/operations-dlq/operations.component').then((m) => m.OperationsComponent),
      },
      {
        path: 'policies',
        loadComponent: () => import('./features/policies/policies.component').then((m) => m.PoliciesComponent),
      },
      {
        path: 'destinations',
        loadComponent: () =>
          import('./features/destinations/destinations.component').then((m) => m.DestinationsComponent),
      },
      {
        path: 'ingestion',
        loadComponent: () => import('./features/ingestion/ingestion.component').then((m) => m.IngestionComponent),
      },
      {
        path: 'opencti-settings',
        loadComponent: () =>
          import('./features/opencti-settings/opencti-settings.component').then((m) => m.OpenCTISettingsComponent),
      },
      {
        path: 'audit',
        loadComponent: () => import('./features/audit/audit.component').then((m) => m.AuditComponent),
      },
    ],
  },
  { path: '**', redirectTo: 'overview' },
];
