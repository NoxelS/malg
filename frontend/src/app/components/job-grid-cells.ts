import {ChangeDetectionStrategy, Component, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
import {ICellRendererAngularComp} from 'ag-grid-angular';
import {ICellRendererParams} from 'ag-grid-community';
import {JobOverviewItem} from '../api-service';

/** Show the research kind and a keyboard-accessible link to its full identity. */
@Component({
  selector: 'app-job-identity-cell', imports: [RouterLink], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `@if (job(); as item) { <a [routerLink]="['/jobs', item.job_id]" [title]="item.job_id"><strong>{{ item.kind }} research</strong><span>{{ item.job_id }}</span></a> }`,
  styles: `:host { display: flex; height: 100%; align-items: center; } a { display: grid; line-height: 1.5; min-width: 0; text-decoration: none; color: var(--tui-text-primary); } strong { text-transform: capitalize; font-weight: 600; } span { color: var(--tui-text-secondary); font-size: .75rem; font-family: monospace; overflow: hidden; text-overflow: ellipsis; } a:focus-visible { outline: 2px solid var(--tui-text-action); }`,
})
export class JobIdentityCell implements ICellRendererAngularComp {
  protected readonly job = signal<JobOverviewItem | undefined>(undefined);
  agInit(params: ICellRendererParams<JobOverviewItem>): void { this.job.set(params.data); }
  refresh(params: ICellRendererParams<JobOverviewItem>): boolean { this.agInit(params); return true; }
}

/** Render lifecycle state with a readable label and semantic Taiga badge. */
@Component({
  selector: 'app-job-status-cell', imports: [TuiBadge], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `@if (status()) { <span tuiBadge [appearance]="appearance()">{{ status() }}</span> }`,
  styles: `:host { display: flex; align-items: center; height: 100%; } span { text-transform: capitalize; }`,
})
export class JobStatusCell implements ICellRendererAngularComp {
  protected readonly status = signal('');
  protected readonly appearance = signal('neutral');
  agInit(params: ICellRendererParams<JobOverviewItem>): void {
    const status = params.data?.status ?? '';
    this.status.set(status);
    this.appearance.set(status === 'failed' ? 'negative' : status === 'succeeded' ? 'positive' : status === 'running' ? 'info' : 'neutral');
  }
  refresh(params: ICellRendererParams<JobOverviewItem>): boolean { this.agInit(params); return true; }
}

type ActionParams = ICellRendererParams<JobOverviewItem> & {
  isBusy: () => boolean;
  deleteJob: (job: JobOverviewItem) => void;
};

/** Keep destructive row actions separate from row navigation and active work. */
@Component({
  selector: 'app-job-actions-cell', imports: [TuiButton], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<button tuiButton type="button" size="s" appearance="flat-destructive" [disabled]="disabled()" [attr.aria-label]="'Delete job ' + (params?.data?.job_id ?? '')" [title]="title()" (click)="$event.stopPropagation(); remove()">Delete</button>`,
  styles: `:host { display: flex; align-items: center; height: 100%; }`,
})
export class JobActionsCell implements ICellRendererAngularComp {
  protected params?: ActionParams;
  protected readonly disabled = signal(true);
  protected readonly title = signal('');
  agInit(params: ActionParams): void {
    this.params = params;
    const terminal = ['cancelled', 'succeeded', 'failed'].includes(params.data?.status ?? '');
    this.disabled.set(!terminal || params.isBusy());
    this.title.set(terminal ? 'Delete job history; keep research artifacts and CRM records' : 'Only finished jobs can be deleted');
  }
  refresh(params: ActionParams): boolean { this.agInit(params); return true; }
  protected remove(): void { if (!this.disabled() && this.params?.data) this.params.deleteJob(this.params.data); }
}
