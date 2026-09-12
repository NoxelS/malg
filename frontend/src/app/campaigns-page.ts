import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {DatePipe} from '@angular/common';
import {ApiService} from './api-service';
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

interface Evidence {
  readonly evidence_id: string;
  readonly claim: string;
  readonly source_type: string;
  readonly evidence_kind: string;
  readonly source_title: string;
  readonly source_url: string;
  readonly dataset_code: string | null;
  readonly observation_date: string | null;
  readonly retrieved_at: string;
  readonly geography: readonly string[];
  readonly unit: string | null;
  readonly population: string | null;
  readonly value: number | string | null;
  readonly strength: string;
}

interface Campaign {
  readonly campaign_id: string;
  readonly title: string;
  readonly positioning: string;
  readonly industries: readonly string[];
  readonly geographies: readonly string[];
  readonly company_size_focus: readonly string[];
  readonly target_workflows: readonly string[];
  readonly problem_statement: string;
  readonly why_now: string;
  readonly buyer_role_hypotheses: readonly string[];
  readonly qualification_signals: readonly string[];
  readonly exclusions: readonly string[];
  readonly entry_offer_hypothesis: string;
  readonly evidence: readonly Evidence[];
  readonly confidence: number;
  readonly assumptions: readonly string[];
  readonly unknowns: readonly string[];
  readonly next_research_questions: readonly string[];
}

@Component({
  selector: 'app-campaigns-page',
  imports: [DatePipe, TuiButton, TuiBadge, TuiChip, ChipListComponent, DetailDisclosureComponent, ExpandableCardComponent, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent, SummaryCardComponent, SummaryGridComponent],
  templateUrl: './campaigns-page.html',
  styleUrl: './campaigns-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CampaignsPage implements OnInit {
  private readonly api = inject(ApiService);

  protected readonly campaigns = signal<readonly Campaign[]>([]);
  protected readonly loading = signal(true);

  protected readonly researchDialogOpen = signal(false);
  protected readonly researchAmount = signal(1);
  protected readonly researchSubmitting = signal(false);
  protected readonly researchError = signal('');
  protected readonly researchSuccess = signal('');

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

  protected updateResearchAmount(event: Event): void {
    const value = Number((event.target as HTMLInputElement).value);
    this.researchAmount.set(Number.isFinite(value) ? value : 0);
  }

  protected queueResearchJobs(): void {
    const amount = this.researchAmount();
    if (!Number.isInteger(amount) || amount < 1 || amount > 100) {
      this.researchError.set('Enter a whole number from 1 to 100.');
      return;
    }

    this.researchSubmitting.set(true);
    this.researchError.set('');
    this.api.enqueueCampaignJobs(amount).subscribe({
      next: () => {
        this.researchSubmitting.set(false);
        this.researchDialogOpen.set(false);
        this.researchAmount.set(1);
        this.researchSuccess.set(`${amount} campaign research job${amount === 1 ? '' : 's'} queued.`);
      },
      error: () => {
        this.researchSubmitting.set(false);
        this.researchError.set('Campaign research jobs could not be queued.');
      },
    });
  }
  protected readonly error = signal(false);
  protected get industryCount(): number {
    return new Set(this.campaigns().flatMap((campaign) => campaign.industries)).size;
  }

  protected get averageConfidence(): string {
    const campaigns = this.campaigns();

    if (!campaigns.length) {
      return '—';
    }

    return (
      campaigns.reduce((total, campaign) => total + campaign.confidence, 0) / campaigns.length
    ).toFixed(1);
  }

  ngOnInit(): void {
    this.api.listCampaigns().subscribe({
      next: (campaigns) => {
        this.campaigns.set(campaigns);
        this.loading.set(false);
      },
      error: () => {
        this.error.set(true);
        this.loading.set(false);
      },
    });
  }
}
