import {DatePipe, JsonPipe} from '@angular/common';
import {HttpErrorResponse} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, DestroyRef, OnDestroy, OnInit, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {ActivatedRoute, RouterLink} from '@angular/router';
import {forkJoin, of} from 'rxjs';
import {catchError, finalize, tap} from 'rxjs/operators';
import {ApiService, AgentRun, AgentTraceEvent, AgentTraceEventPage, AgentTurn, CrmStatus, JobWrites, JsonValue, ResearchJob, StageRecord} from './api-service';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';
import {TracePropertyListComponent} from './components/trace-property-list.component';
import {TraceTurnCardComponent} from './components/trace-turn-card.component';

interface SectionFailure {
  readonly endpoint: 'LLM turns' | 'Trace events';
  readonly status: number;
}

interface TraceSection<T> {
  readonly loading: boolean;
  readonly data: T | null;
  readonly failure: SectionFailure | null;
}

interface RunState {
  readonly turns: TraceSection<readonly AgentTurn[]>;
  readonly events: TraceSection<readonly AgentTraceEvent[]>;
  readonly nextAfterEventId: number | null;
  readonly canLoadMore: boolean;
  readonly loadingMore: boolean;
  readonly loadingMoreFailure: SectionFailure | null;
  readonly failedEventCursor: number | null;
}

const emptyRunState = (): RunState => ({
  turns: {loading: false, data: null, failure: null},
  events: {loading: false, data: null, failure: null},
  nextAfterEventId: null,
  canLoadMore: false,
  loadingMore: false,
  loadingMoreFailure: null,
  failedEventCursor: null,
});

interface DiscoveryCandidate {
  readonly name: string;
  readonly website: string;
  readonly reason: string;
}

