import {ChangeDetectionStrategy, Component, computed, input} from '@angular/core';

import {JsonValue} from '../api-service';

interface TraceProperty {
  readonly path: string;
  readonly value: string;
}

const flatten = (value: JsonValue, path = 'Value'): readonly TraceProperty[] => {
  if (Array.isArray(value)) {
    if (!value.length) return [{path, value: '[]'}];
    return value.flatMap((item, index) => flatten(item, `${path}[${index}]`));
  }
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value);
    if (!entries.length) return [{path, value: '{}'}];
    return entries.flatMap(([key, item]) => flatten(item, path === 'Value' ? key : `${path}.${key}`));
  }
  return [{path, value: value === null ? '—' : String(value)}];
};

@Component({
  selector: 'app-trace-property-list',
  template: `
    <dl class="trace-properties">
      @for (property of properties(); track property.path) {
        <div><dt>{{ property.path }}</dt><dd>{{ property.value }}</dd></div>
      }
    </dl>
  `,
  styles: [`
    :host { display: block; min-width: 0; }
    .trace-properties { display: grid; gap: .65rem; margin: 0; }
    .trace-properties > div { align-items: baseline; display: grid; gap: .5rem 1rem; grid-template-columns: minmax(9rem, 28%) minmax(0, 1fr); min-width: 0; }
    dt { color: var(--tui-text-tertiary); font: var(--tui-font-text-s); overflow-wrap: anywhere; }
    dd { color: var(--tui-text-primary); font: var(--tui-font-text-s); margin: 0; overflow-wrap: anywhere; user-select: text; white-space: pre-wrap; }
    @media (max-width: 48rem) { .trace-properties > div { grid-template-columns: 1fr; gap: .15rem; } }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TracePropertyListComponent {
  readonly value = input.required<JsonValue>();
  protected readonly properties = computed(() => flatten(this.value()));
}
