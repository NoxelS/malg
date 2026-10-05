import {ChangeDetectionStrategy, Component, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {TuiBadge} from '@taiga-ui/kit';
import {ICellRendererAngularComp} from 'ag-grid-angular';
import {ICellRendererParams} from 'ag-grid-community';
import {WorkerOverviewItem} from '../api-service';

/** Render worker liveness as a compact semantic badge. */
@Component({
  selector: 'app-worker-status-cell', imports: [TuiBadge], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<span tuiBadge [appearance]="status() === 'running' ? 'positive' : 'neutral'">{{ status() }}</span>`,
  styles: `:host { display: flex; height: 100%; align-items: center; } span { text-transform: capitalize; }`,
})
export class WorkerStatusCell implements ICellRendererAngularComp {
  protected readonly status = signal<'idle' | 'running'>('idle');
  agInit(params: ICellRendererParams<WorkerOverviewItem>): void { this.status.set(params.data?.status ?? 'idle'); }
  refresh(params: ICellRendererParams<WorkerOverviewItem>): boolean { this.agInit(params); return true; }
}

/** Link the current job while keeping idle rows as plain text. */
@Component({
  selector: 'app-worker-job-cell', imports: [RouterLink], changeDetection: ChangeDetectionStrategy.OnPush,
  template: `@if (job(); as current) { <a [routerLink]="['/jobs', current.job_id]" [title]="current.job_id">{{ current.job_id }}</a> } @else { <span>No job claimed</span> }`,
  styles: `:host { display: flex; height: 100%; align-items: center; min-width: 0; } a, span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } a { color: var(--tui-text-action); } a:focus-visible { outline: 2px solid var(--tui-text-action); }`,
})
export class WorkerJobCell implements ICellRendererAngularComp {
  protected readonly job = signal<WorkerOverviewItem['job']>(null);
  agInit(params: ICellRendererParams<WorkerOverviewItem>): void { this.job.set(params.data?.job ?? null); }
  refresh(params: ICellRendererParams<WorkerOverviewItem>): boolean { this.agInit(params); return true; }
}
