import {ChangeDetectionStrategy, Component} from '@angular/core';
import {RouterLink, RouterLinkActive} from '@angular/router';

type NavigationItem = {
  readonly path: string;
  readonly name: string;
  readonly description: string;
  readonly icon: string;
};

@Component({
  selector: 'app-sidebar-navigation',
  imports: [RouterLink, RouterLinkActive],
  template: `
    <aside class="sidebar" aria-label="Primary navigation">
      <a class="brand" routerLink="/dashboard" aria-label="MALG home">
        <span class="brand__mark" aria-hidden="true">M</span>
        <span class="brand__copy" aria-hidden="true">
          <span class="brand__title">MALG</span>
          <span class="brand__subtitle">Research workspace</span>
        </span>
      </a>

      <nav class="nav" aria-label="Workspace sections">
        <p class="nav__eyebrow">Workspace</p>
        @for (item of navigation; track item.path) {
          <a
            class="nav__link"
            [routerLink]="item.path"
            routerLinkActive="sidebar-nav__link--active"
            [routerLinkActiveOptions]="{exact: true}"
            [attr.aria-label]="item.name"
          >
            <span class="nav__icon" aria-hidden="true">{{ item.icon }}</span>
            <span class="nav__copy">
              <span class="nav__title">{{ item.name }}</span>
              <span class="nav__description">{{ item.description }}</span>
            </span>
            <span class="nav__arrow" aria-hidden="true">→</span>
          </a>
        }
      </nav>
    </aside>
  `,
  styles: [`
    :host {
      display: block;
      flex: 0 0 18rem;
    }

    .sidebar {
      background: var(--tui-background-neutral-1);
      border-inline-end: 1px solid var(--tui-border-normal);
      box-sizing: border-box;
      display: flex;
      flex-direction: column;
      gap: 2.5rem;
      min-height: 100dvh;
      padding: 1.5rem 1rem;
      position: sticky;
      top: 0;
    }

    .brand {
      align-items: center;
      color: var(--tui-text-primary);
      display: flex;
      gap: 0.75rem;
      padding: 0.5rem;
      text-decoration: none;
    }

    .brand__mark {
      align-items: center;
      background: var(--malg-pink);
      border-radius: 0.45rem;
      color: var(--tui-background-base);
      display: flex;
      flex: 0 0 2.25rem;
      font: var(--tui-font-heading-5);
      font-weight: 800;
      height: 2.25rem;
      justify-content: center;
    }

    .brand__copy {
      display: flex;
      flex-direction: column;
      gap: 0.15rem;
    }

    .brand__title {
      font: var(--tui-font-heading-6);
      font-weight: 800;
      letter-spacing: 0.08em;
    }

    .brand__subtitle {
      color: var(--tui-text-secondary);
      font: var(--tui-font-text-s);
    }

    .nav {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }

    .nav__eyebrow {
      color: var(--tui-text-secondary);
      font: var(--tui-font-text-s);
      font-weight: 700;
      letter-spacing: 0.08em;
      margin: 0 0 0.25rem 0.75rem;
      text-transform: uppercase;
    }

    .nav__link {
      align-items: center;
      background: transparent;
      border: 0;
      border-radius: 0.75rem;
      box-sizing: border-box;
      color: var(--tui-text-primary);
      display: grid;
      gap: 0.75rem;
      grid-template-columns: 1.5rem minmax(0, 1fr) auto;
      min-width: 0;
      padding: 0.75rem;
      text-decoration: none;
      transition: background-color 120ms ease, color 120ms ease;
    }

    .nav__link:hover {
      background: var(--malg-pink-pale-hover);
    }

    .nav__link:focus-visible {
      outline: 2px solid var(--tui-border-focus);
      outline-offset: 2px;
    }

    .sidebar-nav__link--active {
      background: var(--malg-pink-pale);
      border-inline-start: 0.25rem solid var(--malg-pink);
      color: var(--malg-pink);
      padding-inline-start: 0.5rem;
    }

    .nav__icon {
      font-size: 1.2rem;
      line-height: 1;
      text-align: center;
    }

    .nav__copy {
      display: flex;
      flex-direction: column;
      gap: 0.2rem;
      min-width: 0;
    }

    .nav__title {
      font: var(--tui-font-text-m);
      font-weight: 700;
    }

    .nav__description {
      color: var(--tui-text-secondary);
      font: var(--tui-font-text-s);
    }

    .sidebar-nav__link--active .nav__description {
      color: var(--malg-pink);
    }

    .nav__arrow {
      color: var(--malg-pink);
      font-size: 1.1rem;
      opacity: 0;
      transition: opacity 120ms ease;
    }

    .nav__link:hover .nav__arrow,
    .sidebar-nav__link--active .nav__arrow {
      opacity: 1;
    }

    @media (max-width: 42rem) {
      :host {
        flex-basis: 5.5rem;
      }

      .sidebar {
        gap: 2rem;
        padding: 1rem 0.75rem;
      }

      .brand {
        justify-content: center;
      }

      .brand__copy,
      .nav__eyebrow,
      .nav__copy,
      .nav__arrow {
        display: none;
      }

      .nav__link {
        display: flex;
        justify-content: center;
        padding: 0.85rem 0;
      }

      .sidebar-nav__link--active {
        padding-inline-start: 0;
      }

      .nav__icon {
        font-size: 1.4rem;
      }
    }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SidebarNavigationComponent {
  readonly navigation: readonly NavigationItem[] = [
    {path: '/dashboard', name: 'Dashboard', description: 'Live activity, jobs, and worker health.', icon: '◌'},
    {path: '/campaigns', name: 'Campaigns', description: 'Research boundaries and evidence.', icon: '◇'},
    {path: '/accounts', name: 'Accounts', description: 'Organization profiles and firmographics.', icon: '□'},
    {path: '/icps', name: 'ICP Overview', description: 'Ideal customer segments and buying context.', icon: '△'},
    {path: '/jobs', name: 'Jobs', description: 'Queued work and recent outcomes.', icon: '◍'},
    {path: '/memory', name: 'Memory', description: 'Durable agent findings and recall history.', icon: '◆'},
  ];
}
