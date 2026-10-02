import {HttpParams} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {Router} from '@angular/router';
import {AgGridAngular} from 'ag-grid-angular';
import {ColDef, ModuleRegistry, RenderApiModule, RowApiModule, GridApi, GridReadyEvent, IDatasource, IGetRowsParams, RowClickedEvent} from 'ag-grid-community';

import {ApiService, MemoryOverviewItem} from './api-service';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {overviewGridTheme, registerOverviewGridModules} from './components/overview-grid.config';
import {StateMessageComponent} from './components/state-message.component';
import {TuiButton, TuiCheckbox, TuiInput} from '@taiga-ui/core';
import {TuiSelect} from '@taiga-ui/kit';
import {catchError, from, map, mergeMap, of, toArray} from 'rxjs';
import {MemoryPreviewCell, MemoryTypeCell, MemoryActionsCell} from './components/memory-grid-cells';

registerOverviewGridModules();
ModuleRegistry.registerModules([RenderApiModule, RowApiModule]);

const formatTimestamp = (params: {value: number | null | undefined}): string => params.value ? new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short'}).format(params.value * 1000) : '—';

@Component({
  selector: 'app-memory-page',
  imports: [AgGridAngular, FormsModule, PageHeaderComponent, PageLayoutComponent, StateMessageComponent, TuiButton, TuiCheckbox, TuiInput, TuiSelect],
  templateUrl: './memory-page.html',
  styleUrl: './memory-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MemoryPage implements OnInit {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private gridApi: GridApi<MemoryOverviewItem> | null = null;
  protected readonly deleting = signal(false);
  protected readonly deleteMessage = signal('');
  protected readonly shownMemories = signal<readonly MemoryOverviewItem[]>([]);
  protected readonly totalMemories = signal(0);
  protected readonly typeOptions = ['', 'info', 'skill', 'episode', 'intent', 'todo', 'reflection', 'scratch'];
  protected readonly typeLabel = (value: string): string => value ? value.charAt(0).toUpperCase() + value.slice(1) : 'All types';
  private generation = 0;
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
  protected readonly defaultColDef: ColDef<MemoryOverviewItem> = {resizable: true, sortable: false, minWidth: 105};
  protected readonly columnDefs: ColDef<MemoryOverviewItem>[] = [
    {field: 'content_preview', headerName: 'Memory', flex: 3, minWidth: 320, cellRenderer: MemoryPreviewCell},
    {field: 'type', headerName: 'Type', width: 130, sortable: true, cellRenderer: MemoryTypeCell},
    {field: 'status', headerName: 'Todo status', width: 135, valueFormatter: ({value}) => value ? this.typeLabel(value) : '—'},
    {field: 'owner', headerName: 'Owner', minWidth: 150},
    {field: 'importance', sortable: true, headerName: 'Importance', width: 120},
    {field: 'salience', sortable: true, hide: true, headerName: 'Salience', width: 110},
    {field: 'strength', sortable: true, hide: true, headerName: 'Strength', width: 110},
    {field: 'access_count', sortable: true, headerName: 'Accesses', width: 110},
    {field: 'created_at', sortable: true, headerName: 'Created', minWidth: 180, valueFormatter: formatTimestamp},
    {field: 'last_accessed_at', sortable: true, hide: true, headerName: 'Last accessed', minWidth: 180, valueFormatter: formatTimestamp},
    {colId: 'actions', headerName: 'Actions', width: 110, pinned: 'right', resizable: false, cellRenderer: MemoryActionsCell, cellRendererParams: {isBusy: () => this.deleting() || this.clearing(), deleteMemory: (memory: MemoryOverviewItem) => this.deleteMemories([memory])}},
    {field: 'id', headerName: 'ID', minWidth: 210, hide: true},
  ];
  protected readonly getRowId = (params: {data: MemoryOverviewItem}): string => params.data.id;
  private readonly datasource: IDatasource = {getRows: (params: IGetRowsParams<MemoryOverviewItem>) => this.loadRows(params.startRow, params.endRow - params.startRow, params.sortModel[0]?.colId, params.sortModel[0]?.sort, params.successCallback, params.failCallback)};

  ngOnInit(): void {}

  protected onGridReady(event: GridReadyEvent<MemoryOverviewItem>): void {
    this.gridApi = event.api;
    event.api.setGridOption('datasource', this.datasource);
  }

  protected applyFilters(): void {
    ++this.generation;
    this.shownMemories.set([]);
    this.gridApi?.paginationGoToFirstPage();
    this.gridApi?.purgeInfiniteCache();
  }

  /** Restrict bulk actions to the loaded rows on the current filtered page. */
  protected updateShownMemories(): void {
    const grid = this.gridApi;
    if (!grid || grid.isDestroyed()) return;
    const start = grid.paginationGetCurrentPage() * grid.paginationGetPageSize();
    const rows: MemoryOverviewItem[] = [];
    for (let index = start; index < start + grid.paginationGetPageSize(); index++) {
      const memory = grid.getDisplayedRowAtIndex(index)?.data;
      if (memory) rows.push(memory);
    }
    this.shownMemories.set(rows);
  }

  /** Snapshot displayed IDs and report partial deletion without widening its scope. */
  protected deleteMemories(memories: readonly MemoryOverviewItem[]): void {
    if (this.deleting() || this.clearing() || !memories.length) return;
    const targets = [...memories];
    this.deleting.set(true);
    this.deleteMessage.set('');
    this.clearedCount.set(null);
    this.gridApi?.refreshCells({columns: ['actions'], force: true});
    from(targets).pipe(
      mergeMap((memory) => this.api.deleteMemory(memory.id).pipe(map(() => true), catchError(() => of(false))), 4),
      toArray(), takeUntilDestroyed(this.destroyRef),
    ).subscribe((results) => {
      const deleted = results.filter(Boolean).length;
      const failed = results.length - deleted;
      this.deleteMessage.set(`${deleted} memor${deleted === 1 ? 'y' : 'ies'} deleted.${failed ? ` ${failed} could not be deleted. Refresh and retry the remaining entries.` : ''}`);
      this.deleting.set(false);
      this.applyFilters();
    });
  }

  protected clearFilters(): void {
    this.contentQuery = ''; this.ownerFilter = ''; this.typeFilter = ''; this.statusFilter = '';
    this.includeArchived.set(false); this.applyFilters();
  }

  protected openMemory(event: RowClickedEvent<MemoryOverviewItem>): void {
    if ((event.event?.target as HTMLElement | null)?.closest('button, a')) return;
    if (event.data) void this.router.navigate(['/memory', event.data.id]);
  }

  protected requestClear(): void { this.clearError.set(false); this.clearConfirmationOpen.set(true); }
  protected cancelClear(): void { this.clearConfirmationOpen.set(false); }

  protected clearAllMemories(): void {
    if (this.clearing() || this.deleting()) return;
    this.clearing.set(true); this.clearError.set(false); this.deleteMessage.set('');
    this.gridApi?.refreshCells({columns: ['actions'], force: true});
    this.api.clearMemories().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (result) => { this.clearing.set(false); this.clearConfirmationOpen.set(false); this.clearedCount.set(result.deleted); this.applyFilters(); },
      error: () => { this.clearing.set(false); this.clearError.set(true); this.gridApi?.refreshCells({columns: ['actions'], force: true}); },
    });
  }

  private loadRows(startRow: number, requestedLimit: number, sortColumn: string | undefined, sortDirection: string | null | undefined, success: (rows: MemoryOverviewItem[], lastRow?: number) => void, fail: () => void): void {
    const generation = this.generation;
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
      next: (page) => { if (generation !== this.generation) return; this.loading.set(false); this.totalMemories.set(page.total); success([...page.items], page.total); this.updateShownMemories(); },
      error: () => { if (generation !== this.generation) return; this.loading.set(false); this.error.set(true); fail(); },
    });
  }
}
