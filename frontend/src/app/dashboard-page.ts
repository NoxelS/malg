import {HttpParams} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {Router, RouterLink} from '@angular/router';
import {AgGridAngular} from 'ag-grid-angular';
import {ColDef, GridApi, GridReadyEvent, IDatasource, IGetRowsParams, RowClickedEvent} from 'ag-grid-community';
import {TuiButton} from '@taiga-ui/core';
import {TuiToastDirective} from '@taiga-ui/kit';
import {ApiService, DashboardJobDuration, DashboardSummary, WorkerOverviewItem, WorkerSummary} from './api-service';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {SectionHeadingComponent} from './components/section-heading.component';
import {StateMessageComponent} from './components/state-message.component';
import {overviewGridTheme, registerOverviewGridModules} from './components/overview-grid.config';
import {WorkerJobCell, WorkerStatusCell} from './components/worker-grid-cells';

registerOverviewGridModules();

const formatDate = (params: {value: string | null | undefined}): string => params.value
  ? new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(params.value))
  : '—';

@Component({
  selector: 'app-dashboard-page',
  imports: [AgGridAngular, RouterLink, TuiButton, TuiToastDirective, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent],
  templateUrl: './dashboard-page.html',
  styleUrl: './dashboard-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DashboardPage implements OnInit {
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly router = inject(Router);
  private gridApi: GridApi<WorkerOverviewItem> | null = null;
  private generation = 0;

  protected readonly summary = signal<DashboardSummary | null>(null);
  protected readonly loading = signal(true);
  protected readonly refreshing = signal(false);
  protected readonly error = signal(false);
  protected readonly gridError = signal(false);
  protected readonly gridTheme = overviewGridTheme;
  protected readonly defaultColDef: ColDef<WorkerOverviewItem> = {resizable: true, sortable: false, minWidth: 110};
  protected readonly columnDefs: ColDef<WorkerOverviewItem>[] = [
    {field: 'worker_id', headerName: 'Worker', minWidth: 190, flex: 1, valueFormatter: ({value}) => value ? value.slice(0, 8) : '—', tooltipValueGetter: ({value}) => value ?? ''},
    {field: 'status', headerName: 'Status', width: 120, sortable: true, cellRenderer: WorkerStatusCell},
    {colId: 'current_job', headerName: 'Current job', minWidth: 225, flex: 2, cellRenderer: WorkerJobCell},
    {colId: 'kind', headerName: 'Kind', width: 115, sortable: true, valueGetter: ({data}) => data?.job?.kind ?? '—'},
    {colId: 'attempt_count', headerName: 'Attempt', width: 105, sortable: true, valueGetter: ({data}) => data?.job?.attempt_count ?? '—'},
    {colId: 'claimed_at', headerName: 'Claimed', minWidth: 180, sortable: true, valueGetter: ({data}) => data?.job?.claimed_at ?? null, valueFormatter: formatDate},
    {field: 'running_for_seconds', headerName: 'Running for', minWidth: 125, valueFormatter: ({value}) => value === null || value === undefined ? '—' : this.formatDuration(value)},
    {field: 'online_since', headerName: 'Online since', minWidth: 180, sortable: true, valueFormatter: formatDate},
    {field: 'last_seen_at', headerName: 'Last check-in', minWidth: 180, sortable: true, valueFormatter: formatDate},
  ];
  protected readonly getRowId = (params: {data: WorkerOverviewItem}): string => params.data.worker_id;
  private readonly datasource: IDatasource = {
    getRows: (params: IGetRowsParams<WorkerOverviewItem>) => this.loadRows(params),
  };

  ngOnInit(): void {
    this.loadSummary();
  }

  protected onGridReady(event: GridReadyEvent<WorkerOverviewItem>): void {
    this.gridApi = event.api;
    event.api.setGridOption('datasource', this.datasource);
  }

  protected refresh(): void {
    this.loadSummary(true);
    ++this.generation;
    this.gridApi?.purgeInfiniteCache();
  }

  protected formatAverageDuration(seconds: number | null): string {
    return seconds === null ? 'No successful jobs yet' : this.formatDuration(Math.round(seconds));
  }

  protected durationLabel(duration: DashboardJobDuration): string {
    return duration.kind === 'icp' ? 'ICP' : duration.kind.charAt(0).toUpperCase() + duration.kind.slice(1);
  }

  protected openJob(event: RowClickedEvent<WorkerOverviewItem>): void {
    const target = event.event?.target as HTMLElement | null;
    if (target?.closest('a, button')) return;
    if (event.data?.job) void this.router.navigate(['/jobs', event.data.job.job_id]);
  }

  private loadSummary(refreshing = false): void {
    if (refreshing) this.refreshing.set(true);
    else this.loading.set(true);
    this.error.set(false);
    this.api.listDashboard().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (summary) => {
        this.summary.set(summary);
        this.loading.set(false);
        this.refreshing.set(false);
      },
      error: () => {
        this.error.set(true);
        this.loading.set(false);
        this.refreshing.set(false);
      },
    });
  }

  private loadRows(params: IGetRowsParams<WorkerOverviewItem>): void {
    const generation = this.generation;
    let query = new HttpParams()
      .set('offset', String(params.startRow))
      .set('limit', String(Math.min(params.endRow - params.startRow, 100)));
    const sort = params.sortModel[0];
    const sortMap: Record<string, string> = {
      status: 'status', last_seen_at: 'last_seen_at', online_since: 'online_since',
      claimed_at: 'claimed_at', kind: 'kind', attempt_count: 'attempt_count',
    };
    const requestedSort = sort?.colId === 'current_job' ? undefined : sortMap[sort?.colId ?? ''];
    if (requestedSort) {
      query = query.set('sort', requestedSort);
      if (sort?.sort === 'asc' || sort?.sort === 'desc') query = query.set('direction', sort.sort);
    }
    this.gridError.set(false);
    this.api.listWorkerOverview(query).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (page) => {
        if (generation !== this.generation) return;
        const rows = page.items.map((worker: WorkerSummary): WorkerOverviewItem => ({
          ...worker,
          running_for_seconds: worker.job ? Math.max(0, Math.floor((Date.now() - Date.parse(worker.job.claimed_at)) / 1000)) : null,
        }));
        params.successCallback(rows, page.total);
      },
      error: () => {
        if (generation !== this.generation) return;
        this.gridError.set(true);
        params.failCallback();
      },
    });
  }

  private formatDuration(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ${minutes % 60}m`;
    return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  }
}
