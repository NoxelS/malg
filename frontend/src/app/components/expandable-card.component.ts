import {ChangeDetectionStrategy, Component, ViewEncapsulation, input} from '@angular/core';

@Component({
  selector: 'app-expandable-card',
  template: '<details [id]="cardId()" [open]="open()"><summary class="expandable-card__summary"><ng-content select="[expandable-card-summary]" /></summary><div class="expandable-card__body"><ng-content select="[expandable-card-body]" /></div></details>',
  styles: ['app-expandable-card details { background: var(--tui-background-base); border: 1px solid var(--tui-border-normal); border-radius: var(--tui-radius-l); box-shadow: var(--tui-shadow-small); overflow: hidden; padding: 0; } app-expandable-card .expandable-card__summary { align-items: flex-start; cursor: pointer; display: flex; gap: 1rem; justify-content: space-between; list-style: none; padding: 1.5rem; } app-expandable-card .expandable-card__summary::-webkit-details-marker { display: none; } app-expandable-card .expandable-card__summary::after { color: var(--tui-text-action); content: \'＋\'; flex: 0 0 auto; font-size: 1.25rem; line-height: 1; } app-expandable-card details[open] > .expandable-card__summary::after { content: \'−\'; } app-expandable-card .expandable-card__body { border-block-start: 1px solid var(--tui-border-normal); overflow: hidden; }'],
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ExpandableCardComponent { readonly cardId = input<string | null>(null); readonly open = input(false); }

@Component({
  selector: 'app-detail-disclosure',
  template: '<details [class.detail-disclosure--wide]="wide()" [open]="open()"><summary>{{ title() }}</summary><ng-content /></details>',
  styles: ['app-detail-disclosure details { border: 1px solid var(--tui-border-normal); border-radius: var(--tui-radius-m); min-width: 0; padding: 1rem 1.25rem; } app-detail-disclosure details.detail-disclosure--wide { grid-column: span 2; } app-detail-disclosure summary { align-items: center; cursor: pointer; display: flex; font: var(--tui-font-heading-6); justify-content: space-between; list-style: none; } app-detail-disclosure summary::-webkit-details-marker { display: none; } app-detail-disclosure summary::after { color: var(--tui-text-action); content: \'＋\'; flex: 0 0 auto; font-size: 1.25rem; line-height: 1; } app-detail-disclosure details[open] > summary::after { content: \'−\'; } app-detail-disclosure details > p, app-detail-disclosure details > ul, app-detail-disclosure details > dl, app-detail-disclosure details > app-chip-list { margin-block: 1rem 0; } app-detail-disclosure ul { margin: .5rem 0 0; padding-inline-start: 1.25rem; } app-detail-disclosure li { color: var(--tui-text-secondary); font: var(--tui-font-text-m); line-height: 1.5; margin: .5rem 0 0; } app-detail-disclosure dl { display: grid; gap: .5rem; } app-detail-disclosure dl div { display: flex; gap: .5rem; justify-content: space-between; } app-detail-disclosure dt, app-detail-disclosure dd { font: var(--tui-font-text-s); margin: 0; } app-detail-disclosure dt { color: var(--tui-text-tertiary); } app-detail-disclosure dd { color: var(--tui-text-secondary); text-align: end; } @media (max-width: 48rem) { app-detail-disclosure details.detail-disclosure--wide { grid-column: auto; } app-detail-disclosure dd { text-align: start; } }'],
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DetailDisclosureComponent { readonly title = input.required<string>(); readonly wide = input(false); readonly open = input(false); }
