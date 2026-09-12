import {DatePipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {HttpClient} from '@angular/common/http';
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

interface AccountIdentity {
  readonly display_name: string;
  readonly legal_name: string | null;
  readonly aliases: readonly string[];
  readonly legal_form: string | null;
  readonly registry_jurisdiction: string | null;
  readonly registration_number: string | null;
  readonly lei: string | null;
  readonly vat_id: string | null;
  readonly operational_status: string;
  readonly registered_office: string | null;
  readonly headquarters: string | null;
  readonly official_website: string | null;
  readonly official_domains: readonly string[];
}

interface AccountFirmographics {
  readonly industries: readonly string[];
  readonly nace_codes: readonly string[];
  readonly employee_range: string | null;
  readonly turnover_range: string | null;
  readonly operating_regions: readonly string[];
  readonly ownership_or_group: string | null;
}

interface AccountOperatingProfile {
  readonly key_workflows: readonly string[];
  readonly current_tools: readonly string[];
  readonly deployment_posture: readonly string[];
  readonly security_requirements: readonly string[];
}

interface Account {
  readonly account_id: string;
  readonly identity: AccountIdentity;
  readonly firmographics: AccountFirmographics;
  readonly operating_profile: AccountOperatingProfile;
  readonly created_at: string;
  readonly updated_at: string;
}

interface CampaignReference {
  readonly campaign_id: string;
  readonly title: string;
}

interface ICPReference {
  readonly icp_id: string;
  readonly title: string;
}

@Component({
  selector: 'app-accounts-page',
  imports: [DatePipe, TuiButton, TuiBadge, TuiChip, ChipListComponent, DetailDisclosureComponent, ExpandableCardComponent, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent, SummaryCardComponent, SummaryGridComponent],
  templateUrl: './accounts-page.html',
  styleUrl: './accounts-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountsPage implements OnInit {
  private readonly http = inject(HttpClient);
  private icpRequest = 0;

  protected readonly accounts = signal<readonly Account[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal(false);
  protected readonly campaigns = signal<readonly CampaignReference[]>([]);
  protected readonly selectedCampaignId = signal('');
  protected readonly icps = signal<readonly ICPReference[]>([]);
  protected readonly selectedIcpId = signal('');
  protected readonly icpsLoading = signal(false);
  protected readonly icpError = signal('');
  protected readonly researchDialogOpen = signal(false);
  protected readonly researchSubmitting = signal(false);
  protected readonly researchError = signal('');
  protected readonly researchSuccess = signal('');

  protected get industryCount(): number {
    return new Set(this.accounts().flatMap((account) => account.firmographics.industries)).size;
  }

  protected get operatingRegionCount(): number {
    return new Set(this.accounts().flatMap((account) => account.firmographics.operating_regions)).size;
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
    const campaignId = (event.target as HTMLSelectElement).value;
    const requestId = ++this.icpRequest;
    this.selectedCampaignId.set(campaignId);
    this.selectedIcpId.set('');
    this.icps.set([]);
    this.icpError.set('');
    this.icpsLoading.set(Boolean(campaignId));
    if (!campaignId) return;
    this.http.get<readonly ICPReference[]>(`/api/v1/campaigns/${campaignId}/icps`).subscribe({
      next: (icps) => {
        if (requestId !== this.icpRequest || campaignId !== this.selectedCampaignId()) return;
        this.icps.set(icps);
        this.icpsLoading.set(false);
      },
      error: () => {
        if (requestId !== this.icpRequest || campaignId !== this.selectedCampaignId()) return;
        this.icpsLoading.set(false);
        this.icpError.set('ICPs could not be loaded for this campaign.');
      },
    });
  }

  protected updateIcp(event: Event): void {
    this.selectedIcpId.set((event.target as HTMLSelectElement).value);
    this.researchError.set('');
  }

  protected queueResearchJob(): void {
    const campaignId = this.selectedCampaignId();
    const icpId = this.selectedIcpId();
    if (!this.campaigns().some((campaign) => campaign.campaign_id === campaignId) ||
      !this.icps().some((icp) => icp.icp_id === icpId)) {
      this.researchError.set('Select a loaded campaign and ICP before submitting.');
      return;
    }
    this.researchSubmitting.set(true);
    this.researchError.set('');
    this.http.post('/api/v1/jobs', {kind: 'account', campaign_id: campaignId, icp_id: icpId}).subscribe({
      next: () => {
        this.researchSubmitting.set(false);
        this.researchDialogOpen.set(false);
        this.selectedCampaignId.set('');
        this.selectedIcpId.set('');
        this.icps.set([]);
        this.researchSuccess.set('1 account research job queued.');
      },
      error: () => {
        this.researchSubmitting.set(false);
        this.researchError.set('Account research job could not be queued.');
      },
    });
  }

  ngOnInit(): void {
    this.http.get<readonly Account[]>('/api/v1/accounts').subscribe({
      next: (accounts) => {
        this.accounts.set(accounts);
        this.loading.set(false);
      },
      error: () => {
        this.error.set(true);
        this.loading.set(false);
      },
    });
    this.http.get<readonly CampaignReference[]>('/api/v1/campaigns').subscribe({
      next: (campaigns) => this.campaigns.set(campaigns),
      error: () => this.campaigns.set([]),
    });
  }
}
