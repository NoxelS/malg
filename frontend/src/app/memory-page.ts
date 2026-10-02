import {HttpParams} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {Router} from '@angular/router';
import {AgGridAngular} from 'ag-grid-angular';
import {ColDef, GridApi, GridReadyEvent, IDatasource, IGetRowsParams, RowClickedEvent} from 'ag-grid-community';

import {ApiService, MemoryOverviewItem} from './api-service';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {overviewGridTheme, registerOverviewGridModules} from './components/overview-grid.config';
import {StateMessageComponent} from './components/state-message.component';
import {TuiButton} from '@taiga-ui/core';

registerOverviewGridModules();

const formatTimestamp = (params: {value: number | null | undefined}): string => params.value ? new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short'}).format(params.value * 1000) : '—';

@Component({
  selector: 'app-memory-page',
  imports: [AgGridAngular, FormsModule, PageHeaderComponent, PageLayoutComponent, StateMessageComponent, TuiButton],
  templateUrl: './memory-page.html',
  styleUrl: './memory-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MemoryPage implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private gridApi: GridApi<MemoryOverviewItem> | null = null;
  protected readonly loading = signal(false);
  protected readonly error = signal(false);
  protected readonly clearing = signal(false);
  protected readonly clearError = signal(false);
  protected readonly clearConfirmationOpen = signal(false);
  protected readonly clearedCount = signal<number | null>(null);
  protected readonly includeArchived = signal(false);
  protected contentQuery = '';
  protected ownerFilter = '';
  protected typeFilter = '';
  protected statusFilter = '';
  protected readonly gridTheme = overviewGridTheme;
  protected readonly defaultColDef: ColDef<MemoryOverviewItem> = {resizable: true, sortable: true, minWidth: 105};
  protected readonly columnDefs: ColDef<MemoryOverviewItem>[] = [
    {field: 'content_preview', headerName: 'Memory', flex: 3, minWidth: 300},
    {field: 'type', headerName: 'Type', width: 130},
    {field: 'status', headerName: 'Status', width: 125},
    {field: 'owner', headerName: 'Owner', minWidth: 150},
    {field: 'importance', headerName: 'Importance', width: 120},
    {field: 'salience', headerName: 'Salience', width: 110},
    {field: 'strength', headerName: 'Strength', width: 110},
    {field: 'access_count', headerName: 'Accesses', width: 110},
    {field: 'created_at', headerName: 'Created', minWidth: 180, valueFormatter: formatTimestamp},
    {field: 'last_accessed_at', headerName: 'Last accessed', minWidth: 180, valueFormatter: formatTimestamp},
    {field: 'archived', headerName: 'Archived', width: 110},
    {field: 'id', headerName: 'ID', minWidth: 210, hide: true},
  ];
  protected readonly getRowId = (params: {data: MemoryOverviewItem}): string => params.data.id;
  private readonly datasource: IDatasource = {getRows: (params: IGetRowsParams<MemoryOverviewItem>) => this.loadRows(params.startRow, params.endRow - params.startRow, params.sortModel[0]?.colId, params.sortModel[0]?.sort, params.successCallback, params.failCallback)};

  ngOnInit(): void {}

  protected onGridReady(event: GridReadyEvent<MemoryOverviewItem>): void {
    this.gridApi = event.api;
    event.api.setGridOption('datasource', this.datasource);
  }

  protected applyFilters(): void { this.gridApi?.purgeInfiniteCache(); }

  protected clearFilters(): void {
    this.contentQuery = ''; this.ownerFilter = ''; this.typeFilter = ''; this.statusFilter = '';
    this.includeArchived.set(false); this.applyFilters();
  }

  protected openMemory(event: RowClickedEvent<MemoryOverviewItem>): void {
    if (event.data) void this.router.navigate(['/memory', event.data.id]);
  }

  protected requestClear(): void { this.clearError.set(false); this.clearConfirmationOpen.set(true); }
  protected cancelClear(): void { this.clearConfirmationOpen.set(false); }

  protected clearAllMemories(): void {
    if (this.clearing()) return;
    this.clearing.set(true); this.clearError.set(false);
    this.api.clearMemories().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (result) => { this.clearing.set(false); this.clearConfirmationOpen.set(false); this.clearedCount.set(result.deleted); this.applyFilters(); },
      error: () => { this.clearing.set(false); this.clearError.set(true); },
    });
  }

  private loadRows(startRow: number, requestedLimit: number, sortColumn: string | undefined, sortDirection: string | null | undefined, success: (rows: MemoryOverviewItem[], lastRow?: number) => void, fail: () => void): void {
    let params = new HttpParams();
    if (this.includeArchived()) params = params.set('include_archived', 'true');
    if (this.contentQuery.trim()) params = params.set('query', this.contentQuery.trim());
    if (this.ownerFilter.trim()) params = params.set('owner', this.ownerFilter.trim());
    if (this.typeFilter) params = params.set('type', this.typeFilter);
    if (this.statusFilter) params = params.set('status', this.statusFilter);
    params = params.set('offset', String(startRow)).set('limit', String(Math.min(requestedLimit, 100)));
    if (sortColumn && ['created_at', 'last_accessed_at', 'importance', 'salience', 'strength', 'access_count', 'type'].includes(sortColumn)) params = params.set('sort', sortColumn);
    if (sortDirection === 'asc' || sortDirection === 'desc') params = params.set('direction', sortDirection);
    this.loading.set(true); this.error.set(false);
    this.api.listMemoryOverview(params).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (page) => { this.loading.set(false); success([...page.items], page.total); },
      error: () => { this.loading.set(false); this.error.set(true); fail(); },
    });
  }
}
