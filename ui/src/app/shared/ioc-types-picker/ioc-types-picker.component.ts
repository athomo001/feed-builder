import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatChipsModule } from '@angular/material/chips';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';

import { FAMILY_LABELS, FAMILY_SUBTYPES, IocFamily } from '../../core/ioc-catalog';

// Reemplaza el textarea "family/subtype separados por coma" (item #1/#3 de
// ISSUES.md): nadie se sabe de memoria los codes validos. Los valores
// seleccionados viajan como strings "family/subtype" (mismo formato que ya
// esperaba el backend en allowed_ioc_types / policy_simulation).
@Component({
  selector: 'app-ioc-types-picker',
  standalone: true,
  imports: [FormsModule, MatButtonModule, MatCheckboxModule, MatChipsModule, MatFormFieldModule, MatIconModule, MatInputModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="ioc-picker">
      @for (family of families; track family) {
        <div class="ioc-picker__family">
          <p class="ioc-picker__family-label">{{ familyLabels[family] }}</p>
          <div class="ioc-picker__subtypes">
            @for (subtype of subtypesOf(family); track subtype) {
              <mat-checkbox [ngModel]="isChecked(key(family, subtype))" (ngModelChange)="toggle(key(family, subtype), $event)">
                {{ subtype }}
              </mat-checkbox>
            }
          </div>
        </div>
      }

      <div class="ioc-picker__family">
        <p class="ioc-picker__family-label">{{ familyLabels['custom'] }}</p>
        <div class="ioc-picker__custom-add">
          <mat-form-field appearance="outline" subscriptSizing="dynamic">
            <mat-label>Subtipo custom</mat-label>
            <input matInput [ngModel]="customInput()" (ngModelChange)="customInput.set($event)" (keydown.enter)="addCustom()" />
          </mat-form-field>
          <button mat-button type="button" (click)="addCustom()">Agregar</button>
        </div>
        @if (customSelected().length > 0) {
          <mat-chip-set>
            @for (item of customSelected(); track item) {
              <mat-chip (removed)="removeCustom(item)">
                {{ item.split('/')[1] }}
                <button matChipRemove><mat-icon>cancel</mat-icon></button>
              </mat-chip>
            }
          </mat-chip-set>
        }
      </div>
    </div>
  `,
  styles: [
    `
      .ioc-picker__family {
        margin-bottom: 0.75rem;
      }
      .ioc-picker__family-label {
        font-weight: 600;
        margin: 0 0 0.25rem;
      }
      .ioc-picker__subtypes {
        display: flex;
        flex-wrap: wrap;
        gap: 0.25rem 1rem;
      }
      .ioc-picker__custom-add {
        display: flex;
        align-items: center;
        gap: 0.5rem;
      }
    `,
  ],
})
export class IocTypesPickerComponent {
  readonly value = input<string[]>([]);
  readonly valueChange = output<string[]>();

  readonly familyLabels = FAMILY_LABELS;
  readonly families = Object.keys(FAMILY_SUBTYPES) as Array<Exclude<IocFamily, 'custom'>>;
  readonly customInput = signal('');

  readonly customSelected = computed(() => this.value().filter((v) => v.startsWith('custom/')));

  subtypesOf(family: Exclude<IocFamily, 'custom'>): string[] {
    return FAMILY_SUBTYPES[family];
  }

  key(family: string, subtype: string): string {
    return `${family}/${subtype}`;
  }

  isChecked(key: string): boolean {
    return this.value().includes(key);
  }

  toggle(key: string, checked: boolean): void {
    const current = this.value();
    this.valueChange.emit(checked ? [...current, key] : current.filter((v) => v !== key));
  }

  addCustom(): void {
    const subtype = this.customInput().trim();
    if (!subtype) return;
    const key = this.key('custom', subtype);
    if (!this.value().includes(key)) {
      this.valueChange.emit([...this.value(), key]);
    }
    this.customInput.set('');
  }

  removeCustom(key: string): void {
    this.valueChange.emit(this.value().filter((v) => v !== key));
  }
}
