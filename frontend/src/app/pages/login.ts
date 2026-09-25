import { ThemePicker } from '../shared/theme-picker';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Auth, message } from '../core/api';
import { Icon } from '../shared/icon';
import { ErrorBox } from '../shared/ui';
@Component({
  selector: 'ax-login',
  imports: [ThemePicker, FormsModule, Icon, ErrorBox],
  template: ` <main class="auth-page">
    <section class="auth-story">
      <a class="brand"
        ><span class="brand-mark">a<span>↗</span></span
        >axiom<span class="brand-tag">TERMINAL</span></a
      >
      <div>
        <div class="eyebrow">CLARITY. CONTROL. CONVICTION.</div>
        <h1>A better view.<br />A sharper edge.</h1>
        <p>One workspace for your markets, strategies and every decision in between.</p>
        <div class="auth-visual">
          <div class="visual-label">
            <span>NIFTY · STRATEGY WORKSPACE</span><span class="pill">BUILT FOR FOCUS</span>
          </div>
          <svg viewBox="0 0 600 180" aria-hidden="true">
            <path d="M0 150H600M0 100H600M0 50H600" stroke="#26384b" />
            <path
              d="M0 145L35 140 65 150 95 110 120 128 155 80 185 105 220 92 250 112 285 65 320 83 355 45 385 62 420 36 450 55 490 20 535 38 565 18 600 5"
              fill="none"
              stroke="#5bdfb6"
              stroke-width="3"
            /></svg
          ><span class="muted">An illustration of the workspace. Not market data.</span>
        </div>
      </div>
      <small>Designed around the way you trade.</small>
    </section>
    <section class="auth-form-wrap">
      <div class="auth-theme"><ax-theme-picker /></div>
      <form class="auth-form" (ngSubmit)="submit()">
        <div class="eyebrow">WELCOME TO AXIOM</div>
        <h2>{{ register() ? 'Make room for your next move.' : 'Good to see you again.' }}</h2>
        <p class="muted">
          {{
            register()
              ? 'Create your personal trading workspace.'
              : 'Sign in to your trading workspace.'
          }}
        </p>
        <div class="segmented">
          <button type="button" [class.active]="!register()" (click)="switch(false)">Sign in</button
          ><button type="button" [class.active]="register()" (click)="switch(true)">
            Create account
          </button>
        </div>
        <ax-error [text]="error()" />
        @if (register()) {
          <label
            >Username<input
              name="username"
              [(ngModel)]="username"
              required
              minlength="3"
              maxlength="100"
              autocomplete="username"
              placeholder="Your trading name"
          /></label>
        }
        <label
          >Email address<input
            name="email"
            type="email"
            [(ngModel)]="email"
            required
            autocomplete="email"
            placeholder="you@example.com" /></label
        ><label
          >Password<input
            name="password"
            type="password"
            [(ngModel)]="password"
            required
            [minlength]="register() ? 8 : 1"
            [attr.autocomplete]="register() ? 'new-password' : 'current-password'"
            placeholder="Enter your password"
        /></label>
        @if (register()) {
          <label
            >Confirm password<input
              name="confirmation"
              type="password"
              [(ngModel)]="confirmation"
              required
              autocomplete="new-password"
              placeholder="Re-enter your password"
          /></label>
        }
        <button class="btn primary full" [disabled]="busy()">
          {{ busy() ? 'Please wait…' : register() ? 'Create workspace' : 'Sign in'
          }}<ax-icon name="arrow" />
        </button>
        <div class="auth-note">
          <ax-icon name="shield" /><span
            >Your account and broker connection are managed separately. Connect Kite after signing
            in.</span
          >
        </div>
      </form>
    </section>
  </main>`,
})
export class Login {
  auth = inject(Auth);
  register = signal(false);
  busy = signal(false);
  error = signal('');
  email = '';
  password = '';
  username = '';
  confirmation = '';
  switch(v: boolean) {
    this.register.set(v);
    this.error.set('');
  }
  async submit() {
    if (this.busy()) return;
    this.error.set('');
    if (this.register() && this.password !== this.confirmation) {
      this.error.set('Passwords do not match.');
      return;
    }
    this.busy.set(true);
    try {
      await this.auth.login(
        { email: this.email, username: this.username, password: this.password },
        this.register(),
      );
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
}
