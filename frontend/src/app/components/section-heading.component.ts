import {ChangeDetectionStrategy, Component, input} from '@angular/core';

@Component({
  selector: 'app-section-heading',
  template: '<div class="section-heading"><div><h2 [id]="headingId()">{{ title() }}</h2><p>{{ description() }}</p></div><div class="section-heading__action"><ng-content select="[section-heading-action]" /></div></div>',
  styles: [':host { display: block; } .section-heading { align-items: flex-start; display: flex; justify-content: space-between; margin-block-end: 1.5rem; } h2 { color: var(--tui-text-primary); font: var(--tui-font-heading-5); margin: 0; } p { color: var(--tui-text-secondary); font: var(--tui-font-text-m); margin: .75rem 0 0; } .section-heading__action:empty { display: none; }'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SectionHeadingComponent { readonly headingId = input.required<string>(); readonly title = input.required<string>(); readonly description = input.required<string>(); }
