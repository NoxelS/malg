import {DatePipe, JsonPipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, OnInit, inject, signal} from '@angular/core';
import {HttpClient} from '@angular/common/http';
import {TuiBadge} from '@taiga-ui/kit';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';

interface Memory {
  readonly id: string;
  readonly type: string;
  readonly content: string;
  readonly owner: string;
  readonly importance: number;
  readonly salience: number;
  readonly strength: number;
  readonly created_at: string;
  readonly status?: string | null;
  readonly tags: readonly string[];
  readonly archived: boolean;
  readonly edges?: readonly unknown[];
  readonly references?: readonly unknown[];
}

interface MemoryPageResponse {
  readonly items: readonly Memory[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

@Component({
  selector: 'app-memory-page',
  imports: [DatePipe, JsonPipe, TuiBadge, PageHeaderComponent, PageLayoutComponent, StateMessageComponent],
  templateUrl: './memory-page.html',
  styleUrl: './memory-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MemoryPage implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly pageSize = 50;

  protected readonly memories = signal<readonly Memory[]>([]);
  protected readonly total = signal(0);
  protected readonly loading = signal(true);
  protected readonly loadingMore = signal(false);
  protected readonly error = signal(false);
  protected readonly includeArchived = signal(false);

  ngOnInit(): void {
    this.loadPage(true);
  }

  protected toggleArchived(event: Event): void {
    this.includeArchived.set((event.target as HTMLInputElement).checked);
    this.loadPage(true);
  }

  protected loadMore(): void {
    if (this.loadingMore() || this.memories().length >= this.total()) return;
    this.loadPage(false);
  }

  private loadPage(reset: boolean): void {
    if (reset) {
      this.loading.set(true);
      this.error.set(false);
    } else {
      this.loadingMore.set(true);
    }
    const offset = reset ? 0 : this.memories().length;
    this.http.get<MemoryPageResponse>('/api/v1/memories', {
      params: {limit: this.pageSize, offset, include_archived: this.includeArchived()},
    }).subscribe({
      next: (page) => {
        this.memories.update((items) => reset ? page.items : [...items, ...page.items]);
        this.total.set(page.total);
        this.loading.set(false);
        this.loadingMore.set(false);
      },
      error: () => {
        this.loading.set(false);
        this.loadingMore.set(false);
        this.error.set(true);
      },
    });
  }
}
