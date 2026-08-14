import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';

@Component({
  selector: 'app-forbidden',
  standalone: true,
  imports: [RouterLink, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div style="padding: 3rem; text-align: center;">
      <h1>No tenes permiso para ver esta seccion</h1>
      <p>Tu rol actual no alcanza para esta pagina. Pedile a un administrador que te genere un token con un rol mayor.</p>
      <a mat-flat-button color="primary" routerLink="/overview">Volver a Overview</a>
    </div>
  `,
})
export class ForbiddenComponent {}
