import {ChangeDetectionStrategy, Component, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
import {ICellRendererAngularComp} from 'ag-grid-angular';
import {ICellRendererParams} from 'ag-grid-community';
import {MemoryOverviewItem} from '../api-service';

/** Show a readable two-line preview and a keyboard-accessible detail link. */
@Component({
  selector: 'app-memory-preview-cell', imports: [RouterLink], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `@if (memory(); as item) { <a [routerLink]="['/memory', item.id]" [title]="item.content_preview"><strong>{{ item.content_preview || 'Empty memory' }}</strong><span>{{ item.id }}</span></a> }`,
  styles: `:host { display: flex; height: 100%; align-items: center; } a { display: grid; gap: .25rem; line-height: 1.4; min-width: 0; text-decoration: none; color: var(--tui-text-primary); } strong { font-weight: 500; white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; } span { color: var(--tui-text-secondary); font-size: .7rem; font-family: monospace; overflow: hidden; text-overflow: ellipsis; } a:focus-visible { outline: 2px solid var(--tui-text-action); }`,
})
export class MemoryPreviewCell implements ICellRendererAngularComp {
  protected readonly memory = signal<MemoryOverviewItem | undefined>(undefined);
  agInit(params: ICellRendererParams<MemoryOverviewItem>): void { this.memory.set(params.data); }
  refresh(params: ICellRendererParams<MemoryOverviewItem>): boolean { this.agInit(params); return true; }
}

/** Distinguish memory type and archived state without raw boolean columns. */
@Component({
  selector: 'app-memory-type-cell', imports: [TuiBadge], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `@if (memory(); as item) { <span tuiBadge appearance="neutral">{{ item.type }}</span>@if (item.archived) { <small>Archived</small> } }`,
  styles: `:host { display: flex; flex-direction: column; justify-content: center; align-items: start; gap: .25rem; height: 100%; line-height: 1.3; } span { text-transform: capitalize; } small { color: var(--tui-text-secondary); }`,
})
export class MemoryTypeCell implements ICellRendererAngularComp {
  protected readonly memory = signal<MemoryOverviewItem | undefined>(undefined);
  agInit(params: ICellRendererParams<MemoryOverviewItem>): void { this.memory.set(params.data); }
  refresh(params: ICellRendererParams<MemoryOverviewItem>): boolean { this.agInit(params); return true; }
}

type ActionParams = ICellRendererParams<MemoryOverviewItem> & {
  isBusy: () => boolean;
  deleteMemory: (memory: MemoryOverviewItem) => void;
};

/** Delete a single entry without triggering the surrounding row navigation. */
@Component({
  selector: 'app-memory-actions-cell', imports: [TuiButton], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<button tuiButton type="button" size="s" appearance="flat-destructive" [disabled]="disabled()" [attr.aria-label]="'Delete memory ' + (params?.data?.id ?? '')" title="Permanently delete this memory and its associations" (click)="$event.stopPropagation(); remove()">Delete</button>`,
  styles: `:host { display: flex; align-items: center; height: 100%; }`,
})
export class MemoryActionsCell implements ICellRendererAngularComp {
  protected params?: ActionParams;
  protected readonly disabled = signal(true);
  agInit(params: ActionParams): void { this.params = params; this.disabled.set(!params.data || params.isBusy()); }
  refresh(params: ActionParams): boolean { this.agInit(params); return true; }
  protected remove(): void { if (!this.disabled() && this.params?.data) this.params.deleteMemory(this.params.data); }
}