@Component({
  selector: 'app-job-detail-page',
  imports: [DatePipe, JsonPipe, RouterLink, TuiBadge, TuiButton, PageHeaderComponent, PageLayoutComponent, StateMessageComponent, TracePropertyListComponent, TraceTurnCardComponent],
  templateUrl: './job-detail-page.html',
  styleUrl: './job-detail-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class JobDetailPage implements OnDestroy, OnInit {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly job = signal<ResearchJob | null>(null);
  protected readonly runs = signal<readonly AgentRun[]>([]);
  protected readonly stages = signal<readonly StageRecord[]>([]);
  protected readonly writes = signal<JobWrites | null>(null);
  protected readonly inputPayload = signal<JsonValue>(null);
  protected readonly stagesUnavailable = signal(false);
  protected readonly writesUnavailable = signal(false);
  protected readonly crm = signal<CrmStatus | null>(null);
  protected readonly candidatePending = signal<string | null>(null);
  protected readonly candidateJobs = signal<Record<string, string>>({});
  protected readonly cancellingJob = signal(false);
  protected readonly loading = signal(true);
  protected readonly error = signal<'not-found' | 'unavailable' | null>(null);
  protected readonly traceUnavailable = signal(false);
  protected readonly refreshing = signal(false);
  protected readonly retryingJob = signal(false);
  protected readonly jobActionError = signal(false);
  protected readonly runStates = signal<Record<string, RunState>>({});
  private jobId = '';
  private pollHandle?: number;
  private generation = 0;
  private inFlight = false;
  ngOnInit(): void {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      const jobId = params.get('jobId');
      if (!jobId) return;
      ++this.generation; this.inFlight = false; clearTimeout(this.pollHandle);
      this.jobId = jobId;
      this.job.set(null);
      this.runs.set([]);
      this.runStates.set({});
      this.stages.set([]); this.writes.set(null); this.inputPayload.set(null);
      this.candidateJobs.set({}); this.error.set(null);
      this.loadCrm();
      this.loadJob(false);
    });
  }
  ngOnDestroy(): void { ++this.generation; clearTimeout(this.pollHandle); }
  protected retryJob(): void {
    const job = this.job();
    if (!job || job.status !== 'failed' || job.data_origin !== 'twenty' || !this.canPublish() || this.retryingJob()) return;
    this.retryingJob.set(true); this.jobActionError.set(false);
    this.api.retryJob(this.jobId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: (updated) => { this.job.set(updated); this.retryingJob.set(false); this.loadJob(true); }, error: () => { this.retryingJob.set(false); this.jobActionError.set(true); this.loadCrm(); }});
  }

  protected refresh(): void { this.loadJob(true); this.loadCrm(); }

  protected canPublish(): boolean { return this.crm()?.available === true && this.crm()?.schema_compatible === true; }

  private loadCrm(): void {
    const generation = this.generation;
    this.api.getCrmStatus().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (status) => { if (generation === this.generation) this.crm.set(status); },
      error: () => { if (generation === this.generation) this.crm.set(null); },
    });
  }

  protected cancelJob(): void {
    if (!this.job() || !['queued', 'running'].includes(this.job()!.status) || this.cancellingJob()) return;
    this.cancellingJob.set(true); this.jobActionError.set(false);
    this.api.cancelJob(this.jobId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (job) => { this.job.set(job); this.cancellingJob.set(false); this.loadJob(true); },
      error: () => { this.cancellingJob.set(false); this.jobActionError.set(true); },
    });
  }

  protected candidates(): readonly DiscoveryCandidate[] {
    const stage = [...this.stages()].reverse().find((item) => item.stage_key === 'discovery.research');
    const payload = stage?.payload;
    if (!payload || typeof payload !== 'object' || Array.isArray(payload) || !Array.isArray(payload['candidates'])) return [];
    return payload['candidates'].flatMap((candidate) => {
      if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate) || typeof candidate['name'] !== 'string' || typeof candidate['website'] !== 'string') return [];
      return [{name: candidate['name'], website: candidate['website'], reason: typeof candidate['reason'] === 'string' ? candidate['reason'] : ''}];
    });
  }

  protected enqueueCandidate(candidate: DiscoveryCandidate): void {
    const job = this.job();
    if (!job?.icp_id || job.data_origin !== 'twenty' || !this.canPublish() || this.candidatePending() || this.candidateJobs()[candidate.website]) return;
    this.candidatePending.set(candidate.website); this.jobActionError.set(false);
    this.api.submitJob({kind: 'account', icp_id: job.icp_id, name: candidate.name, website: candidate.website}).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (created) => { this.candidateJobs.update((jobs) => ({...jobs, [candidate.website]: created.job_id})); this.candidatePending.set(null); },
      error: () => { this.candidatePending.set(null); this.jobActionError.set(true); },
    });
  }

  protected loadRun(run: AgentRun, force = false): void {
    const current = this.runStates()[run.run_id];
    if (!force && current && !current.turns.loading && !current.events.loading && (current.turns.data !== null || current.turns.failure !== null) && (current.events.data !== null || current.events.failure !== null)) return;
    const base = emptyRunState();
    this.runStates.update((states) => ({...states, [run.run_id]: {
      ...(current ?? base),
      turns: {loading: true, data: force ? null : current?.turns.data ?? null, failure: null},
      events: {loading: true, data: force ? null : current?.events.data ?? null, failure: null},
      nextAfterEventId: null,
      canLoadMore: false,
      loadingMore: false,
      loadingMoreFailure: null,
      failedEventCursor: null,
    }}));
    this.loadTurns(run);
    this.loadEvents(run);
  }

  protected retryTurns(run: AgentRun): void {
    this.runStates.update((states) => ({...states, [run.run_id]: {...(states[run.run_id] ?? emptyRunState()), turns: {loading: true, data: null, failure: null}}}));
    this.loadTurns(run);
  }

  protected retryEvents(run: AgentRun): void {
    this.runStates.update((states) => ({...states, [run.run_id]: {...(states[run.run_id] ?? emptyRunState()), events: {loading: true, data: null, failure: null}}}));
    this.loadEvents(run);
  }

  protected loadMoreEvents(run: AgentRun): void {
    const state = this.stateFor(run);
    if (!state.canLoadMore || state.loadingMore || state.nextAfterEventId === null) return;
    const cursor = state.nextAfterEventId;
    this.setRunState(run.run_id, {...state, loadingMore: true, loadingMoreFailure: null, failedEventCursor: null});
    this.api.listRunEvents(run.run_id, cursor, 100).subscribe({
      next: (page) => this.appendEvents(run.run_id, page),
      error: (failure) => this.setRunState(run.run_id, {...this.stateFor(run), loadingMore: false, loadingMoreFailure: this.failure('Trace events', failure), failedEventCursor: cursor}),
    });
  }

  protected retryMoreEvents(run: AgentRun): void {
    const cursor = this.stateFor(run).failedEventCursor;
    if (cursor === null) return;
    this.setRunState(run.run_id, {...this.stateFor(run), loadingMore: true, loadingMoreFailure: null});
    this.api.listRunEvents(run.run_id, cursor, 100).subscribe({
      next: (page) => this.appendEvents(run.run_id, page),
      error: (failure) => this.setRunState(run.run_id, {...this.stateFor(run), loadingMore: false, loadingMoreFailure: this.failure('Trace events', failure)}),
    });
  }

  protected stateFor(run: AgentRun): RunState { return this.runStates()[run.run_id] ?? emptyRunState(); }
  protected display(value: string | number | null): string | number { return value ?? '—'; }
  protected diagnostic(failure: SectionFailure): string { return `${failure.endpoint} request failed (HTTP ${failure.status}).`; }
  protected isNotFound(error: unknown): boolean { return error instanceof HttpErrorResponse && error.status === 404; }

  private loadTurns(run: AgentRun): void {
    this.api.listRunTurns(run.run_id).subscribe({
      next: (turns) => this.setRunState(run.run_id, {...this.stateFor(run), turns: {loading: false, data: turns, failure: null}}),
      error: (failure) => this.setRunState(run.run_id, {...this.stateFor(run), turns: {loading: false, data: null, failure: this.failure('LLM turns', failure)}}),
    });
  }

  private loadEvents(run: AgentRun): void {
    this.api.listRunEvents(run.run_id, undefined, 100).subscribe({
      next: (page) => this.setRunState(run.run_id, {...this.stateFor(run), events: {loading: false, data: page.items, failure: null}, nextAfterEventId: page.next_after_event_id, canLoadMore: page.items.length > 0 && page.next_after_event_id !== null, loadingMoreFailure: null, failedEventCursor: null}),
      error: (failure) => this.setRunState(run.run_id, {...this.stateFor(run), events: {loading: false, data: null, failure: this.failure('Trace events', failure)}, canLoadMore: false}),
    });
  }

  private appendEvents(runId: string, page: AgentTraceEventPage): void {
    const state = this.stateFor({run_id: runId} as AgentRun);
    const existing = state.events.data ?? [];
    const known = new Set(existing.map((event) => event.event_id));
    const events = [...existing, ...page.items.filter((event) => !known.has(event.event_id))].sort((a, b) => a.event_id - b.event_id);
    this.setRunState(runId, {...state, events: {loading: false, data: events, failure: null}, nextAfterEventId: page.next_after_event_id, canLoadMore: page.items.length > 0 && page.next_after_event_id !== null, loadingMore: false, loadingMoreFailure: null, failedEventCursor: null});
  }

  private setRunState(runId: string, state: RunState): void { this.runStates.update((states) => ({...states, [runId]: state})); }

  private failure(endpoint: SectionFailure['endpoint'], error: unknown): SectionFailure {
    return {endpoint, status: error instanceof HttpErrorResponse && error.status > 0 ? error.status : 0};
  }

  private loadJob(isRefresh: boolean): void {
    if (this.inFlight) return;
    this.inFlight = true;
    clearTimeout(this.pollHandle);
    if (isRefresh) this.refreshing.set(true); else this.loading.set(true);
    const generation = this.generation;
    const current = () => generation === this.generation;
    const job = this.api.getJob(this.jobId).pipe(
      tap((value) => { if (current()) { this.job.set(value); this.error.set(null); } }),
      catchError((failure) => { if (current()) this.error.set(this.isNotFound(failure) ? 'not-found' : 'unavailable'); return of(null); }),
    );
    const runs = this.api.listJobRuns(this.jobId).pipe(
      tap((value) => { if (current()) { this.runs.set(value); this.traceUnavailable.set(false); } }),
      catchError(() => { if (current()) this.traceUnavailable.set(true); return of(null); }),
    );
    const stages = this.api.listJobStages(this.jobId).pipe(
      tap((value) => { if (current()) { this.stages.set(value.items); this.inputPayload.set(value.input_payload); this.stagesUnavailable.set(false); } }),
      catchError(() => { if (current()) this.stagesUnavailable.set(true); return of(null); }),
    );
    const writes = this.api.listJobWrites(this.jobId).pipe(
      tap((value) => { if (current()) { this.writes.set(value); this.writesUnavailable.set(false); } }),
      catchError(() => { if (current()) this.writesUnavailable.set(true); return of(null); }),
    );
    forkJoin({job, runs, stages, writes}).pipe(
      takeUntilDestroyed(this.destroyRef),
      finalize(() => {
        if (!current()) return;
        this.inFlight = false; this.loading.set(false); this.refreshing.set(false);
        const job = this.job();
        if (job && (['queued', 'running'].includes(job.status) || this.writes()?.side_effects_pending || this.writesUnavailable())) {
          this.pollHandle = window.setTimeout(() => this.loadJob(true), 2000);
        }
      }),
    ).subscribe();
  }
}
