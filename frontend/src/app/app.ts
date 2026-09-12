import {Component, inject} from '@angular/core';
import {Router, RouterOutlet} from '@angular/router';
import {TuiRoot} from '@taiga-ui/core';

import {SidebarNavigationComponent} from './components/sidebar-navigation.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, SidebarNavigationComponent, TuiRoot],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  private readonly router = inject(Router);

  protected get showShell(): boolean {
    return !this.router.url.startsWith('/login');
  }
}
