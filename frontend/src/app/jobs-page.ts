import {HttpParams} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, WritableSignal, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {Router} from '@angular/router';
import {Observable} from 'rxjs';
import {AgGridAngular} from 'ag-grid-angular';
import {ColDef, GridApi, GridReadyEvent, IDatasource, IGetRowsParams, RowClickedEvent} from 'ag-grid-community';
import {ApiService, CrmListItem, CrmPage, CrmStatus, JobKind, JobOverviewItem} from './api-service';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';
import {overviewGridTheme, registerOverviewGridModules} from './components/overview-grid.config';

type Selector = 'campaigns' | 'icps';
type LoadState = 'idle' | 'loading' | 'error';

const formatDate = (params: {value: string | null | undefined}): string => params.value ? new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short'}).format(new Date(params.value)) : '—';

registerOverviewGridModules();

@Component({selector: 'app-jobs-page', imports: [AgGridAngular, FormsModule, TuiButton, PageHeaderComponent, PageLayoutComponent, StateMessageComponent], changeDetection: ChangeDetectionStrategy.OnPush, templateUrl: './jobs-page.html', styleUrl: './jobs-page.scss'})
export class JobsPage implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private gridApi: GridApi<JobOverviewItem> | null = null;
  protected readonly overviewLoading = signal(false);
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
  protected readonly defaultColDef: ColDef<JobOverviewItem> = {resizable: true, sortable: true, minWidth: 110};
  protected readonly columnDefs: ColDef<JobOverviewItem>[] = [
    {field: 'job_id', headerName: 'Job ID', minWidth: 220, flex: 2},
    {field: 'kind', headerName: 'Kind', width: 130},
    {field: 'status', headerName: 'Status', width: 130},
    {field: 'result_outcome', headerName: 'Outcome', minWidth: 150},
    {field: 'attempt_count', headerName: 'Attempts', width: 115},
    {field: 'created_at', headerName: 'Created', minWidth: 180, valueFormatter: formatDate},
    {field: 'started_at', headerName: 'Started', minWidth: 180, valueFormatter: formatDate},
    {field: 'finished_at', headerName: 'Finished', minWidth: 180, valueFormatter: formatDate},
    {field: 'deadline_at', headerName: 'Deadline', minWidth: 180, valueFormatter: formatDate},
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
  }

  protected onGridReady(event: GridReadyEvent<JobOverviewItem>): void {
    this.gridApi = event.api;
    event.api.setGridOption('datasource', this.datasource);
  }

  protected applyOverviewFilters(): void { this.gridApi?.purgeInfiniteCache(); }

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
    if (event.data) void this.router.navigate(['/jobs', event.data.job_id]);
  }

  private loadOverviewRows(startRow: number, requestedLimit: number, sortColumn: string | undefined, sortDirection: string | null | undefined, success: (rows: JobOverviewItem[], lastRow?: number) => void, fail: () => void): void {
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
    this.overviewLoading.set(true); this.overviewError.set(false);
    this.api.listJobOverview(params).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (page) => { this.overviewLoading.set(false); success([...page.items], page.total); },
      error: () => { this.overviewLoading.set(false); this.overviewError.set(true); fail(); },
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
