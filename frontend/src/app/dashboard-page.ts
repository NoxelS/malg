import {DatePipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {ApiService} from './api-service';
import {forkJoin} from 'rxjs';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {SectionHeadingComponent} from './components/section-heading.component';
import {StateMessageComponent} from './components/state-message.component';
import {SummaryCardComponent} from './components/summary-card.component';
import {SummaryGridComponent} from './components/summary-grid.component';
interface DashboardJobCounts {
  readonly queued: number;
  readonly running: number;
  readonly succeeded: number;
  readonly failed: number;
  readonly cancelled: number;
}

interface DashboardJobDuration {
  readonly kind: string;
  readonly average_duration_seconds: number | null;
}

interface DashboardSummary {
  readonly active_workers: number;
  readonly jobs: DashboardJobCounts;
  readonly job_durations: readonly DashboardJobDuration[];
}

interface WorkerJobSummary {
  readonly job_id: string;
  readonly kind: string;
  readonly attempt_count: number;
  readonly claimed_at: string;
}

interface WorkerSummary {
  readonly worker_id: string;
  readonly online_since: string;
  readonly last_seen_at: string;
  readonly status: 'idle' | 'running';
  readonly job: WorkerJobSummary | null;
}

interface WorkerView extends WorkerSummary {
  readonly runningFor: string | null;
}

@Component({
  selector: 'app-dashboard-page',
  imports: [DatePipe, RouterLink, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent, SummaryCardComponent, SummaryGridComponent],
  templateUrl: './dashboard-page.html',
  styleUrl: './dashboard-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class DashboardPage implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly summary = signal<DashboardSummary | null>(null);
  protected readonly workers = signal<readonly WorkerView[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal(false);

  ngOnInit(): void {
    forkJoin({
      summary: this.api.listDashboard(),
      workers: this.api.listWorkers(),
    }).subscribe({
      next: ({summary, workers}) => {
        this.summary.set(summary);
        this.workers.set(workers.map((worker: WorkerSummary) => ({
          ...worker,
          runningFor: worker.job ? this.durationSince(worker.job.claimed_at) : null,
        })));
        this.loading.set(false);
      },
      error: () => {
        this.error.set(true);
        this.loading.set(false);
      },
    });
  }

  protected formatAverageDuration(seconds: number | null): string {
    return seconds === null ? 'No successful jobs yet' : this.formatDuration(Math.round(seconds));
  }

  private durationSince(timestamp: string): string {
    return this.formatDuration(Math.max(0, Math.floor((Date.now() - Date.parse(timestamp)) / 1000)));
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
