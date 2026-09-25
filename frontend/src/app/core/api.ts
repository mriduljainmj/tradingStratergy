import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Router } from '@angular/router';
import { firstValueFrom, catchError, throwError, timeout } from 'rxjs';
import { User } from './models';

@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);
  async get<T = any>(path: string): Promise<T> {
    return this.send<T>('GET', path);
  }
  async post<T = any>(path: string, body: unknown = {}): Promise<T> {
    return this.send<T>('POST', path, body);
  }
  async put<T = any>(path: string, body: unknown): Promise<T> {
    return this.send<T>('PUT', path, body);
  }
  async delete<T = any>(path: string): Promise<T> {
    return this.send<T>('DELETE', path);
  }
  private async send<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await firstValueFrom(
      this.http
        .request<T>(method, '/api' + path, { body })
        .pipe(timeout(path.includes('backtest') ? 300000 : 30000)),
    );
    const value = response as any;
    if (value?.ok === false || value?.error) throw new Error(value.error || 'Request failed.');
    return response;
  }
}
@Injectable({ providedIn: 'root' })
export class Auth {
  api = inject(Api);
  router = inject(Router);
  user = signal<User | null>(null);
  token = signal(sessionStorage.getItem('axiom_token') || localStorage.getItem('orb_token') || '');
  async restore() {
    if (!this.token()) return false;
    try {
      const d = await this.api.get('/auth/me');
      this.user.set(d.user);
      return true;
    } catch {
      return false;
    }
  }
  async login(payload: unknown, register = false) {
    const d = await this.api.post('/auth/' + (register ? 'register' : 'login'), payload);
    this.token.set(d.token);
    sessionStorage.setItem('axiom_token', d.token);
    localStorage.removeItem('orb_token');
    localStorage.removeItem('orb_user');
    this.user.set(d.user);
    await this.router.navigateByUrl('/overview');
  }
  clear() {
    this.user.set(null);
    this.token.set('');
    sessionStorage.removeItem('axiom_token');
    localStorage.removeItem('orb_token');
    localStorage.removeItem('orb_user');
  }
  async logout() {
    try {
      await this.api.post('/auth/logout');
    } catch {
      /* Local sign-out must still complete if the server is unreachable. */
    } finally {
      this.clear();
      await this.router.navigateByUrl('/login');
    }
  }
}
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(Auth);
  const router = inject(Router);
  if (auth.token() && req.url.startsWith('/api/'))
    req = req.clone({ setHeaders: { Authorization: 'Bearer ' + auth.token() } });
  return next(req).pipe(
    catchError((error: HttpErrorResponse) => {
      if (error.status === 401 && !req.url.endsWith('/login')) {
        auth.clear();
        void router.navigateByUrl('/login');
      }
      return throwError(
        () =>
          new Error(
            error.error?.error ||
              error.error?.msg ||
              (error.status === 0
                ? 'Cannot reach the server. Check your connection.'
                : 'Request failed. Please try again.'),
          ),
      );
    }),
  );
};
export function message(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}
export function query(values: Record<string, unknown>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([k, v]) => {
    if (v !== '' && v != null) params.set(k, String(v));
  });
  return '?' + params.toString();
}
