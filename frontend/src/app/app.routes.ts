import { Routes } from '@angular/router';

import { AccountsPage } from './accounts-page';
import { CampaignsPage } from './campaigns-page';
import { DashboardPage } from './dashboard-page';
import { IcpsPage } from './icps-page';
import { MemoryPage } from './memory-page';
import { JobsPage } from './jobs-page';

export const routes: Routes = [
  {path: '', pathMatch: 'full', redirectTo: 'dashboard'},
  {path: 'dashboard', component: DashboardPage},
  {path: 'jobs', component: JobsPage},
  {path: 'campaigns', component: CampaignsPage},
  {path: 'accounts', component: AccountsPage},
  {path: 'memory', component: MemoryPage},
  {path: 'icps', component: IcpsPage},
];
