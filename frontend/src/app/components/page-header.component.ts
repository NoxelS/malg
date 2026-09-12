import {ChangeDetectionStrategy, Component, input} from '@angular/core';

@Component({
  selector: 'app-page-header',
  template: '<header class="page-header"><div class="page-header__copy"><p class="page-header__eyebrow">{{ eyebrow() }}</p><h1>{{ title() }}</h1><p class="page-header__description">{{ description() }}</p></div><div class="page-header__actions"><ng-content select="[page-header-actions]" /></div></header>',
  styles: [':host { display: block; } .page-header { align-items: flex-start; display: flex; gap: 2rem; justify-content: space-between; margin-block-end: 2.5rem; } .page-header__copy { min-width: 0; } .page-header__eyebrow { color: var(--tui-text-action); font: var(--tui-font-text-s); font-weight: 700; letter-spacing: .08em; margin: 0 0 .5rem; text-transform: uppercase; } h1 { color: var(--tui-text-primary); font: var(--tui-font-heading-3); margin: 0; } .page-header__description { color: var(--tui-text-secondary); font: var(--tui-font-text-m); margin: .75rem 0 0; } .page-header__actions:empty { display: none; } @media (max-width: 48rem) { .page-header { flex-direction: column; } .page-header__actions { align-self: stretch; } .page-header__actions > * { width: 100%; } }'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PageHeaderComponent { readonly eyebrow = input.required<string>(); readonly title = input.required<string>(); readonly description = input.required<string>(); }
