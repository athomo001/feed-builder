import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatListModule } from '@angular/material/list';
import { MatSelectModule } from '@angular/material/select';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatToolbarModule } from '@angular/material/toolbar';

import { AuthService } from '../core/auth.service';
import { ALL_ROLES, Role, roleSatisfies } from '../core/models';

interface NavItem {
  path: string;
  label: string;
  icon: string;
  minRole: Role;
}

// spec/07-ADMIN-UI-ANGULAR.md "Navegacion": los 7 items originales + 1
// agregado por el pivot "standalone" (Conexion OpenCTI, hub/opencti_settings_store.py).
// Lectura de cada seccion = viewer (spec/08 "viewer: lectura de estado y
// auditoria permitida"); las acciones dentro de cada pagina son las que
// piden un rol mayor, gateadas por el propio Admin API.
const NAV_ITEMS: NavItem[] = [
  { path: '/overview', label: 'Overview', icon: 'dashboard', minRole: 'viewer' },
  { path: '/observability', label: 'Observabilidad & Logs', icon: 'timeline', minRole: 'viewer' },
  { path: '/operations', label: 'Operaciones & DLQ', icon: 'build', minRole: 'viewer' },
  { path: '/policies', label: 'Politicas', icon: 'policy', minRole: 'viewer' },
  { path: '/destinations', label: 'Destinos', icon: 'dns', minRole: 'viewer' },
  { path: '/ingestion', label: 'OpenCTI / Ingesta', icon: 'sync', minRole: 'viewer' },
  { path: '/opencti-settings', label: 'Conexion OpenCTI', icon: 'cable', minRole: 'viewer' },
  { path: '/audit', label: 'Auditoria & Configuracion', icon: 'fact_check', minRole: 'viewer' },
];

@Component({
  selector: 'app-shell',
  standalone: true,
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    FormsModule,
    MatToolbarModule,
    MatSidenavModule,
    MatListModule,
    MatIconModule,
    MatButtonModule,
    MatSelectModule,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './shell.component.html',
  styleUrl: './shell.component.scss',
})
export class ShellComponent {
  private readonly auth = inject(AuthService);

  readonly role = this.auth.role;
  readonly allRoles = ALL_ROLES;
  readonly navItems = computed(() => NAV_ITEMS.filter((item) => roleSatisfies(this.auth.role(), item.minRole)));

  // Cosmetico: cambia solo la etiqueta local (ver AuthService.setDisplayRole).
  setDisplayRole(role: Role): void {
    this.auth.setDisplayRole(role);
  }

  logout(): void {
    this.auth.logout();
  }
}
