import { Injectable, inject } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { Observable, map } from 'rxjs';

import { ConfirmDialogComponent, ConfirmDialogData, ConfirmDialogResult } from './confirm-dialog.component';

// Punto unico para abrir el modal de confirmacion (spec/07 "ninguna accion
// destructiva... sin modal de confirmacion explicita"), asi cada feature no
// repite el cableado de MatDialog.
@Injectable({ providedIn: 'root' })
export class ConfirmService {
  private readonly dialog = inject(MatDialog);

  confirm(data: ConfirmDialogData): Observable<ConfirmDialogResult> {
    return this.dialog
      .open<ConfirmDialogComponent, ConfirmDialogData, ConfirmDialogResult>(ConfirmDialogComponent, {
        data,
        width: '440px',
      })
      .afterClosed()
      .pipe(map((result) => result ?? { confirmed: false }));
  }
}
