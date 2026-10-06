import {HttpParams} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, WritableSignal, computed, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {Router} from '@angular/router';
import {Observable, catchError, from, interval, map, mergeMap, of, toArray} from 'rxjs';
import {AgGridAngular} from 'ag-grid-angular';
import {ColDef, ModuleRegistry, RenderApiModule, RowApiModule, GridApi, GridReadyEvent, IDatasource, IGetRowsParams, RowClickedEvent} from 'ag-grid-community';
import {ApiService, CrmListItem, CrmPage, CrmStatus, JobKind, JobOverviewItem} from './api-service';
import {TuiButton, TuiCheckbox, TuiInput} from '@taiga-ui/core';
import {TuiSelect} from '@taiga-ui/kit';
import {JobActionsCell, JobIdentityCell, JobStatusCell} from './components/job-grid-cells';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';
import {AutoRefreshIndicatorComponent, AUTO_REFRESH_INTERVAL_MS} from './components/auto-refresh-indicator.component';

import {overviewGridTheme, registerOverviewGridModules} from './components/overview-grid.config';
type Selector = 'campaigns' | 'icps';
type LoadState = 'idle' | 'loading' | 'error';

const formatDate = (params: {value: string | null | undefined}): string => params.value ? new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(params.value)) : '—';

registerOverviewGridModules();
ModuleRegistry.registerModules([RenderApiModule, RowApiModule]);

