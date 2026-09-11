import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {DatePipe} from '@angular/common';
import {HttpClient} from '@angular/common/http';
import {TuiBadge, TuiChip} from '@taiga-ui/kit';

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
  imports: [DatePipe, TuiBadge, TuiChip],
  templateUrl: './campaigns-page.html',
  styleUrl: './campaigns-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CampaignsPage implements OnInit {
  private readonly http = inject(HttpClient);

  protected readonly campaigns = signal<readonly Campaign[]>([]);
  protected readonly loading = signal(true);
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
    this.http.get<readonly Campaign[]>('/api/v1/campaigns').subscribe({
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
