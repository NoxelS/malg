import {DatePipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {HttpClient} from '@angular/common/http';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';

interface ResearchJob {
  readonly job_id: string;
  readonly kind: string;
  readonly status: string;
  readonly campaign_id: string | null;
  readonly icp_id: string | null;
  readonly account_match_id: string | null;
  readonly attempt_count: number;
  readonly created_at: string;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly failure_detail: string | null;
}

@Component({
  selector: 'app-jobs-page',
  imports: [DatePipe, TuiBadge, TuiButton, PageHeaderComponent, PageLayoutComponent, StateMessageComponent],
  templateUrl: './jobs-page.html',
  styleUrl: './jobs-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class JobsPage implements OnInit {
  private readonly http = inject(HttpClient);

  protected readonly jobs = signal<readonly ResearchJob[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal(false);
  protected readonly cancelError = signal(false);
  protected readonly pendingJobId = signal<string | null>(null);

  ngOnInit(): void {
    this.http.get<readonly ResearchJob[]>('/api/v1/jobs').subscribe({
      next: (jobs) => {
        this.jobs.set(jobs);
        this.loading.set(false);
      },
      error: () => {
        this.error.set(true);
        this.loading.set(false);
      },
    });
  }

  protected cancelJob(job: ResearchJob): void {
    if (job.status !== 'queued' || this.pendingJobId()) return;
    this.pendingJobId.set(job.job_id);
    this.cancelError.set(false);
    this.http.post<ResearchJob>(`/api/v1/jobs/${job.job_id}/cancel`, {}).subscribe({
      next: (updated) => {
        this.jobs.update((jobs) => jobs.map((item) => item.job_id === updated.job_id ? updated : item));
        this.pendingJobId.set(null);
      },
      error: () => {
        this.pendingJobId.set(null);
        this.cancelError.set(true);
      },
    });
  }

}
