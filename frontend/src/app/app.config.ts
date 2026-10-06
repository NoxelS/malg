import { provideHttpClient } from '@angular/common/http';
import { ApplicationConfig, provideBrowserGlobalErrorListeners } from '@angular/core';
import { TitleStrategy, provideRouter } from '@angular/router';
import { provideTaiga } from '@taiga-ui/core';

import { routes } from './app.routes';
import { PageTitleStrategy } from './page-title-strategy';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideHttpClient(),
    provideRouter(routes),
    provideTaiga(),
    {provide: TitleStrategy, useClass: PageTitleStrategy},
  ],
};
