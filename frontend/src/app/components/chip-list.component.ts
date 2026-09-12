import {ChangeDetectionStrategy, Component, input} from '@angular/core';

@Component({
  selector: 'app-chip-list',
  template: '<div class="chip-list" [class.chip-list--end]="align() === \'end\'"><ng-content /></div>',
  styles: [':host { display: block; } .chip-list { display: flex; flex-wrap: wrap; gap: .5rem; margin-block-start: .65rem; } .chip-list--end { justify-content: flex-end; }'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ChipListComponent { readonly align = input<'start' | 'end'>('start'); }
