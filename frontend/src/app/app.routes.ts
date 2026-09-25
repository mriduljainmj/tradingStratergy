import { inject } from '@angular/core';
import { Routes, CanActivateFn } from '@angular/router';
import { Auth } from './core/api';
const signedIn: CanActivateFn = async () => {
  const auth = inject(Auth);
  if (auth.user() || (await auth.restore())) return true;
  return auth.router.parseUrl('/login');
};
export const routes: Routes = [
  { path: 'login', loadComponent: () => import('./pages/login').then((m) => m.Login) },
  {
    path: '',
    canActivate: [signedIn],
    loadComponent: () => import('./pages/shell').then((m) => m.Shell),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'overview' },
      { path: 'overview', loadComponent: () => import('./pages/overview').then((m) => m.Overview) },
      {
        path: 'portfolio',
        loadComponent: () => import('./pages/portfolio').then((m) => m.Portfolio),
      },
      { path: 'markets', loadComponent: () => import('./pages/markets').then((m) => m.Markets) },
      {
        path: 'strategies',
        loadComponent: () => import('./pages/strategies').then((m) => m.Strategies),
      },
      {
        path: 'backtests',
        loadComponent: () => import('./pages/backtests').then((m) => m.Backtests),
      },
      { path: 'results', loadComponent: () => import('./pages/results').then((m) => m.Results) },
      { path: 'charts', loadComponent: () => import('./pages/charts').then((m) => m.Charts) },
      { path: 'profile', loadComponent: () => import('./pages/profile').then((m) => m.Profile) },
      { path: 'settings', loadComponent: () => import('./pages/settings').then((m) => m.Settings) },
    ],
  },
  { path: '**', redirectTo: 'overview' },
];
