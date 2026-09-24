import {DatePipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, WritableSignal, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {FormsModule} from '@angular/forms';
import {RouterLink} from '@angular/router';
import {Observable, map} from 'rxjs';
import {ApiService, CrmListItem, CrmPage, CrmStatus, JobKind, ResearchJob} from './api-service';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';

type Selector = 'campaigns' | 'icps' | 'accounts' | 'people';
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
  protected readonly accounts = signal<readonly CrmListItem[]>([]);
  protected readonly people = signal<readonly CrmListItem[]>([]);
  protected readonly selectedKind = signal<JobKind>('discovery');
  protected selectedCampaign = '';
  protected selectedIcp = '';
  protected selectedAccount = '';
  protected selectedPerson = '';
  protected accountQuery = '';
  protected amount = 1;
  protected discoveryLimit = 10;
  protected readonly accountCursor = signal<string | null>(null);
  protected readonly campaignCursor = signal<string | null>(null);
  protected readonly personCursor = signal<string | null>(null);
  protected readonly icpCursor = signal<string | null>(null);
  protected readonly selectorState = signal<Record<Selector, LoadState>>({campaigns: 'idle', icps: 'idle', accounts: 'idle', people: 'idle'});
  protected candidateName = '';
  protected candidateWebsite = '';
  protected readonly enqueueError = signal('');
  protected readonly enqueuePending = signal(false);
  protected readonly actionError = signal<'cancel' | 'delete' | null>(null);
  protected readonly pendingJobId = signal<string | null>(null);
  private readonly generations: Record<Selector, number> = {campaigns: 0, icps: 0, accounts: 0, people: 0};

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
          if (!this.accounts().length) this.loadAccounts();
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
  protected loadAccounts(): void { this.loadPage('accounts', this.api.listCrmAccounts(this.accountQuery, this.accountCursor() ?? undefined), this.accounts, this.accountCursor); }
  protected loadPeople(): void {
    if (this.selectedAccount) this.loadPage('people', this.api.listCrmPeople(this.selectedAccount, this.personCursor() ?? undefined), this.people, this.personCursor);
  }
  protected accountQueryChanged(query: string): void {
    this.accountQuery = query;
    this.selectedAccount = ''; this.accounts.set([]); this.accountCursor.set(null); ++this.generations.accounts;
    this.selectorState.update((state) => ({...state, accounts: 'idle'}));
    this.accountChanged();
    this.loadAccounts();
  }
  protected campaignChanged(): void {
    this.selectedIcp = ''; this.icps.set([]); this.icpCursor.set(null); ++this.generations.icps;
    this.selectorState.update((state) => ({...state, icps: 'idle'}));
    this.loadIcps();
  }
  protected accountChanged(): void {
    this.selectedPerson = ''; this.people.set([]); this.personCursor.set(null); ++this.generations.people;
    this.selectorState.update((state) => ({...state, people: 'idle'}));
    this.loadPeople();
  }
  protected kindChanged(): void { this.enqueueError.set(''); }
  protected canEnqueue(): boolean {
    if (this.crmError() || !this.crm()?.available || this.crm()?.schema_compatible !== true) return false;
    const kind = this.selectedKind();
    if (['campaign', 'icp', 'account'].includes(kind) && (!Number.isInteger(this.amount) || this.amount < 1 || this.amount > 100)) return false;
    if (kind === 'campaign') return true;
    if (kind === 'icp') return Boolean(this.selectedCampaign);
    if (kind === 'discovery') return Boolean(this.selectedIcp) && Number.isInteger(this.discoveryLimit) && this.discoveryLimit >= 1 && this.discoveryLimit <= 20;
    if (kind === 'account') return Boolean(this.selectedIcp) && (!(this.candidateName || this.candidateWebsite) || this.amount === 1);
    if (kind === 'account_hydration') return Boolean(this.selectedAccount);
    if (kind === 'person') return Boolean(this.selectedAccount && this.selectedIcp);
    return Boolean(this.selectedAccount && this.selectedPerson);
  }
  protected enqueue(): void {
    if (!this.canEnqueue() || this.enqueuePending()) return;
    const kind = this.selectedKind();
    this.enqueuePending.set(true); this.enqueueError.set('');
    let request: Observable<readonly ResearchJob[]>;
    if (kind === 'campaign') request = this.api.enqueueCampaignJobs(this.amount);
    else if (kind === 'icp') request = this.api.enqueueIcpJobs(this.selectedCampaign, this.amount);
    else if (kind === 'account' && !(this.candidateName || this.candidateWebsite)) request = this.api.enqueueAccountJobs(this.selectedIcp, this.amount, this.selectedCampaign || undefined);
    else {
      const single = kind === 'discovery' ? this.api.submitJob({kind, icp_id: this.selectedIcp, limit: this.discoveryLimit, ...(this.selectedCampaign ? {campaign_id: this.selectedCampaign} : {})})
        : kind === 'account' ? this.api.submitJob({kind, icp_id: this.selectedIcp, ...(this.selectedCampaign ? {campaign_id: this.selectedCampaign} : {}), ...(this.candidateName ? {name: this.candidateName} : {}), ...(this.candidateWebsite ? {website: this.candidateWebsite} : {})})
        : kind === 'account_hydration' ? this.api.submitJob({kind, account_id: this.selectedAccount})
        : kind === 'person' ? this.api.submitJob({kind, account_id: this.selectedAccount, icp_id: this.selectedIcp})
        : this.api.submitJob({kind: 'person_hydration', person_id: this.selectedPerson});
      request = single.pipe(map((job) => [job]));
    }
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (added) => { this.jobs.update((jobs) => [...added, ...jobs]); this.enqueuePending.set(false); },
      error: () => { this.enqueuePending.set(false); this.enqueueError.set('The job could not be started. Check CRM compatibility and the selected scope.'); },
    });
  }
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
