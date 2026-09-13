import {DatePipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, computed, input} from '@angular/core';

import {AgentTurn, JsonValue} from '../api-service';

interface JsonRecord {
  readonly [key: string]: JsonValue;
}

const asRecord = (value: JsonValue | undefined): JsonRecord | null =>
  value !== null && !Array.isArray(value) && typeof value === 'object' ? value : null;

const asRecords = (value: JsonValue | undefined): readonly JsonRecord[] =>
  Array.isArray(value) ? value.flatMap((item) => {
    const record = asRecord(item);
    return record === null ? [] : [record];
  }) : [];

interface PromptMessage {
  readonly role: string;
  readonly content: string | null;
}
@Component({
  selector: 'app-trace-turn-card',
  imports: [DatePipe],
  template: `
    <details class="trace-turn">
      <summary class="trace-turn__summary">
        <span class="trace-turn__command"><span aria-hidden="true">$</span><strong>{{ turn().method_name }}</strong><small>{{ turn().strategy }}</small></span>
        <span class="trace-turn__facts">
          <span>{{ messageCount() }} messages</span>
          @if (toolNames().length) { <span>{{ toolNames().join(', ') }}</span> }
          <span>{{ duration() }}</span>
          <span class="trace-turn__status" [class.trace-turn__status--failed]="turn().success === false">{{ status() }}</span>
        </span>
      </summary>
      <div class="trace-turn__body">
        <dl class="trace-turn__metadata">
          <div><dt>Turn</dt><dd>{{ turn().turn_number }}</dd></div>
          <div><dt>Generation</dt><dd>{{ turn().generation_id }}</dd></div>
          <div><dt>Started</dt><dd>{{ turn().started_at | date:'medium' }}</dd></div>
          <div><dt>Finished</dt><dd>{{ turn().finished_at ? (turn().finished_at | date:'medium') : '—' }}</dd></div>
          <div><dt>Prompt</dt><dd>{{ messageCount() }} messages</dd></div>
          <div><dt>Tools</dt><dd>{{ toolNames().length ? toolNames().join(', ') : 'None' }}</dd></div>
          @if (finishReason()) { <div><dt>Finish reason</dt><dd>{{ finishReason() }}</dd></div> }
        </dl>

        @if (responsePreview(); as preview) { <p class="trace-turn__response">{{ preview }}</p> }
        @if (turn().error_type || turn().error_message || turn().error_traceback) {
          <div class="trace-turn__failure"><strong>{{ turn().error_type ?? 'Turn failed' }}</strong><p>{{ turn().error_message ?? '—' }}</p>@if (turn().error_traceback) { <p class="trace-turn__traceback">{{ turn().error_traceback }}</p> }</div>
        }

        <section class="trace-turn__messages" aria-label="Prompt messages">
          <h4>Prompt messages</h4>
          @for (message of promptMessages(); track $index) {
            <article><span>{{ message.role }}</span><p>{{ message.content ?? 'Structured message content' }}</p></article>
          }
        </section>
      </div>
    </details>
  `,
  styles: [`
    :host { display: block; }
    .trace-turn { border: 1px solid var(--tui-border-normal); border-radius: var(--tui-radius-m); min-width: 0; }
    .trace-turn__summary { align-items: center; cursor: pointer; display: flex; flex-wrap: wrap; gap: .5rem 1rem; justify-content: space-between; list-style: none; padding: .7rem .9rem; }
    .trace-turn__summary::-webkit-details-marker { display: none; }
    .trace-turn__summary::before { color: var(--tui-text-action); content: '›'; font-size: 1.25rem; line-height: 1; }
    .trace-turn[open] > .trace-turn__summary::before { content: '⌄'; }
    .trace-turn__command { align-items: baseline; display: flex; flex: 1 1 15rem; gap: .5rem; min-width: 0; }
    .trace-turn__command > span { color: var(--tui-text-action); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .trace-turn__command strong { color: var(--tui-text-primary); font: var(--tui-font-text-m); overflow-wrap: anywhere; }
    .trace-turn__command small, .trace-turn__facts { color: var(--tui-text-tertiary); font: var(--tui-font-text-s); }
    .trace-turn__facts { align-items: center; display: flex; flex-wrap: wrap; gap: .35rem .75rem; }
    .trace-turn__status { color: var(--tui-text-positive); font-weight: 700; text-transform: uppercase; }
    .trace-turn__status--failed { color: var(--tui-text-negative); }
    .trace-turn__body { border-block-start: 1px solid var(--tui-border-normal); display: grid; gap: .85rem; padding: .9rem; }
    .trace-turn__metadata { display: flex; flex-wrap: wrap; gap: .6rem 1.25rem; margin: 0; }
    .trace-turn__metadata div { min-width: 0; }
    dt { color: var(--tui-text-tertiary); font: var(--tui-font-text-s); }
    dd { color: var(--tui-text-secondary); font: var(--tui-font-text-s); margin: .15rem 0 0; max-width: 18rem; overflow-wrap: anywhere; user-select: text; }
    .trace-turn__response { border-inline-start: .2rem solid var(--tui-text-action); color: var(--tui-text-primary); margin: 0; overflow-wrap: anywhere; padding-inline-start: .75rem; white-space: pre-wrap; }
    .trace-turn__failure { background: var(--tui-background-negative); color: var(--tui-text-negative); overflow-wrap: anywhere; padding: .75rem; }
    .trace-turn__failure p { margin: .4rem 0; white-space: pre-wrap; }
    .trace-turn__traceback { color: var(--tui-text-secondary); font: var(--tui-font-text-s); }
    .trace-turn__messages { border-block-start: 1px solid var(--tui-border-normal); display: grid; gap: .5rem; padding-block-start: .65rem; }
    .trace-turn__messages h4 { color: var(--tui-text-secondary); font: var(--tui-font-text-s); margin: 0; text-transform: uppercase; }
    .trace-turn__messages article { align-items: baseline; display: grid; gap: .4rem .75rem; grid-template-columns: 5rem minmax(0, 1fr); }
    .trace-turn__messages span { color: var(--tui-text-action); font: var(--tui-font-text-s); font-weight: 700; text-transform: uppercase; }
    .trace-turn__messages p { color: var(--tui-text-primary); margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
    @media (max-width: 48rem) { .trace-turn__summary { align-items: flex-start; flex-wrap: nowrap; } .trace-turn__facts { justify-content: flex-start; } .trace-turn__messages article { grid-template-columns: 1fr; gap: .15rem; } }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TraceTurnCardComponent {
  readonly turn = input.required<AgentTurn>();
  readonly messageCount = computed(() => this.turn().request_messages.length);
  readonly promptMessages = computed<readonly PromptMessage[]>(() => this.turn().request_messages.map((message) => {
    const record = asRecord(message);
    const role = record?.['role'];
    const content = record?.['content'];
    return {
      role: typeof role === 'string' ? role : 'unknown',
      content: typeof content === 'string' ? content : null,
    };
  }));
  readonly response = computed(() => asRecord(this.turn().response));
  readonly toolNames = computed(() => asRecords(this.response()?.['tool_calls']).flatMap((call) => {
    const functionCall = asRecord(call['function']);
    const name = call['name'] ?? functionCall?.['name'];
    return typeof name === 'string' ? [name] : [];
  }));
  readonly finishReason = computed(() => {
    const value = this.response()?.['finish_reason'];
    return typeof value === 'string' ? value : null;
  });
  readonly responsePreview = computed(() => {
    const value = this.response()?.['content'] ?? asRecord(this.response()?.['assistant_message'])?.['content'] ?? this.turn().response;
    return typeof value === 'string' && value ? value : null;
  });
  readonly status = computed(() => this.turn().success === false ? 'failed' : this.turn().success === true ? 'done' : 'running');
  readonly duration = computed(() => {
    const finishedAt = this.turn().finished_at;
    if (!finishedAt) return 'running';
    const milliseconds = Date.parse(finishedAt) - Date.parse(this.turn().started_at);
    if (!Number.isFinite(milliseconds) || milliseconds < 0) return '—';
    if (milliseconds < 1000) return `${milliseconds}ms`;
    if (milliseconds < 60_000) return `${Math.round(milliseconds / 1000)}s`;
    return `${Math.floor(milliseconds / 60_000)}m ${Math.round((milliseconds % 60_000) / 1000)}s`;
  });
}
