import {HttpParams} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {ActivatedRoute, Router, RouterLink} from '@angular/router';
import * as echarts from 'echarts/core';
import {BarChart, HeatmapChart, LineChart} from 'echarts/charts';
import {AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, VisualMapComponent} from 'echarts/components';
import type {EChartsCoreOption} from 'echarts/core';
import {CanvasRenderer} from 'echarts/renderers';
import {EMPTY, Subject, catchError, switchMap, tap} from 'rxjs';
import {NgxEchartsDirective, provideEchartsCore} from 'ngx-echarts';

import {ApiService} from './api-service';
import type {StatsExecutionPage, StatsIssue, StatsJobBucket, StatsOutcome, StatsResponse, StatsToolBucket, StatsToolCounts, StatsToolSeries, StatsToolSource, StatsWorker} from './api-service';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {SectionHeadingComponent} from './components/section-heading.component';
import {StateMessageComponent} from './components/state-message.component';

echarts.use([
  BarChart,
  LineChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  DataZoomComponent,
  VisualMapComponent,
  AriaComponent,
  CanvasRenderer,
]);

const RANGE_PRESETS = [1, 6, 24, 168, 720, 2160] as const;
const TOOL_SOURCES: readonly StatsToolSource[] = ['search', 'fetch', 'browser_mcp'];
const OUTCOMES: readonly StatsOutcome[] = ['success', 'degraded', 'blocked', 'error', 'rejected', 'cancelled'];
const ZERO_COUNTS: StatsToolCounts = {
  completed: 0,
  success: 0,
  degraded: 0,
  blocked: 0,
  error: 0,
  rejected: 0,
  cancelled: 0,
  cache_hits: 0,
  coalesced: 0,
  outbound_attempts: 0,
};

interface SnapshotRequest {
  readonly hours: number;
  readonly generation: number;
}
interface WorkerPageRequest extends SnapshotRequest {
  readonly anchor: string;
  readonly offset: number;
  readonly requestId: number;
}
type ExecutionPageRequest = WorkerPageRequest;

const parseHours = (raw: string | null): number | null => {
  if (raw === null || !/^\d+$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value >= 1 && value <= 2160 ? value : null;
};

const addCounts = (left: StatsToolCounts, right: StatsToolCounts): StatsToolCounts => ({
  completed: left.completed + right.completed,
  success: left.success + right.success,
  degraded: left.degraded + right.degraded,
  blocked: left.blocked + right.blocked,
  error: left.error + right.error,
  rejected: left.rejected + right.rejected,
  cancelled: left.cancelled + right.cancelled,
  cache_hits: left.cache_hits + right.cache_hits,
  coalesced: left.coalesced + right.coalesced,
  outbound_attempts: left.outbound_attempts + right.outbound_attempts,
});

const LOCAL_DATE_TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  year: 'numeric', month: 'short', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
});
const LOCAL_AXIS_TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'});
const BROWSER_TIME_ZONE = LOCAL_DATE_TIME_FORMATTER.resolvedOptions().timeZone || 'browser local time';

const epochMicroseconds = (value: string): number => {
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) return Number.NaN;
  const fraction = value.match(/\.(\d+)(?=(?:Z|[+-]\d{2}:?\d{2})$)/i)?.[1] ?? '';
  const remainingMicroseconds = Number(fraction.slice(0, 6).padEnd(6, '0')) % 1000;
  return milliseconds * 1000 + remainingMicroseconds;
};

const formatDateTime = (value: string | null | undefined): string => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  const fraction = value.match(/\.(\d+)(?=(?:Z|[+-]\d{2}:?\d{2})$)/i)?.[1];
  return LOCAL_DATE_TIME_FORMATTER.formatToParts(date)
    .map((part) => part.type === 'second' && fraction ? `${part.value}.${fraction}` : part.value)
    .join('');
};

const formatAxisTime = (value: string): string => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return LOCAL_AXIS_TIME_FORMATTER.format(date);
};

