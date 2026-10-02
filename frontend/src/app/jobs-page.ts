import {DatePipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, WritableSignal, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {RouterLink} from '@angular/router';
import {Observable} from 'rxjs';
import {ApiService, CrmListItem, CrmPage, CrmStatus, JobKind, ResearchJob} from './api-service';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';

type Selector = 'campaigns' | 'icps';
type LoadState = 'idle' | 'loading' | 'error';

@Component({selector: 'app-jobs-page', imports: [DatePipe, FormsModule, RouterLink, TuiBadge, TuiButton, PageHeaderComponent, PageLayoutComponent, StateMessageComponent], changeDetection: ChangeDetectionStrategy.OnPush, templateUrl: './jobs-page.html', styleUrl: './jobs-page.scss'})
export class JobsPage implements OnInit {
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);
  protected readonly jobs = signal<readonly ResearchJob[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal(false);
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
  protected readonly actionError = signal<'cancel' | 'delete' | null>(null);
  protected readonly pendingJobId = signal<string | null>(null);
  private readonly generations: Record<Selector, number> = {campaigns: 0, icps: 0};

  ngOnInit(): void {
    this.api.listJobs().pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (jobs) => { this.jobs.set(jobs); this.loading.set(false); },
      error: () => { this.error.set(true); this.loading.set(false); },
    });
    this.refreshCrm();
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
      next: (added) => { this.jobs.update((jobs) => [added, ...jobs]); this.enqueuePending.set(false); },
      error: () => { this.enqueuePending.set(false); this.enqueueError.set('The job could not be started. Check CRM compatibility and the selected scope.'); },
    });
  }
  private validCount(value: number, maximum: number): boolean { return Number.isInteger(value) && value >= 1 && value <= maximum; }
  protected isDeletable(job: ResearchJob): boolean { return ['cancelled', 'succeeded', 'failed'].includes(job.status); }
  protected cancelJob(job: ResearchJob): void {
    if (!['queued', 'running'].includes(job.status) || this.pendingJobId()) return;
    this.pendingJobId.set(job.job_id); this.actionError.set(null);
    this.api.cancelJob(job.job_id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: (updated) => { this.jobs.update((jobs) => jobs.map((item) => item.job_id === updated.job_id ? updated : item)); this.pendingJobId.set(null); }, error: () => { this.pendingJobId.set(null); this.actionError.set('cancel'); }});
  }
  protected deleteJob(job: ResearchJob): void {
    if (!this.isDeletable(job) || this.pendingJobId()) return;
    this.pendingJobId.set(job.job_id); this.actionError.set(null);
    this.api.deleteJob(job.job_id).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({next: () => { this.jobs.update((jobs) => jobs.filter((item) => item.job_id !== job.job_id)); this.pendingJobId.set(null); }, error: () => { this.pendingJobId.set(null); this.actionError.set('delete'); }});
  }
}
