import { ApplicationConfig } from '@angular/core';
import { provideRouter, withEnabledBlockingInitialNavigation } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';

import { routes } from './app.routes';
import { authInterceptor } from './interceptors/auth.interceptor';

export const appConfig: ApplicationConfig = {
  providers: [
    // withEnabledBlockingInitialNavigation evita el flash de pantalla antes del guard
    provideRouter(routes, withEnabledBlockingInitialNavigation()),
    provideHttpClient(withInterceptors([authInterceptor])),
  ],
};