const formatInterval = (start: string, end: string): string => `${formatDateTime(start)} – ${formatDateTime(end)}`;
const formatCount = (value: number): string => new Intl.NumberFormat().format(value);
const formatPercent = (numerator: number, denominator: number): string => denominator > 0 ? `${(numerator / denominator * 100).toFixed(1)}%` : '—';
const formatDuration = (seconds: number | null): string => {
  if (seconds === null || !Number.isFinite(seconds)) return 'Unknown';
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return remainder ? `${minutes} min ${remainder} s` : `${minutes} min`;
};

const issueLabel = (issue: StatsIssue): string => `${issue.source}${issue.engine ? ` · ${issue.engine}` : ''} — ${issue.message}`;

@Component({
  selector: 'app-stats-page',
  imports: [NgxEchartsDirective, RouterLink, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent],
  providers: [provideEchartsCore({echarts})],
  templateUrl: './stats-page.html',
  styleUrl: './stats-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StatsPage {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  private readonly snapshotRequests = new Subject<SnapshotRequest>();
  private readonly workerPageRequests = new Subject<WorkerPageRequest | null>();
  private readonly executionPageRequests = new Subject<ExecutionPageRequest | null>();
  private generation = 0;
  private workerRequestId = 0;
  private executionRequestId = 0;
  private workerTargetOffset: number | null = null;
  private executionTargetOffset: number | null = null;
  private activeHours: number | null = null;
  private lastQueryHours: string | null | undefined;
  private normalizingInvalidHours = false;

  readonly presets = RANGE_PRESETS;
  readonly hours = signal(24);
  readonly customHours = signal('');
  readonly rangeWarning = signal<string | null>(null);
  readonly snapshot = signal<StatsResponse | null>(null);
  readonly anchor = signal<string | null>(null);
  readonly snapshotLoading = signal(false);
  readonly snapshotError = signal(false);
  readonly staleSnapshot = signal(false);
  readonly lastSuccessfulRefresh = signal<string | null>(null);
  readonly workerOffset = signal(0);
  readonly workerLoading = signal(false);
  readonly workerError = signal(false);
  readonly workerRetryOffset = signal<number | null>(null);
  readonly executionOffset = signal(0);
  readonly executionPage = signal<StatsExecutionPage | null>(null);
  readonly executionLoading = signal(false);
  readonly executionError = signal(false);
  readonly executionRetryOffset = signal<number | null>(null);
  readonly sourceFilter = signal<'all' | StatsToolSource>('all');
  readonly timezone = BROWSER_TIME_ZONE;

  readonly coverageState = computed<'covered' | 'partial' | 'unavailable'>(() => {
    const value = this.snapshot();
    if (!value) return 'unavailable';
    const start = epochMicroseconds(value.window.from);
    const end = epochMicroseconds(value.window.to);
    const coverageStart = epochMicroseconds(value.window.tool_coverage_from);
    if (coverageStart >= end) return 'unavailable';
    if (coverageStart > start) return 'partial';
    return 'covered';
  });

  readonly searchTotals = computed(() => this.totalsFor(['search']));
  readonly searchFetchTotals = computed(() => this.totalsFor(['search', 'fetch']));
  readonly mcpTotals = computed(() => this.totalsFor(['browser_mcp']));
  readonly successRate = computed(() => formatPercent(this.searchFetchTotals().success, this.searchFetchTotals().completed));
  readonly cacheHitRate = computed(() => formatPercent(this.searchFetchTotals().cache_hits, this.searchFetchTotals().completed));

  readonly outcomesOption = computed<EChartsCoreOption>(() => {
    const buckets = this.snapshot()?.jobs.buckets ?? [];
    const selected = this.selectedToolSeries();
    const mapped = selected.map((series) => new Map(series.buckets.map((bucket) => [bucket.start, bucket])));
    const values = OUTCOMES.map((outcome) => ({
      name: this.outcomeLabel(outcome),
      type: 'bar' as const,
      stack: 'outcomes',
      emphasis: {focus: 'series' as const},
      data: buckets.map((bucket, index) => {
        const rows = mapped.map((map) => map.get(bucket.start));
        if (!rows.length || rows.some((row) => !row)) return null;
        return rows.reduce((total, row) => total + (row?.[outcome] ?? 0), 0);
      }),
    }));
    return {
      aria: {enabled: true, description: 'Recorded completed tool-call outcomes per time bucket. Missing buckets are unavailable, not zero.'},
      color: ['#246b5a', '#d59d27', '#b54444', '#8c4a9b', '#58677b', '#333f4d'],
      grid: {left: 52, right: 20, top: 52, bottom: 72},
      legend: {type: 'scroll', top: 4},
      tooltip: {trigger: 'axis', renderMode: 'richText', formatter: (params: unknown) => this.timeTooltip(params, buckets)},
      dataZoom: [{type: 'inside'}, {type: 'slider', height: 18, bottom: 12}],
      xAxis: {type: 'category', data: buckets.map((bucket) => formatAxisTime(bucket.start)), axisLabel: {rotate: 25, hideOverlap: true}},
      yAxis: {type: 'value', name: 'Recorded calls', minInterval: 1},
      series: values,
    };
  });

  readonly cacheOption = computed<EChartsCoreOption>(() => {
    const buckets = this.snapshot()?.jobs.buckets ?? [];
    const series = this.snapshot()?.tools ?? [];
    const dataFor = (source: StatsToolSource, pick: (bucket: StatsToolBucket) => number): (number | null)[] => {
      const index = new Map((series.find((item) => item.source === source)?.buckets ?? []).map((bucket) => [bucket.start, bucket]));
      return buckets.map((bucket) => index.has(bucket.start) ? pick(index.get(bucket.start)!) : null);
    };
    return {
      aria: {enabled: true, description: 'Search shared-cache hits, fetch local-cache hits, and coalesced search callers are separate time series.'},
      color: ['#316f88', '#d18b35', '#8f60a8'],
      grid: {left: 52, right: 20, top: 46, bottom: 72},
      legend: {top: 4},
      tooltip: {trigger: 'axis', renderMode: 'richText', formatter: (params: unknown) => this.timeTooltip(params, buckets)},
      dataZoom: [{type: 'inside'}, {type: 'slider', height: 18, bottom: 12}],
      xAxis: {type: 'category', data: buckets.map((bucket) => formatAxisTime(bucket.start)), axisLabel: {rotate: 25, hideOverlap: true}},
      yAxis: {type: 'value', name: 'Recorded events', minInterval: 1},
      series: [
        {name: 'Shared search-cache hits', type: 'line', showSymbol: false, connectNulls: false, data: dataFor('search', (bucket) => bucket.cache_hits)},
        {name: 'Local fetch-cache hits', type: 'line', showSymbol: false, connectNulls: false, data: dataFor('fetch', (bucket) => bucket.cache_hits)},
        {name: 'Coalesced search callers (not hits by definition)', type: 'line', showSymbol: false, connectNulls: false, data: dataFor('search', (bucket) => bucket.coalesced)},
      ],
    };
  });

  readonly blockReasonsOption = computed<EChartsCoreOption>(() => this.issueChartOption(this.snapshot()?.block_reasons.items ?? [], 'Recorded block diagnostics by occurrence'));
  readonly searxngErrorsOption = computed<EChartsCoreOption>(() => this.issueChartOption(this.snapshot()?.searxng_errors.items ?? [], 'SearXNG engine diagnostics by occurrence, including block and error issues'));

  readonly jobsOption = computed<EChartsCoreOption>(() => {
    const buckets = this.snapshot()?.jobs.buckets ?? [];
    return {
      aria: {enabled: true, description: 'Worker-scope attempt starts are bucketed by start time; succeeded and failed spans are bucketed by finish time.'},
      color: ['#316f88', '#3d866f', '#bd5550'],
      grid: {left: 56, right: 20, top: 46, bottom: 72},
      legend: {top: 4},
      tooltip: {trigger: 'axis', renderMode: 'richText', formatter: (params: unknown) => this.timeTooltip(params, buckets)},
      dataZoom: [{type: 'inside'}, {type: 'slider', height: 18, bottom: 12}],
      xAxis: {type: 'category', data: buckets.map((bucket) => formatAxisTime(bucket.start)), axisLabel: {rotate: 25, hideOverlap: true}},
      yAxis: {type: 'value', name: 'Attempts', minInterval: 1},
      series: [
        {name: 'Attempts started', type: 'bar', data: buckets.map((bucket) => bucket.attempts_started)},
        {name: 'Attempts succeeded', type: 'bar', data: buckets.map((bucket) => bucket.attempts_succeeded)},
        {name: 'Attempts failed', type: 'bar', data: buckets.map((bucket) => bucket.attempts_failed)},
      ],
    };
  });

  readonly workerHeatmapOption = computed<EChartsCoreOption>(() => {
    const value = this.snapshot();
    const buckets = value?.jobs.buckets ?? [];
    const workers = value?.workers.items ?? [];
    const points: number[][] = [];
    for (let workerIndex = 0; workerIndex < workers.length; workerIndex++) {
      const worker = workers[workerIndex];
      const workerBuckets = new Map(worker.buckets.map((bucket) => [bucket.start, bucket]));
      for (let bucketIndex = 0; bucketIndex < buckets.length; bucketIndex++) {
        const interval = buckets[bucketIndex];
        const recorded = workerBuckets.get(interval.start);
        if (!recorded) continue;
        const seconds = Math.max(0, (epochMicroseconds(interval.end) - epochMicroseconds(interval.start)) / 1_000_000);
        const fraction = seconds > 0 ? recorded.busy_seconds / seconds : 0;
        points.push([bucketIndex, workerIndex, fraction, recorded.busy_seconds, recorded.incomplete_runs]);
      }
    }
    return {
      aria: {enabled: true, description: 'Worker busy fraction is the union of verified busy seconds divided by exact bucket duration. Color represents recorded busy fraction, not online status; an exclamation mark denotes an incomplete span.'},
      grid: {left: 140, right: 24, top: 26, bottom: 82},
      tooltip: {
        trigger: 'item',
        renderMode: 'richText',
        formatter: (raw: unknown) => this.heatmapTooltip(raw, buckets, workers.map((worker) => worker.worker_token)),
      },
      xAxis: {type: 'category', data: buckets.map((bucket) => formatAxisTime(bucket.start)), axisLabel: {rotate: 30, hideOverlap: true}},
      yAxis: {type: 'category', data: workers.map((worker) => worker.worker_token), axisLabel: {width: 124, overflow: 'truncate'}},
      visualMap: {min: 0, max: 1, calculable: false, orient: 'horizontal', left: 'center', bottom: 4, text: ['100% busy', '0% recorded'], inRange: {color: ['#f0f3f5', '#9dc9bd', '#17644f']}},
      series: [{name: 'Recorded busy fraction', type: 'heatmap', data: points, label: {show: true, color: '#172b24', fontWeight: 'bold', formatter: (raw: unknown) => this.heatmapMarker(raw)}, emphasis: {itemStyle: {shadowBlur: 8, shadowColor: 'rgba(0, 0, 0, 0.35)'}}}],
    };
  });

  constructor() {
    this.snapshotRequests.pipe(
      switchMap((request) => {
        this.snapshotLoading.set(true);
        this.snapshotError.set(false);
        this.staleSnapshot.set(false);
        this.workerPageRequests.next(null);
        this.executionPageRequests.next(null);
        this.workerLoading.set(false);
        this.executionLoading.set(false);
        if (!this.snapshot()) this.lastSuccessfulRefresh.set(null);
        const params = new HttpParams().set('hours', request.hours).set('worker_offset', 0).set('worker_limit', 25);
        return this.api.getStats(params).pipe(
          tap((response) => {
            if (request.generation !== this.generation) return;
            this.snapshot.set(response);
            this.anchor.set(response.window.to);
            this.lastSuccessfulRefresh.set(response.window.generated_at);
            this.snapshotLoading.set(false);
            this.snapshotError.set(false);
            this.staleSnapshot.set(false);
            this.workerOffset.set(response.workers.offset);
            this.workerTargetOffset = null;
            this.workerRetryOffset.set(null);
            this.workerError.set(false);
            this.executionOffset.set(0);
            this.executionTargetOffset = null;
            this.executionRetryOffset.set(null);
            this.executionPage.set(null);
            this.executionError.set(false);
            this.loadExecutionPage(request.hours, response.window.to, request.generation, 0);
          }),
          catchError(() => {
            if (request.generation === this.generation) {
              this.snapshotLoading.set(false);
              this.snapshotError.set(true);
              this.staleSnapshot.set(this.snapshot() !== null);
              const previousAnchor = this.anchor();
              if (this.snapshot() && previousAnchor && !this.executionPage()) {
                this.loadExecutionPage(request.hours, previousAnchor, request.generation, this.executionOffset());
              }
            }
            return EMPTY;
          }),
        );
      }),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe();

    this.workerPageRequests.pipe(
      switchMap((request) => {
        if (!request) return EMPTY;
        const params = new HttpParams()
          .set('hours', request.hours)
          .set('to', request.anchor)
          .set('worker_offset', request.offset)
          .set('worker_limit', 25);
        return this.api.getStats(params).pipe(
          tap((response) => {
            if (request.generation !== this.generation || request.anchor !== this.anchor() || request.offset !== this.workerTargetOffset || request.requestId !== this.workerRequestId) return;
            const current = this.snapshot();
            if (!current) return;
            this.snapshot.set({...current, workers: response.workers});
            this.workerOffset.set(response.workers.offset);
            this.workerTargetOffset = null;
            this.workerRetryOffset.set(null);
            this.workerLoading.set(false);
            this.workerError.set(false);
          }),
          catchError(() => {
            if (request.generation === this.generation && request.offset === this.workerTargetOffset && request.requestId === this.workerRequestId) {
              this.workerTargetOffset = null;
              this.workerRetryOffset.set(request.offset);
              this.workerLoading.set(false);
              this.workerError.set(true);
            }
            return EMPTY;
          }),
        );
      }),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe();

    this.executionPageRequests.pipe(
      switchMap((request) => {
        if (!request) return EMPTY;
        const params = new HttpParams()
          .set('hours', request.hours)
          .set('to', request.anchor)
          .set('offset', request.offset)
          .set('limit', 50);
        return this.api.getStatsExecutions(params).pipe(
          tap((page) => {
            if (request.generation !== this.generation || request.anchor !== this.anchor() || request.offset !== this.executionTargetOffset || request.requestId !== this.executionRequestId) return;
            this.executionPage.set(page);
            this.executionOffset.set(page.offset);
            this.executionTargetOffset = null;
            this.executionRetryOffset.set(null);
            this.executionLoading.set(false);
            this.executionError.set(false);
          }),
          catchError(() => {
            if (request.generation === this.generation && request.offset === this.executionTargetOffset && request.requestId === this.executionRequestId) {
              this.executionTargetOffset = null;
              this.executionRetryOffset.set(request.offset);
              this.executionLoading.set(false);
              this.executionError.set(true);
            }
            return EMPTY;
          }),
        );
      }),
      takeUntilDestroyed(this.destroyRef),
    ).subscribe();

    this.route.queryParamMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      const raw = params.get('hours');
      if (raw !== this.lastQueryHours) {
        const parsed = parseHours(raw);
        const invalid = raw !== null && parsed === null;
        const selectedHours = invalid || raw === null ? 24 : parsed!;
        if (invalid) {
          this.rangeWarning.set('The hours URL value is invalid. The range was normalized to 24 hours.');
          this.normalizingInvalidHours = true;
        } else if (this.normalizingInvalidHours && raw === String(selectedHours)) {
          this.normalizingInvalidHours = false;
        } else {
          this.rangeWarning.set(null);
        }
        this.lastQueryHours = raw;
        if (raw === null || invalid) this.writeHoursToUrl(24);
        this.hours.set(selectedHours);
        this.customHours.set(String(selectedHours));
        if (this.activeHours !== selectedHours) {
          this.activeHours = selectedHours;
          this.requestSnapshot(selectedHours, false);
        }
      }
    });
  }

  selectHours(value: number): void {
    if (!Number.isInteger(value) || value < 1 || value > 2160) return;
    this.rangeWarning.set(null);
    this.customHours.set(String(value));
    if (this.activeHours === value) {
      this.writeHoursToUrl(value);
      this.requestSnapshot(value, true);
      return;
    }
    this.activeHours = value;
    this.hours.set(value);
    this.requestSnapshot(value, false);
    this.writeHoursToUrl(value);
  }

  onCustomHoursInput(event: Event): void {
    this.customHours.set((event.target as HTMLInputElement).value);
  }

  applyCustomHours(): void {
    const parsed = parseHours(this.customHours());
    if (parsed === null) {
      this.rangeWarning.set('Enter a whole number of hours from 1 to 2160.');
      return;
    }
    this.selectHours(parsed);
  }

  refresh(): void {
    this.requestSnapshot(this.hours(), this.snapshot() !== null);
  }

  changeWorkerPage(offset: number): void {
    this.requestWorkerPage(offset);
  }

  retryWorkerPage(): void {
    this.requestWorkerPage(this.workerRetryOffset() ?? this.workerOffset(), true);
  }

  changeExecutionPage(offset: number): void {
    const page = this.executionPage();
    const anchor = this.anchor();
    if (this.snapshotLoading() || !page || !anchor || offset < 0 || offset >= page.total || offset === this.executionOffset() || this.executionLoading()) return;
    this.loadExecutionPage(this.hours(), anchor, this.generation, offset);
  }

  retryExecutionPage(): void {
    const anchor = this.anchor();
    const offset = this.executionRetryOffset() ?? this.executionOffset();
    if (!this.snapshotLoading() && anchor) this.loadExecutionPage(this.hours(), anchor, this.generation, offset);
  }

  onSourceFilterChange(event: Event): void {
    const value = (event.target as HTMLSelectElement).value;
    if (value === 'all' || TOOL_SOURCES.includes(value as StatsToolSource)) this.sourceFilter.set(value as 'all' | StatsToolSource);
  }

  hasNoToolObservations(): boolean {
    const selected = this.selectedToolSeries();
    return selected.length > 0 && selected.every((series) => series.totals.completed === 0);
  }

  toolBucket(source: StatsToolSource, start: string): StatsToolBucket | undefined {
    return this.snapshot()?.tools.find((series) => series.source === source)?.buckets.find((bucket) => bucket.start === start);
  }

  workerBucketForTemplate(worker: StatsWorker, start: string): StatsWorker['buckets'][number] | undefined {
    return worker.buckets.find((bucket) => bucket.start === start);
  }

  bucketDurationSeconds(bucket: {readonly start: string; readonly end: string}): number {
    return (epochMicroseconds(bucket.end) - epochMicroseconds(bucket.start)) / 1_000_000;
  }

  displayedKpi(value: number): string {
    return this.coverageState() === 'unavailable' ? 'Unavailable' : formatCount(value);
  }

  unavailableCoverageMessage(): string {
    const value = this.snapshot();
    if (!value) return 'Tool telemetry is unavailable.';
    const to = epochMicroseconds(value.window.to);
    const collection = epochMicroseconds(value.window.collection_started_at);
    if (to <= collection) return `No recorded tool history is available before collection started on ${formatDateTime(value.window.collection_started_at)}.`;
    return `No tool telemetry is available in this window because it falls before the retained ${value.window.retention_days}-day coverage boundary.`;
  }

  coverageNote(): string {
    const value = this.snapshot();
    if (!value) return '';
    if (this.coverageState() === 'unavailable') return this.unavailableCoverageMessage();
    if (this.coverageState() === 'partial') return `Partial tool coverage: recorded counts cover [${formatDateTime(value.window.tool_coverage_from)}, ${formatDateTime(value.window.to)}). The earlier portion of the requested window is unknown, not zero.`;
    return 'Tool totals include recorded terminal calls only; they are not billing, audit, or network-packet totals.';
  }

  formatDate(value: string | null | undefined): string { return formatDateTime(value); }
  formatRange(value: StatsResponse): string { return `[${formatDateTime(value.window.from)}, ${formatDateTime(value.window.to)})`; }
  formatCount(value: number): string { return formatCount(value); }
  formatPercent(numerator: number, denominator: number): string { return formatPercent(numerator, denominator); }
  formatDuration(value: number | null): string { return formatDuration(value); }
  outcomeLabel(outcome: StatsOutcome): string {
    switch (outcome) {
      case 'success': return 'Success';
      case 'degraded': return 'Degraded';
      case 'blocked': return 'Blocked';
      case 'error': return 'Error';
      case 'rejected': return 'Rejected';
      case 'cancelled': return 'Cancelled';
    }
  }

  coverageBucketLabel(bucket: StatsJobBucket): string {
    const value = this.snapshot();
    if (!value) return 'Coverage unavailable';
    const start = epochMicroseconds(bucket.start);
    const end = epochMicroseconds(bucket.end);
    const coverage = epochMicroseconds(value.window.tool_coverage_from);
    if (this.coverageState() === 'unavailable' || coverage >= end) return 'Unavailable before tool coverage';
    if (coverage <= start) return 'Covered bucket';
    return `Partial; counts cover [${formatDateTime(value.window.tool_coverage_from)}, ${formatDateTime(bucket.end)}), not the earlier portion.`;
  }

  private requestSnapshot(hours: number, preserve: boolean): void {
    const generation = ++this.generation;
    if (!preserve) {
      this.snapshot.set(null);
      this.anchor.set(null);
      this.lastSuccessfulRefresh.set(null);
      this.workerOffset.set(0);
      this.executionOffset.set(0);
      this.executionPage.set(null);
      this.workerError.set(false);
      this.workerRetryOffset.set(null);
      this.executionError.set(false);
      this.executionRetryOffset.set(null);
    }
    this.snapshotError.set(false);
    this.staleSnapshot.set(false);
    this.snapshotLoading.set(true);
    this.workerLoading.set(false);
    this.executionLoading.set(false);
    this.workerRequestId++;
    this.executionRequestId++;
    this.workerTargetOffset = null;
    this.executionTargetOffset = null;
    this.workerPageRequests.next(null);
    this.executionPageRequests.next(null);
    this.snapshotRequests.next({hours, generation});
  }

  private requestWorkerPage(offset: number, retry = false): void {
    const value = this.snapshot();
    const anchor = this.anchor();
    if (this.snapshotLoading() || !value || !anchor || offset < 0 || offset >= value.workers.total || (!retry && offset === this.workerOffset()) || this.workerLoading()) return;
    this.workerLoading.set(true);
    this.workerError.set(false);
    this.workerRetryOffset.set(null);
    this.workerTargetOffset = offset;
    this.workerPageRequests.next({hours: this.hours(), anchor, offset, generation: this.generation, requestId: ++this.workerRequestId});
  }

  private loadExecutionPage(hours: number, anchor: string, generation: number, offset: number): void {
    this.executionError.set(false);
    this.executionRetryOffset.set(null);
    this.executionTargetOffset = offset;
    this.executionPageRequests.next({hours, anchor, offset, generation, requestId: ++this.executionRequestId});
  }

  private writeHoursToUrl(hours: number): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: {hours: String(hours)},
      queryParamsHandling: 'merge',
      replaceUrl: true,
    });
  }

  private totalsFor(sources: readonly StatsToolSource[]): StatsToolCounts {
    return sources.reduce((totals, source) => addCounts(totals, this.snapshot()?.tools.find((series) => series.source === source)?.totals ?? ZERO_COUNTS), ZERO_COUNTS);
  }

  private selectedToolSeries(): readonly StatsToolSeries[] {
    const series = this.snapshot()?.tools ?? [];
    return this.sourceFilter() === 'all' ? series.filter((item) => TOOL_SOURCES.includes(item.source)) : series.filter((item) => item.source === this.sourceFilter());
  }

  private timeTooltip(raw: unknown, buckets: readonly {readonly start: string; readonly end: string}[]): string {
    const points = Array.isArray(raw) ? raw : [raw];
    const first = points[0] as {dataIndex?: unknown} | undefined;
    const index = typeof first?.dataIndex === 'number' ? first.dataIndex : -1;
    const bucket = buckets[index];
    const lines = points.map((point) => {
      const item = point as {seriesName?: unknown; value?: unknown};
      const rawValue = Array.isArray(item.value) ? item.value[1] : item.value;
      return `${String(item.seriesName ?? 'Recorded value')}: ${rawValue === null || rawValue === undefined ? 'unavailable' : String(rawValue)}`;
    });
    return `${bucket ? formatInterval(bucket.start, bucket.end) : 'Time bucket'}\n${lines.join('\n')}`;
  }

  private issueChartOption(items: readonly StatsIssue[], description: string): EChartsCoreOption {
    const labels = items.map(issueLabel);
    return {
      aria: {enabled: true, description},
      grid: {left: '34%', right: 26, top: 20, bottom: 28},
      tooltip: {
        trigger: 'axis',
        renderMode: 'richText',
        formatter: (raw: unknown) => {
          const points = Array.isArray(raw) ? raw : [raw];
          const point = points[0] as {dataIndex?: unknown; value?: unknown} | undefined;
          const index = typeof point?.dataIndex === 'number' ? point.dataIndex : -1;
          return `${labels[index] ?? 'Issue'}\nOccurrences: ${String(point?.value ?? '—')}`;
        },
      },
      xAxis: {type: 'value', name: 'Occurrences', minInterval: 1},
      yAxis: {type: 'category', data: labels, inverse: true, axisLabel: {width: 300, overflow: 'truncate'}},
      series: [{name: 'Occurrences', type: 'bar', data: items.map((item) => item.occurrences), itemStyle: {color: '#b54444'}}],
    };
  }

  private heatmapMarker(raw: unknown): string {
    const point = raw as {data?: unknown} | null;
    const data = Array.isArray(point?.data) ? point.data : [];
    return Number(data[4] ?? 0) > 0 ? '!' : '';
  }

  private heatmapTooltip(raw: unknown, buckets: readonly StatsJobBucket[], workerTokens: readonly string[]): string {
    const point = raw as {data?: unknown} | null;
    const data = Array.isArray(point?.data) ? point.data : [];
    const bucketIndex = Number(data[0]);
    const workerIndex = Number(data[1]);
    const bucket = buckets[bucketIndex];
    const fraction = Number(data[2] ?? 0);
    const seconds = Number(data[3] ?? 0);
    const incomplete = Number(data[4] ?? 0);
    const lines = [
      workerTokens[workerIndex] ?? 'Worker',
      bucket ? formatInterval(bucket.start, bucket.end) : 'Time bucket unavailable',
      `Recorded busy time: ${seconds.toFixed(1)} seconds`,
      `Recorded busy fraction: ${(fraction * 100).toFixed(1)}%`,
      `Incomplete spans: ${incomplete}${incomplete > 0 ? ' (marker)' : ''}`,
    ];
    return lines.join('\n');
  }
}
