import { Routes } from '@angular/router';

import { CampaignsPage } from './campaigns-page';
import { AccountsPage } from './accounts-page';
import { IcpsPage } from './icps-page';

export const routes: Routes = [
  {path: '', pathMatch: 'full', redirectTo: 'campaigns'},
  {path: 'campaigns', component: CampaignsPage},
  {path: 'accounts', component: AccountsPage},
  {path: 'icps', component: IcpsPage},
];
