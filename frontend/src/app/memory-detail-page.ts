import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {JsonPipe} from '@angular/common';
import {interval} from 'rxjs';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal} from '@angular/core';
import {ActivatedRoute, RouterLink} from '@angular/router';

import {ApiService, MemoryDetail} from './api-service';
import {AutoRefreshIndicatorComponent, AUTO_REFRESH_INTERVAL_MS} from './components/auto-refresh-indicator.component';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';
@Component({
  selector: 'app-memory-detail-page',
  imports: [JsonPipe, RouterLink, TuiBadge, TuiButton, AutoRefreshIndicatorComponent, PageHeaderComponent, PageLayoutComponent, StateMessageComponent],
  templateUrl: './memory-detail-page.html',
  styleUrl: './memory-detail-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MemoryDetailPage implements OnInit {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);
  private memoryId = '';
  protected readonly memory = signal<MemoryDetail | null>(null);
  protected readonly loading = signal(true);
  protected readonly refreshing = signal(false);
  protected readonly error = signal(false);
  private inFlight = false;
  private generation = 0;

  ngOnInit(): void {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      const memoryId = params.get('memoryId');
      if (!memoryId) return;
      this.memoryId = memoryId;
      this.memory.set(null);
      ++this.generation;
      this.inFlight = false;
      this.loading.set(true);
      this.refreshing.set(false);
      this.error.set(false);
      this.load(false);
    });
    interval(AUTO_REFRESH_INTERVAL_MS).pipe(takeUntilDestroyed(this.destroyRef)).subscribe(() => this.load(true));
  }
  protected timestamp(value: number): string { return new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short'}).format(value * 1000); }
  protected retry(): void { this.load(false); }

  private load(isRefresh: boolean): void {
    if (!this.memoryId || this.inFlight) return;
    this.inFlight = true;
    const generation = this.generation;
    if (isRefresh) this.refreshing.set(true);
    else this.loading.set(true);
    this.error.set(false);
    this.api.getMemory(this.memoryId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (memory) => {
        if (generation !== this.generation) return;
        this.memory.set(memory);
        this.inFlight = false; this.loading.set(false); this.refreshing.set(false);
      },
      error: () => {
        if (generation !== this.generation) return;
        this.inFlight = false; this.loading.set(false); this.refreshing.set(false); this.error.set(true);
      },
    });
  }
}
