import { Injectable, inject } from '@angular/core';
import { MatSnackBar } from '@angular/material/snack-bar';

// spec/07-ADMIN-UI-ANGULAR.md "Un error muestra siempre: causa probable,
// impacto, ultimo exito y accion recomendada" -- este servicio es el canal
// unico de banners/toasts para que ese mensaje sea consistente en toda la app.
@Injectable({ providedIn: 'root' })
export class NotificationService {
  private readonly snackBar = inject(MatSnackBar);

  error(message: string): void {
    this.snackBar.open(message, 'Cerrar', { duration: 8000, panelClass: 'notification-error' });
  }

  success(message: string): void {
    this.snackBar.open(message, 'Cerrar', { duration: 4000, panelClass: 'notification-success' });
  }
}
