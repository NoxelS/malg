import {HttpParams} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {Router, RouterLink} from '@angular/router';
import {AgGridAngular} from 'ag-grid-angular';
import {ColDef, GridApi, GridReadyEvent, IDatasource, IGetRowsParams, RowClickedEvent} from 'ag-grid-community';
import {TuiButton, TuiInput} from '@taiga-ui/core';
import {TuiSelect, TuiToastDirective} from '@taiga-ui/kit';
import {AccountResearchController, ApiService, CrmListItem, DashboardJobDuration, DashboardSummary, WorkerOverviewItem, WorkerSummary} from './api-service';
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
  imports: [AgGridAngular, RouterLink, FormsModule, TuiButton, TuiInput, TuiSelect, TuiToastDirective, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent],
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
  protected readonly controller = signal<AccountResearchController | null>(null);
  protected readonly controllerError = signal('');
  protected readonly controllerBusy = signal(false);
  protected readonly configuringController = signal(false);
  protected readonly campaigns = signal<readonly CrmListItem[]>([]);
  protected readonly icps = signal<readonly CrmListItem[]>([]);
  protected controllerCampaign = '';
  protected controllerIcp = '';
  protected controllerCompanies = 5;
  protected controllerPeople = 2;
  protected controllerOpportunities = 1;
  protected readonly controllerLabel = (id: string): string => [...this.campaigns(), ...this.icps()].find((item) => item.id === id)?.display_name ?? 'Select a scope';
  protected readonly controllerCampaignIds = (): string[] => ['', ...this.campaigns().map((item) => item.id)];
  protected readonly controllerIcpIds = (): string[] => ['', ...this.icps().map((item) => item.id)];
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
    this.loadController();
  }

  protected onGridReady(event: GridReadyEvent<WorkerOverviewItem>): void {
    this.gridApi = event.api;
    event.api.setGridOption('datasource', this.datasource);
  }

  protected refresh(): void {
    this.loadSummary(true);
    this.loadController();
    ++this.generation;
    this.gridApi?.purgeInfiniteCache();
  }

  protected openControllerConfiguration(): void {
    const configuration = this.controller()?.configuration;
    this.controllerCampaign = configuration?.campaign_id ?? ''; this.controllerIcp = configuration?.icp_id ?? '';
    this.controllerCompanies = configuration?.company_count ?? 5; this.controllerPeople = configuration?.people_per_company ?? 2; this.controllerOpportunities = configuration?.opportunities_per_company ?? 1;
    this.configuringController.set(true); this.loadControllerScopes();
  }
  protected closeControllerConfiguration(): void { this.configuringController.set(false); this.controllerError.set(''); }
  protected controllerCampaignChanged(): void { this.controllerIcp = ''; this.icps.set([]); if (this.controllerCampaign) this.api.listCrmIcps(this.controllerCampaign).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: (page) => this.icps.set(page.items), error: () => this.controllerError.set('ICPs could not be loaded.')}); }
  protected saveControllerConfiguration(): void {
    if (this.controllerBusy() || !this.controllerCampaign || !this.controllerIcp || !this.validControllerCounts()) return;
    this.controllerBusy.set(true); this.controllerError.set('');
    this.api.saveAccountResearchController({campaign_id: this.controllerCampaign, icp_id: this.controllerIcp, company_count: this.controllerCompanies, people_per_company: this.controllerPeople, opportunities_per_company: this.controllerOpportunities}).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: (controller) => { this.controller.set(controller); this.controllerBusy.set(false); this.configuringController.set(false); }, error: () => { this.controllerBusy.set(false); this.controllerError.set('The configuration could not be saved. Check the selected Twenty scope.'); }});
  }
  protected setControllerEnabled(enabled: boolean): void {
    const controller = this.controller(); if (!controller || this.controllerBusy()) return;
    this.controllerBusy.set(true); this.controllerError.set('');
    this.api.setAccountResearchControllerState(enabled, controller.revision).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: (value) => { this.controller.set(value); this.controllerBusy.set(false); }, error: () => { this.controllerBusy.set(false); this.controllerError.set('The controller state could not be changed. Refresh and try again.'); this.loadController(); }});
  }
  protected validControllerCounts(): boolean { return Number.isInteger(this.controllerCompanies) && this.controllerCompanies >= 1 && this.controllerCompanies <= 20 && Number.isInteger(this.controllerPeople) && this.controllerPeople >= 1 && this.controllerPeople <= 5 && Number.isInteger(this.controllerOpportunities) && this.controllerOpportunities >= 1 && this.controllerOpportunities <= 3; }
  private loadController(): void { this.api.getAccountResearchController().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: (controller) => this.controller.set(controller), error: () => this.controllerError.set('Continuous research status could not be loaded.')}); }
  private loadControllerScopes(): void { this.api.listCrmCampaigns().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: (page) => this.campaigns.set(page.items), error: () => this.controllerError.set('Campaigns could not be loaded.')}); if (this.controllerCampaign) this.api.listCrmIcps(this.controllerCampaign).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: (page) => this.icps.set(page.items), error: () => this.controllerError.set('ICPs could not be loaded.')}); }

  protected formatAverageDuration(seconds: number | null): string {
    return seconds === null ? 'No successful jobs yet' : this.formatDuration(Math.round(seconds));
  }

  protected formatSearchDate(value: string | null): string {
    return formatDate({value});
  }

  protected searchFailureLabel(reason: string | null): string {
    const labels: Record<string, string> = {
      search_captcha: 'a CAPTCHA challenge', search_rate_limited: 'provider rate limiting',
      search_provider_blocked: 'blocked provider access', search_provider_failure: 'a provider failure',
      search_http_error: 'an HTTP failure', search_transport_error: 'a connection failure',
      search_parser_failure: 'an invalid search response',
    };
    return labels[reason ?? ''] ?? 'a search outage';
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
