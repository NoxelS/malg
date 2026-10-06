import {inject} from '@angular/core';
import {CanActivateFn, Router, Routes} from '@angular/router';

import {ApiService} from './api-service';
import {JobDetailPage} from './job-detail-page';
import {LoginPage} from './login-page';
const requireAuth: CanActivateFn = (_route, state) => {
  if (inject(ApiService).authenticated()) return true;
  return inject(Router).createUrlTree(['/login'], {queryParams: {returnUrl: state.url}});
};

export const routes: Routes = [
  {path: 'login', component: LoginPage, title: 'Login'},
  {path: '', pathMatch: 'full', redirectTo: 'dashboard'},
  {path: 'dashboard', title: 'Dashboard', loadComponent: () => import('./dashboard-page').then((module) => module.DashboardPage), canActivate: [requireAuth]},
  {path: 'jobs', title: 'Jobs', loadComponent: () => import('./jobs-page').then((module) => module.JobsPage), canActivate: [requireAuth]},
  {path: 'jobs/:jobId', title: 'Job details', component: JobDetailPage, canActivate: [requireAuth]},
  {path: 'memory', title: 'Memory', loadComponent: () => import('./memory-page').then((module) => module.MemoryPage), canActivate: [requireAuth]},
  {path: 'memory/:memoryId', title: 'Memory detail', loadComponent: () => import('./memory-detail-page').then((module) => module.MemoryDetailPage), canActivate: [requireAuth]},
];
