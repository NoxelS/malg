import {ChangeDetectionStrategy, Component} from '@angular/core';

@Component({
  selector: 'app-page-layout',
  template: '<div class="page-layout"><ng-content /></div>',
  styles: [':host { display: block; } .page-layout { margin: 0 auto; max-width: 96rem; padding: 3rem clamp(1.5rem, 4vw, 4rem); } @media (max-width: 48rem) { .page-layout { padding-block: 2rem; } }'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PageLayoutComponent {}
