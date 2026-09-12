import {Component} from '@angular/core';
import {RouterOutlet} from '@angular/router';
import {TuiRoot} from '@taiga-ui/core';

import {SidebarNavigationComponent} from './components/sidebar-navigation.component';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, SidebarNavigationComponent, TuiRoot],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {}
