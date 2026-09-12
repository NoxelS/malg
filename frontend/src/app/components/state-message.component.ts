import {ChangeDetectionStrategy, Component, input} from '@angular/core';

@Component({
  selector: 'app-state-message',
  template: '<p class="state-message" [class.state-message--error]="tone() === \'error\'" [attr.role]="tone() === \'error\' ? \'alert\' : \'status\'">{{ message() }}</p>',
  styles: [':host { display: block; } .state-message { background: var(--tui-background-neutral-1); color: var(--tui-text-secondary); margin: 0; padding: 2rem; text-align: center; } .state-message--error { color: var(--tui-text-negative); }'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StateMessageComponent { readonly message = input.required<string>(); readonly tone = input<'default' | 'error'>('default'); }
