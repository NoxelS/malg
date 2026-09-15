import {DatePipe} from '@angular/common';
import {HttpErrorResponse} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {ActivatedRoute, RouterLink} from '@angular/router';
import {forkJoin, of, throwError} from 'rxjs';
import {catchError} from 'rxjs/operators';
import {ApiService, AgentRun, AgentTraceEvent, AgentTraceEventPage, AgentTurn, ResearchJob} from './api-service';
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

@Component({
  selector: 'app-job-detail-page',
  imports: [DatePipe, RouterLink, TuiBadge, TuiButton, PageHeaderComponent, PageLayoutComponent, StateMessageComponent, TracePropertyListComponent, TraceTurnCardComponent],
  templateUrl: './job-detail-page.html',
  styleUrl: './job-detail-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class JobDetailPage implements OnInit {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  protected readonly job = signal<ResearchJob | null>(null);
  protected readonly runs = signal<readonly AgentRun[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal<'not-found' | 'unavailable' | null>(null);
  protected readonly traceUnavailable = signal(false);
  protected readonly refreshing = signal(false);
  protected readonly runStates = signal<Record<string, RunState>>({});
  private jobId = '';

  ngOnInit(): void {
    this.route.paramMap.subscribe((params) => {
      const jobId = params.get('jobId');
      if (!jobId) return;
      this.jobId = jobId;
      this.job.set(null);
      this.runs.set([]);
      this.runStates.set({});
      this.loadJob(false);
    });
  }

  protected refresh(): void { this.loadJob(true); }

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
    if (isRefresh) this.refreshing.set(true); else this.loading.set(true);
    this.error.set(null);
    this.traceUnavailable.set(false);
    const runs = this.api.listJobRuns(this.jobId).pipe(catchError((failure) => {
      if (this.isNotFound(failure)) {
        this.traceUnavailable.set(true);
        return of<readonly AgentRun[]>([]);
      }
      return throwError(() => failure);
    }));
    forkJoin({job: this.api.getJob(this.jobId), runs}).subscribe({
      next: ({job, runs: traceRuns}) => {
        this.job.set(job);
        this.runs.set(traceRuns);
        this.loading.set(false);
        this.refreshing.set(false);
        if (isRefresh) {
          for (const run of traceRuns) if (this.runStates()[run.run_id]) this.loadRun(run, true);
        }
      },
      error: (failure) => {
        this.loading.set(false);
        this.refreshing.set(false);
        this.error.set(this.isNotFound(failure) ? 'not-found' : 'unavailable');
      },
    });
  }
}
