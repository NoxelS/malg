import {JsonPipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, DestroyRef, OnInit, inject, signal} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {ActivatedRoute, RouterLink} from '@angular/router';

import {ApiService, MemoryDetail} from './api-service';
import {PageHeaderComponent} from './components/page-header.component';
import {PageLayoutComponent} from './components/page-layout.component';
import {StateMessageComponent} from './components/state-message.component';
import {TuiButton} from '@taiga-ui/core';
import {TuiBadge} from '@taiga-ui/kit';

@Component({
  selector: 'app-memory-detail-page',
  imports: [JsonPipe, RouterLink, TuiBadge, TuiButton, PageHeaderComponent, PageLayoutComponent, StateMessageComponent],
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
  protected readonly error = signal(false);

  ngOnInit(): void {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      const memoryId = params.get('memoryId');
      if (!memoryId) return;
      this.memoryId = memoryId;
      this.load();
    });
  }

  protected refresh(): void { this.load(); }
  protected timestamp(value: number): string { return new Intl.DateTimeFormat(undefined, {dateStyle: 'medium', timeStyle: 'short'}).format(value * 1000); }

  private load(): void {
    this.loading.set(true); this.error.set(false);
    this.api.getMemory(this.memoryId).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (memory) => { this.memory.set(memory); this.loading.set(false); },
      error: () => { this.loading.set(false); this.error.set(true); },
    });
  }
}