@Component({selector: 'app-jobs-page', imports: [AgGridAngular, FormsModule, TuiButton, TuiCheckbox, TuiInput, TuiSelect, AutoRefreshIndicatorComponent, PageHeaderComponent, PageLayoutComponent, StateMessageComponent], changeDetection: ChangeDetectionStrategy.OnPush, templateUrl: './jobs-page.html', styleUrl: './jobs-page.scss'})
export class JobsPage implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private gridApi: GridApi<JobOverviewItem> | null = null;
  protected readonly deleting = signal(false);
  protected readonly deleteMessage = signal('');
  protected readonly shownJobs = signal<readonly JobOverviewItem[]>([]);
  protected readonly totalJobs = signal(0);
  protected readonly kindOptions = ['campaign', 'icp', 'account'];
  protected readonly outcomeOptions = ['', 'complete', 'partial', 'needs_review', 'insufficient_evidence', 'budget_exhausted'];
  protected readonly label = (value: string): string => value === 'icp' ? 'ICP' : value ? value.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase()) : 'All outcomes';
  protected readonly crmLabel = (id: string): string => [...this.campaigns(), ...this.icps()].find((item) => item.id === id)?.display_name ?? 'Select a scope';
  protected readonly campaignIds = (): string[] => ['', ...this.campaigns().map((item) => item.id)];
  protected readonly icpIds = (): string[] => ['', ...this.icps().map((item) => item.id)];
  private overviewGeneration = 0;
  private readonly overviewRequestCount = signal(0);
  protected readonly overviewLoading = computed(() => this.overviewRequestCount() > 0);
  protected readonly overviewHasLoaded = signal(false);
  protected readonly overviewError = signal(false);
  protected readonly crm = signal<CrmStatus | null>(null);
  protected readonly crmError = signal(false);
  protected readonly crmLoading = signal(false);
  protected readonly campaigns = signal<readonly CrmListItem[]>([]);
  protected readonly icps = signal<readonly CrmListItem[]>([]);
  protected readonly selectedKind = signal<JobKind>('account');
  protected selectedCampaign = '';
  protected selectedIcp = '';
  protected icpCount = 1;
  protected companyCount = 5;
  protected peoplePerCompany = 2;
  protected opportunitiesPerCompany = 1;
  protected readonly campaignCursor = signal<string | null>(null);
  protected readonly icpCursor = signal<string | null>(null);
  protected readonly selectorState = signal<Record<Selector, LoadState>>({campaigns: 'idle', icps: 'idle'});
  protected readonly enqueueError = signal('');
  protected readonly enqueuePending = signal(false);
  protected jobIdFilter = '';
  protected outcomeFilter = '';
  protected createdAfter = '';
  protected minimumAttempts: number | null = null;
  protected readonly selectedStatuses = signal<readonly string[]>([]);
  protected readonly selectedKinds = signal<readonly string[]>([]);
  protected readonly gridTheme = overviewGridTheme;
  protected readonly defaultColDef: ColDef<JobOverviewItem> = {resizable: true, sortable: false, minWidth: 110};
  protected readonly columnDefs: ColDef<JobOverviewItem>[] = [
    {field: 'job_id', headerName: 'Research job', minWidth: 260, flex: 2, cellRenderer: JobIdentityCell},

    {field: 'status', headerName: 'Status', width: 140, sortable: true, cellRenderer: JobStatusCell},
    {field: 'result_outcome', headerName: 'Outcome', minWidth: 170, flex: 1, valueFormatter: ({value}) => value ? this.label(value) : '—'},
    {field: 'attempt_count', headerName: 'Attempts', width: 110, sortable: true},
    {field: 'created_at', headerName: 'Created', minWidth: 180, valueFormatter: formatDate, sortable: true},
    {field: 'started_at', headerName: 'Started', hide: true, minWidth: 180, valueFormatter: formatDate, sortable: true},
    {field: 'finished_at', headerName: 'Finished', minWidth: 180, valueFormatter: formatDate, sortable: true},
    {field: 'deadline_at', headerName: 'Deadline', hide: true, minWidth: 180, valueFormatter: formatDate},
    {colId: 'actions', headerName: 'Actions', width: 110, pinned: 'right', resizable: false, cellRenderer: JobActionsCell, cellRendererParams: {isBusy: () => this.deleting(), deleteJob: (job: JobOverviewItem) => this.deleteJobs([job])}},
    {field: 'campaign_id', headerName: 'Campaign ID', minWidth: 190, hide: true},
    {field: 'icp_id', headerName: 'ICP ID', minWidth: 190, hide: true},
    {field: 'workflow_id', headerName: 'Workflow ID', minWidth: 190, hide: true},
    {field: 'stage_key', headerName: 'Stage', minWidth: 160, hide: true},
  ];
  protected readonly getRowId = (params: {data: JobOverviewItem}): string => params.data.job_id;
  private readonly datasource: IDatasource = {getRows: (params: IGetRowsParams<JobOverviewItem>) => this.loadOverviewRows(params.startRow, params.endRow - params.startRow, params.sortModel[0]?.colId, params.sortModel[0]?.sort, params.successCallback, params.failCallback)};
  private readonly generations: Record<Selector, number> = {campaigns: 0, icps: 0};


  ngOnInit(): void {
    this.refreshCrm();
    interval(AUTO_REFRESH_INTERVAL_MS).pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => {
      this.refreshCrm();
      this.refreshOverview();
    });
  }

  protected onGridReady(event: GridReadyEvent<JobOverviewItem>): void {
    this.gridApi = event.api;
    event.api.setGridOption('datasource', this.datasource);
  }

  protected refreshOverview(): void {
    if (!this.gridApi || this.overviewLoading() || this.deleting()) return;
    this.gridApi.refreshInfiniteCache();
  }

  protected applyOverviewFilters(): void {
    ++this.overviewGeneration;
    this.shownJobs.set([]);
    this.gridApi?.paginationGoToFirstPage();
    this.gridApi?.purgeInfiniteCache();
  }

  /** Limit bulk actions to loaded rows on the current filtered page. */
  protected updateShownJobs(): void {
    const grid = this.gridApi;
    if (!grid || grid.isDestroyed()) return;
    const start = grid.paginationGetCurrentPage() * grid.paginationGetPageSize();
    const rows: JobOverviewItem[] = [];
    for (let index = start; index < start + grid.paginationGetPageSize(); index++) {
      const data = grid.getDisplayedRowAtIndex(index)?.data;
      if (data) rows.push(data);
    }
    this.shownJobs.set(rows);
  }

  protected deletableShownJobs(): readonly JobOverviewItem[] {
    return this.shownJobs().filter((job) => ['cancelled', 'succeeded', 'failed'].includes(job.status));
  }

  /** Snapshot the displayed IDs and retain failed rows when a batch partially succeeds. */
  protected deleteJobs(jobs: readonly JobOverviewItem[]): void {
    if (this.deleting()) return;
    const targets = jobs.filter((job) => ['cancelled', 'succeeded', 'failed'].includes(job.status));
    if (!targets.length) return;
    this.deleting.set(true);
    this.deleteMessage.set('');
    this.gridApi?.refreshCells({columns: ['actions'], force: true});
    from(targets).pipe(
      mergeMap((job) => this.api.deleteJob(job.job_id).pipe(map(() => true), catchError(() => of(false))), 4),
      toArray(), takeUntilDestroyed(this.destroyRef),
    ).subscribe((results) => {
      const deleted = results.filter(Boolean).length;
      const failed = results.length - deleted;
      this.deleteMessage.set(`${deleted} job${deleted === 1 ? '' : 's'} deleted.${failed ? ` ${failed} could not be deleted. Jobs with unresolved CRM writes cannot be removed; wait for the list to refresh, then retry.` : ''}`);
      this.deleting.set(false);
      this.applyOverviewFilters();
    });
  }

  protected clearOverviewFilters(): void {
    this.jobIdFilter = ''; this.outcomeFilter = ''; this.createdAfter = ''; this.minimumAttempts = null;
    this.selectedStatuses.set([]); this.selectedKinds.set([]); this.applyOverviewFilters();
  }

  protected toggleFilter(values: 'status' | 'kind', value: string, checked: boolean): void {
    const target = values === 'status' ? this.selectedStatuses : this.selectedKinds;
    target.update((current) => checked ? [...current, value] : current.filter((item) => item !== value));
    this.applyOverviewFilters();
  }

  protected openJob(event: RowClickedEvent<JobOverviewItem>): void {
    if ((event.event?.target as HTMLElement | null)?.closest('button, a')) return;
    if (event.data) void this.router.navigate(['/jobs', event.data.job_id]);
  }

  private loadOverviewRows(startRow: number, requestedLimit: number, sortColumn: string | undefined, sortDirection: string | null | undefined, success: (rows: JobOverviewItem[], lastRow?: number) => void, fail: () => void): void {
    const generation = this.overviewGeneration;
    let params = new HttpParams();
    this.selectedStatuses().forEach((status) => { params = params.append('status', status); });
    this.selectedKinds().forEach((kind) => { params = params.append('kind', kind); });
    if (this.jobIdFilter.trim()) params = params.set('job_id', this.jobIdFilter.trim());
    if (this.outcomeFilter) params = params.set('outcome', this.outcomeFilter);
    if (this.createdAfter) params = params.set('created_after', new Date(this.createdAfter).toISOString());
    if (this.minimumAttempts !== null) params = params.set('minimum_attempts', String(this.minimumAttempts));
    params = params.set('offset', String(startRow)).set('limit', String(Math.min(requestedLimit, 100)));
    if (sortColumn && ['created_at', 'started_at', 'finished_at', 'attempt_count', 'status', 'kind'].includes(sortColumn)) params = params.set('sort', sortColumn);
    if (sortDirection === 'asc' || sortDirection === 'desc') params = params.set('direction', sortDirection);
    this.overviewRequestCount.update((count) => count + 1);
    this.overviewError.set(false);
    this.api.listJobOverview(params).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (page) => {
        this.overviewRequestCount.update((count) => Math.max(0, count - 1));
        if (generation !== this.overviewGeneration) return;
        this.overviewHasLoaded.set(true);
        this.totalJobs.set(page.total);
        success([...page.items], page.total);
        this.gridApi?.setRowCount(page.total);
        this.updateShownJobs();
      },
      error: () => {
        this.overviewRequestCount.update((count) => Math.max(0, count - 1));
        if (generation !== this.overviewGeneration) return;
        this.overviewError.set(true);
        fail();
      },
    });
  }


  protected refreshCrm(): void {
    if (this.crmLoading()) return;
    this.crmLoading.set(true);
    this.api.getCrmStatus().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (status) => {
        this.crm.set(status); this.crmError.set(false); this.crmLoading.set(false);
        if (status.available && status.schema_compatible) {
          if (!this.campaigns().length) this.loadCampaigns();
          if (!this.icps().length) this.loadIcps();
        }
      },
      error: () => { this.crmError.set(true); this.crmLoading.set(false); },
    });
  }

  private loadPage<T extends CrmListItem>(key: Selector, request: Observable<CrmPage<T>>, items: WritableSignal<readonly T[]>, cursor: WritableSignal<string | null>): void {
    if (this.selectorState()[key] === 'loading') return;
    const generation = this.generations[key];
    const append = cursor() !== null;
    this.selectorState.update((state) => ({...state, [key]: 'loading'}));
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (page) => {
        if (generation !== this.generations[key]) return;
        items.update((existing) => append ? [...existing, ...page.items.filter((item) => !existing.some((old) => old.id === item.id))] : page.items);
        cursor.set(page.next_cursor);
        this.selectorState.update((state) => ({...state, [key]: 'idle'}));
      },
      error: () => { if (generation === this.generations[key]) this.selectorState.update((state) => ({...state, [key]: 'error'})); },
    });
  }

  protected loadCampaigns(): void { this.loadPage('campaigns', this.api.listCrmCampaigns(this.campaignCursor() ?? undefined), this.campaigns, this.campaignCursor); }
  protected loadIcps(): void { this.loadPage('icps', this.api.listCrmIcps(this.selectedCampaign || undefined, this.icpCursor() ?? undefined), this.icps, this.icpCursor); }
  protected campaignChanged(): void {
    this.selectedIcp = ''; this.icps.set([]); this.icpCursor.set(null); ++this.generations.icps;
    this.selectorState.update((state) => ({...state, icps: 'idle'}));
    this.loadIcps();
  }
  protected kindChanged(): void { this.enqueueError.set(''); }
  protected canEnqueue(): boolean {
    if (this.crmError() || !this.crm()?.available || this.crm()?.schema_compatible !== true) return false;
    const kind = this.selectedKind();
    if (kind === 'campaign') return true;
    if (kind === 'icp') return Boolean(this.selectedCampaign) && this.validCount(this.icpCount, 20);
    return Boolean(this.selectedIcp)
      && this.validCount(this.companyCount, 20)
      && this.validCount(this.peoplePerCompany, 5)
      && this.validCount(this.opportunitiesPerCompany, 3);
  }
  protected enqueue(): void {
    if (!this.canEnqueue() || this.enqueuePending()) return;
    const kind = this.selectedKind();
    this.enqueuePending.set(true); this.enqueueError.set('');
    const request = kind === 'campaign' ? this.api.submitJob({kind})
      : kind === 'icp' ? this.api.submitJob({kind, campaign_id: this.selectedCampaign, icp_count: this.icpCount})
      : this.api.submitJob({kind: 'account', icp_id: this.selectedIcp, company_count: this.companyCount, people_per_company: this.peoplePerCompany, opportunities_per_company: this.opportunitiesPerCompany});
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: () => { this.applyOverviewFilters(); this.enqueuePending.set(false); },
      error: () => { this.enqueuePending.set(false); this.enqueueError.set('The job could not be started. Check CRM compatibility and the selected scope.'); },
    });
  }
  private validCount(value: number, maximum: number): boolean { return Number.isInteger(value) && value >= 1 && value <= maximum; }
}
