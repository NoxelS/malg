import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {HttpClient} from '@angular/common/http';
import {RouterLink} from '@angular/router';
import {forkJoin, map} from 'rxjs';
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
  selector: 'app-icps-page',
  imports: [RouterLink, TuiBadge, TuiChip, ChipListComponent, DetailDisclosureComponent, ExpandableCardComponent, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent, SummaryCardComponent, SummaryGridComponent],
  templateUrl: './icps-page.html',
  styleUrl: './icps-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class IcpsPage implements OnInit {
  private readonly http = inject(HttpClient);

  protected readonly icps = signal<readonly ICP[]>([]);
  protected readonly campaigns = signal<ReadonlyMap<string, CampaignReference>>(new Map());
  protected readonly loading = signal(true);
  protected readonly error = signal(false);

  protected get campaignCount(): number {
    return new Set(this.icps().map((icp) => icp.campaign_id)).size;
  }

  protected get averageFitScore(): string {
    const icps = this.icps();
    if (!icps.length) return '—';
    return (icps.reduce((total, icp) => total + icp.fit_score.score, 0) / icps.length).toFixed(1);
  }

  protected campaignTitle(campaignId: string): string {
    return this.campaigns().get(campaignId)?.title ?? campaignId;
  }

  ngOnInit(): void {
    this.http.get<readonly CampaignReference[]>('/api/v1/campaigns').pipe(
      map((campaigns) => ({
        campaigns,
        campaignMap: new Map(campaigns.map((campaign) => [campaign.campaign_id, campaign])),
      })),
    ).subscribe({
      next: ({campaigns, campaignMap}) => {
        this.campaigns.set(campaignMap);
        if (!campaigns.length) {
          this.loading.set(false);
          return;
        }

        forkJoin(campaigns.map((campaign) =>
          this.http.get<readonly ICP[]>(`/api/v1/campaigns/${campaign.campaign_id}/icps`),
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
