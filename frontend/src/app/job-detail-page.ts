import {DatePipe, JsonPipe} from '@angular/common';
import {HttpErrorResponse} from '@angular/common/http';
import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {ActivatedRoute, RouterLink} from '@angular/router';
import {catchError, forkJoin, of, throwError} from 'rxjs';
import {ApiService, AgentRun, AgentTraceEvent, AgentTurn, ResearchJob} from './api-service';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';

interface RunState {
  readonly loading: boolean;
  readonly error: boolean;
  readonly turns: readonly AgentTurn[];
  readonly events: readonly AgentTraceEvent[];
  readonly nextAfterEventId: number | null;
  readonly canLoadMore: boolean;
  readonly loadingMore: boolean;
}

const emptyRunState = (): RunState => ({loading: false, error: false, turns: [], events: [], nextAfterEventId: null, canLoadMore: true, loadingMore: false});

@Component({
  selector: 'app-job-detail-page',
  imports: [DatePipe, JsonPipe, RouterLink, TuiBadge, TuiButton, PageHeaderComponent, PageLayoutComponent, StateMessageComponent],
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
      if (jobId) {
        this.jobId = jobId;
        this.loadJob(false);
      }
    });
  }

  protected refresh(): void { this.loadJob(true); }

  protected loadRun(run: AgentRun, force = false): void {
    const current = this.runStates()[run.run_id];
    if (!force && (current?.loading || (current && !current.error))) return;
    this.runStates.update((states) => ({...states, [run.run_id]: {...(current ?? emptyRunState()), loading: true, error: false}}));
    forkJoin({turns: this.api.listRunTurns(run.run_id), events: this.api.listRunEvents(run.run_id, undefined, 100)}).subscribe({
      next: ({turns, events}) => this.runStates.update((states) => ({...states, [run.run_id]: {loading: false, error: false, turns, events: events.items, nextAfterEventId: events.next_after_event_id, canLoadMore: events.items.length > 0 && events.next_after_event_id !== null, loadingMore: false}})),
      error: () => this.runStates.update((states) => ({...states, [run.run_id]: {...(states[run.run_id] ?? emptyRunState()), loading: false, error: true}})),
    });
  }

  protected retryRun(run: AgentRun): void {
    this.runStates.update((states) => ({...states, [run.run_id]: emptyRunState()}));
    this.loadRun(run);
  }

  protected loadMoreEvents(run: AgentRun): void {
    const state = this.runStates()[run.run_id];
    if (!state?.canLoadMore || state.loadingMore || state.nextAfterEventId === null) return;
    const cursor = state.nextAfterEventId;
    this.runStates.update((states) => ({...states, [run.run_id]: {...state, loadingMore: true, error: false}}));
    this.api.listRunEvents(run.run_id, cursor, 100).subscribe({
      next: (page) => this.runStates.update((states) => {
        const current = states[run.run_id] ?? state;
        const known = new Set(current.events.map((event) => event.event_id));
        const events = [...current.events, ...page.items.filter((event) => !known.has(event.event_id))];
        return {...states, [run.run_id]: {...current, events, nextAfterEventId: page.next_after_event_id, canLoadMore: page.items.length > 0 && page.next_after_event_id !== null, loadingMore: false}};
      }),
      error: () => this.runStates.update((states) => ({...states, [run.run_id]: {...(states[run.run_id] ?? state), loadingMore: false, error: true}})),
    });
  }

  protected stateFor(run: AgentRun): RunState { return this.runStates()[run.run_id] ?? emptyRunState(); }
  protected display(value: string | number | null): string | number { return value ?? '—'; }
  protected isNotFound(error: unknown): boolean { return error instanceof HttpErrorResponse && error.status === 404; }

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
        this.job.set(job); this.runs.set(traceRuns); this.loading.set(false); this.refreshing.set(false);
        if (isRefresh) {
          const loaded = this.runStates();
          for (const run of traceRuns) if (loaded[run.run_id]) this.loadRun(run, true);
        }
      },
      error: (failure) => { this.loading.set(false); this.refreshing.set(false); this.error.set(this.isNotFound(failure) ? 'not-found' : 'unavailable'); },
    });
  }
}
