import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

// spec/07-ADMIN-UI-ANGULAR.md "Principios visuales": verde=operativo/
// entregado, ambar=degradado/reintentando/advertencia, rojo=incidente/
// caido/DLQ con fallos, gris=pausado/inactivo.
export type BadgeTone = 'green' | 'amber' | 'red' | 'gray';

const STATE_TONE: Record<string, BadgeTone> = {
  delivered: 'green',
  acknowledged: 'green',
  published: 'green',
  ok: 'green',
  pending: 'gray',
  paused: 'gray',
  skipped: 'gray',
  revoked: 'gray',
  expired: 'gray',
  draft: 'gray',
  superseded: 'gray',
  sending: 'amber',
  retrying: 'amber',
  rolled_back: 'amber',
  degraded: 'amber',
  dead_letter: 'red',
  down: 'red',
  failure: 'red',
};

@Component({
  selector: 'app-status-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span class="status-badge" [class]="'status-badge--' + tone()">{{ label() }}</span>`,
  styles: [
    `
      .status-badge {
        display: inline-flex;
        align-items: center;
        padding: 0.15rem 0.6rem;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.02em;
        white-space: nowrap;
      }
      .status-badge--green {
        background: #e6f4ea;
        color: #1e7e34;
      }
      .status-badge--amber {
        background: #fff4e5;
        color: #b25e00;
      }
      .status-badge--red {
        background: #fdecea;
        color: #c62828;
      }
      .status-badge--gray {
        background: #eceff1;
        color: #546e7a;
      }
    `,
  ],
})
export class StatusBadgeComponent {
  readonly state = input.required<string>();
  readonly toneOverride = input<BadgeTone | undefined>(undefined);

  readonly tone = computed<BadgeTone>(() => this.toneOverride() ?? STATE_TONE[this.state()] ?? 'gray');
  readonly label = computed(() => this.state().replace(/_/g, ' '));
}
