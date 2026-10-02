import {inject} from '@angular/core';
import {CanActivateFn, Router, Routes} from '@angular/router';

import {ApiService} from './api-service';
import {DashboardPage} from './dashboard-page';
import {JobDetailPage} from './job-detail-page';
import {LoginPage} from './login-page';
const requireAuth: CanActivateFn = (_route, state) => {
  if (inject(ApiService).authenticated()) return true;
  return inject(Router).createUrlTree(['/login'], {queryParams: {returnUrl: state.url}});
};

export const routes: Routes = [
  {path: 'login', component: LoginPage},
  {path: '', pathMatch: 'full', redirectTo: 'dashboard'},
  {path: 'dashboard', component: DashboardPage, canActivate: [requireAuth]},
  {path: 'jobs', loadComponent: () => import('./jobs-page').then((module) => module.JobsPage), canActivate: [requireAuth]},
  {path: 'jobs/:jobId', component: JobDetailPage, canActivate: [requireAuth]},
  {path: 'memory', loadComponent: () => import('./memory-page').then((module) => module.MemoryPage), canActivate: [requireAuth]},
  {path: 'memory/:memoryId', loadComponent: () => import('./memory-detail-page').then((module) => module.MemoryDetailPage), canActivate: [requireAuth]},
];
