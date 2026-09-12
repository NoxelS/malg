import {ChangeDetectionStrategy, Component} from '@angular/core';

@Component({
  selector: 'app-summary-grid',
  template: '<section class="summary-grid"><ng-content /></section>',
  styles: [':host { display: block; } .summary-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr)); margin-block-end: 3rem; } @media (max-width: 48rem) { .summary-grid { grid-template-columns: 1fr; } }'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SummaryGridComponent {}
