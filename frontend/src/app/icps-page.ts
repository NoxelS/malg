import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {ApiService} from './api-service';
import {RouterLink} from '@angular/router';
import {Observable, forkJoin, map} from 'rxjs';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge, TuiChip} from '@taiga-ui/kit';
import {ChipListComponent} from './components/chip-list.component';
import {DetailDisclosureComponent, ExpandableCardComponent} from './components/expandable-card.component';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {SectionHeadingComponent} from './components/section-heading.component';
import {StateMessageComponent} from './components/state-message.component';
import {SummaryCardComponent} from './components/summary-card.component';
import {SummaryGridComponent} from './components/summary-grid.component';

interface CampaignReference {
  readonly campaign_id: string;
  readonly title: string;
}

interface ICPIdentity {
  readonly industry: string;
  readonly geography: string;
  readonly company_size_band: string;
  readonly primary_workflow: string;
  readonly primary_buyer_role: string;
  readonly deployment_posture: string;
}

interface ICP {
  readonly campaign_id: string;
  readonly icp_id: string;
  readonly title: string;
  readonly identity: ICPIdentity;
  readonly profile_summary: string;
  readonly firmographics: {
    readonly industries: readonly string[];
    readonly countries: readonly string[];
    readonly employee_range: string;
    readonly turnover_range: string | null;
  };
  readonly operating_profile: {
    readonly target_workflows: readonly string[];
    readonly desired_outcomes: readonly string[];
  };
  readonly pains_and_jobs: readonly {readonly pain: string; readonly job_to_be_done: string}[];
  readonly buying_committee: readonly {readonly likely_titles: readonly string[]}[];
  readonly purchase_triggers: readonly {readonly signal: string; readonly why_now: string}[];
  readonly qualification_signals: readonly {readonly signal: string; readonly fit_or_intent: string}[];
  readonly entry_offer: {readonly name: string; readonly expected_outcome: string; readonly delivery_window: string};
  readonly fit_score: {readonly score: number; readonly rationale: string};
}

@Component({
  imports: [RouterLink, TuiButton, TuiBadge, TuiChip, ChipListComponent, DetailDisclosureComponent, ExpandableCardComponent, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent, SummaryCardComponent, SummaryGridComponent],
  templateUrl: './icps-page.html',
  styleUrl: './icps-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class IcpsPage implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly icps = signal<readonly ICP[]>([]);
  protected readonly campaigns = signal<readonly CampaignReference[]>([]);
  protected readonly campaignMap = signal<ReadonlyMap<string, CampaignReference>>(new Map());
  protected readonly loading = signal(true);
  protected readonly error = signal(false);
  protected readonly researchDialogOpen = signal(false);
  protected readonly selectedCampaignId = signal('');
  protected readonly researchSubmitting = signal(false);
  protected readonly researchError = signal('');
  protected readonly researchSuccess = signal('');

  protected get campaignCount(): number {
    return new Set(this.icps().map((icp) => icp.campaign_id)).size;
  }

  protected get averageFitScore(): string {
    const icps = this.icps();
    if (!icps.length) return '—';
    return (icps.reduce((total, icp) => total + icp.fit_score.score, 0) / icps.length).toFixed(1);
  }

  protected campaignTitle(campaignId: string): string {
    return this.campaignMap().get(campaignId)?.title ?? campaignId;
  }

  protected openResearchDialog(): void {
    this.researchError.set('');
    this.researchDialogOpen.set(true);
  }

  protected closeResearchDialog(): void {
    if (!this.researchSubmitting()) {
      this.researchDialogOpen.set(false);
      this.researchError.set('');
    }
  }

  protected updateCampaign(event: Event): void {
    this.selectedCampaignId.set((event.target as HTMLSelectElement).value);
    this.researchError.set('');
  }

  protected queueResearchJob(): void {
    const campaignId = this.selectedCampaignId();
    if (!this.campaignMap().has(campaignId)) {
      this.researchError.set('Select a loaded campaign before submitting.');
      return;
    }
    this.researchSubmitting.set(true);
    this.researchError.set('');
    this.api.enqueueIcpJob(campaignId).subscribe({
      next: () => {
        this.researchSubmitting.set(false);
        this.researchDialogOpen.set(false);
        this.selectedCampaignId.set('');
        this.researchSuccess.set('1 ICP research job queued.');
      },
      error: () => {
        this.researchSubmitting.set(false);
        this.researchError.set('ICP research job could not be queued.');
      },
    });
  }

  ngOnInit(): void {
    (this.api.listCampaigns() as Observable<readonly CampaignReference[]>).pipe(
      map((campaigns) => ({
        campaigns,
        campaignMap: new Map<string, CampaignReference>(
          campaigns.map((campaign: CampaignReference) => [campaign.campaign_id, campaign]),
        ),
      })),
    ).subscribe({
      next: ({campaigns, campaignMap}) => {
        this.campaigns.set(campaigns);
        this.campaignMap.set(campaignMap);
        if (!campaigns.length) {
          this.loading.set(false);
          return;
        }

        forkJoin(campaigns.map((campaign: CampaignReference) =>
          this.api.listCampaignIcps(campaign.campaign_id) as Observable<readonly ICP[]>,
        )).subscribe({
          next: (icpGroups) => {
            this.icps.set(icpGroups.flat());
            this.loading.set(false);
          },
          error: () => this.setError(),
        });
      },
      error: () => this.setError(),
    });
  }

  private setError(): void {
    this.error.set(true);
    this.loading.set(false);
  }
}
