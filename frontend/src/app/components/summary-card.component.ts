import {ChangeDetectionStrategy, Component, input} from '@angular/core';

@Component({
  selector: 'app-summary-card',
  template: '<article class="summary-card"><span class="summary-card__label">{{ label() }}</span><strong class="summary-card__value">{{ value() }}@if (suffix()) {<small>{{ suffix() }}</small>}</strong><span class="summary-card__hint">{{ hint() }}</span></article>',
  styles: [':host { display: block; } .summary-card { background: var(--tui-background-base); border: 1px solid var(--tui-border-normal); border-radius: var(--tui-radius-l); box-shadow: var(--tui-shadow-small); display: flex; flex-direction: column; min-height: 8rem; padding: 1.5rem; } .summary-card__label { color: var(--tui-text-action); font: var(--tui-font-text-s); font-weight: 700; letter-spacing: .08em; margin: 0 0 .5rem; text-transform: uppercase; } .summary-card__value { color: var(--tui-text-primary); font: var(--tui-font-heading-4); font-size: 3.5rem; line-height: 1; margin-block-start: auto; } .summary-card__value small { color: var(--tui-text-secondary); font: var(--tui-font-text-m); font-size: 1.25rem; } .summary-card__hint { color: var(--tui-text-secondary); font: var(--tui-font-text-m); margin: .75rem 0 0; }'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SummaryCardComponent { readonly label = input.required<string>(); readonly value = input.required<string | number>(); readonly hint = input.required<string>(); readonly suffix = input<string | null>(null); }
