import {ChangeDetectionStrategy, Component, input} from '@angular/core';

export const AUTO_REFRESH_INTERVAL_MS = 1_000;

@Component({
  selector: 'app-auto-refresh-indicator',
  template: '<span class="auto-refresh" [class.auto-refresh--active]="refreshing()" aria-label="Auto-refresh enabled"><span class="auto-refresh__icon" aria-hidden="true">↻</span><span>Auto-refresh</span></span>',
  styles: [':host { display: inline-block; } .auto-refresh { align-items: center; color: var(--tui-text-secondary); display: inline-flex; font: var(--tui-font-text-s); gap: .4rem; white-space: nowrap; } .auto-refresh__icon { display: inline-block; font-size: 1.1rem; line-height: 1; } .auto-refresh--active .auto-refresh__icon { animation: auto-refresh-rotate 1.2s ease-in-out infinite; } @keyframes auto-refresh-rotate { to { transform: rotate(360deg); } } @media (prefers-reduced-motion: reduce) { .auto-refresh--active .auto-refresh__icon { animation: none; } }'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AutoRefreshIndicatorComponent { readonly refreshing = input(false); }
