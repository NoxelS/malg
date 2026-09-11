import { Routes } from '@angular/router';

import { CampaignsPage } from './campaigns-page';
import { IcpsPage } from './icps-page';

export const routes: Routes = [
  {path: '', pathMatch: 'full', redirectTo: 'campaigns'},
  {path: 'campaigns', component: CampaignsPage},
  {path: 'icps', component: IcpsPage},
];
