import {inject} from '@angular/core';
import {CanActivateFn, Router, Routes} from '@angular/router';

import {ApiService} from './api-service';
import {AccountsPage} from './accounts-page';
import {CampaignsPage} from './campaigns-page';
import {DashboardPage} from './dashboard-page';
import {IcpsPage} from './icps-page';
import {MemoryPage} from './memory-page';
import {JobsPage} from './jobs-page';
import {LoginPage} from './login-page';

const requireAuth: CanActivateFn = (_route, state) => {
  if (inject(ApiService).authenticated()) return true;
  return inject(Router).createUrlTree(['/login'], {queryParams: {returnUrl: state.url}});
};

export const routes: Routes = [
  {path: 'login', component: LoginPage},
  {path: '', pathMatch: 'full', redirectTo: 'dashboard'},
  {path: 'dashboard', component: DashboardPage, canActivate: [requireAuth]},
  {path: 'jobs', component: JobsPage, canActivate: [requireAuth]},
  {path: 'campaigns', component: CampaignsPage, canActivate: [requireAuth]},
  {path: 'accounts', component: AccountsPage, canActivate: [requireAuth]},
  {path: 'memory', component: MemoryPage, canActivate: [requireAuth]},
  {path: 'icps', component: IcpsPage, canActivate: [requireAuth]},
];
