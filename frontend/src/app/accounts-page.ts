import {DatePipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {HttpClient} from '@angular/common/http';
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

@Component({
  selector: 'app-accounts-page',
  imports: [DatePipe, TuiBadge, TuiChip, ChipListComponent, DetailDisclosureComponent, ExpandableCardComponent, PageHeaderComponent, PageLayoutComponent, SectionHeadingComponent, StateMessageComponent, SummaryCardComponent, SummaryGridComponent],
  templateUrl: './accounts-page.html',
  styleUrl: './accounts-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountsPage implements OnInit {
  private readonly http = inject(HttpClient);

  protected readonly accounts = signal<readonly Account[]>([]);
  protected readonly loading = signal(true);
  protected readonly error = signal(false);

  protected get industryCount(): number {
    return new Set(this.accounts().flatMap((account) => account.firmographics.industries)).size;
  }

  protected get operatingRegionCount(): number {
    return new Set(this.accounts().flatMap((account) => account.firmographics.operating_regions)).size;
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
  }
}
