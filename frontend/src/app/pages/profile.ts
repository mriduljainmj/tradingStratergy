import { Component, inject, signal, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Api, Auth, message } from '../core/api';
import { Feedback } from '../core/feedback';
import { Heading, ErrorBox } from '../shared/ui';
import { Icon } from '../shared/icon';
@Component({
  selector: 'ax-profile',
  imports: [FormsModule, Heading, ErrorBox, Icon, RouterLink],
  template: `<ax-heading
      title="Make yourself at home."
      subtitle="Your profile, preferences and connection to the market."
      eyebrow="ACCOUNT & CONNECTION"
    /><ax-error [text]="error()" />
    @if (route.snapshot.queryParamMap.get('kite_ok') === '1' && auth.user()?.has_kite_token) {
      <div class="connection-banner" role="status">
        <ax-icon name="check" />
        <div>
          <strong>Kite connected successfully.</strong>
          <p>
            Your broker session is ready. Open Overview to view market data and manage trading
            separately.
          </p>
        </div>
        <a class="btn" routerLink="/overview">Open overview →</a>
      </div>
    }
    <div class="profile-layout">
      <section class="panel profile-card">
        <div class="profile-avatar">
          @if (auth.user()?.photo_base64) {
            <img [src]="auth.user()?.photo_base64" alt="Your profile photo" />
          } @else {
            <span>{{ auth.user()?.username?.slice(0, 1)?.toUpperCase() }}</span>
          }
        </div>
        <h2>{{ auth.user()?.display_name || auth.user()?.username }}</h2>
        <p>{{ auth.user()?.email }}</p>
        <span class="pill">{{ auth.user()?.is_admin ? 'Administrator' : 'Member' }}</span
        ><label class="btn upload full"
          >Change photo<input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            (change)="photo($event)"
        /></label>
        <div class="profile-card-note">
          <ax-icon name="shield" />
          <p>Your broker credentials are encrypted on the server.</p>
        </div>
      </section>
      <div class="profile-forms">
        <form class="panel" (ngSubmit)="saveProfile()">
          <div class="panel-heading">
            <h2>Personal details</h2>
            <ax-icon name="profile" />
          </div>
          <div class="form-grid">
            <label
              >Display name<input name="display" [(ngModel)]="displayName" maxlength="150" /></label
            ><label
              >Broker client ID<input name="broker" [(ngModel)]="brokerId" maxlength="100" /></label
            ><label class="span-two"
              >About you<textarea
                name="bio"
                [(ngModel)]="bio"
                rows="3"
                maxlength="500"
                placeholder="A little about your trading approach…"
              ></textarea>
            </label>
          </div>
          <button class="btn primary" [disabled]="busy()">Save changes</button>
        </form>
        <section class="panel">
          <div class="panel-heading">
            <h2>Broker connection</h2>
            <span class="pill" [class.success]="auth.user()?.has_kite_token">{{
              auth.user()?.has_kite_token ? 'CONNECTED' : 'NOT CONNECTED'
            }}</span>
          </div>
          <div class="broker-intro">
            <span class="kite-mark">K</span>
            <div>
              <h3>Zerodha Kite</h3>
              <p>Connect for market data, historical sessions and order execution.</p>
            </div>
          </div>
          <form (ngSubmit)="credentials()">
            <div class="form-grid">
              <label
                >API key<input name="key" [(ngModel)]="apiKey" required autocomplete="off" /></label
              ><label
                >API secret<input
                  type="password"
                  name="secret"
                  [(ngModel)]="apiSecret"
                  required
                  autocomplete="off"
                  placeholder="Enter to update credentials"
              /></label>
            </div>
            <button class="btn" [disabled]="busy()">Save credentials</button>
          </form>
          <div class="button-row broker-buttons">
            <button class="btn primary" [disabled]="busy()" (click)="connect()">
              Connect with Kite<ax-icon name="arrow" /></button
            ><button
              class="btn danger-ghost"
              [disabled]="busy() || !auth.user()?.has_kite_token"
              (click)="disconnect()"
            >
              Disconnect
            </button>
          </div>
          <p class="muted text-small">
            The Kite callback URL is <code>{{ callback }}</code
            >. Configure it in your Kite application. Broker sessions must be renewed when they
            expire.
          </p>
          <details>
            <summary>Use an access token instead</summary>
            <form (ngSubmit)="saveToken()">
              <label
                >Access token<input
                  type="password"
                  name="access"
                  [(ngModel)]="accessToken"
                  required
                  autocomplete="off" /></label
              ><button class="btn" [disabled]="busy()">Connect token</button>
            </form>
          </details>
        </section>
        @if (incidents().length) {
          <section class="panel">
            <div class="panel-heading">
              <h2>Orders needing review</h2>
              <span class="pill">EXECUTION PAUSED</span>
            </div>
            <p>
              Check these orders in Kite. Close positions and cancel outstanding orders before
              reconciling. Import confirmed fills into Performance afterward.
            </p>
            @for (order of incidents(); track order.id) {
              <p>
                <strong>{{ order.side }} {{ order.quantity }} {{ order.symbol }}</strong> ·
                {{ order.order_id || 'Order ID not received' }}
              </p>
            }
            @if (auth.user()?.is_admin) {
              <button class="btn" [disabled]="busy()" (click)="reconcile()">
                Verify broker and reconcile
              </button>
            }
          </section>
        }
        <form class="panel" (ngSubmit)="password()">
          <div class="panel-heading">
            <h2>Change password</h2>
            <ax-icon name="shield" />
          </div>
          <div class="form-grid">
            <label
              >Current password<input
                name="old"
                type="password"
                [(ngModel)]="oldPassword"
                required
                autocomplete="current-password" /></label
            ><label
              >New password<input
                name="new"
                type="password"
                [(ngModel)]="newPassword"
                required
                minlength="8"
                autocomplete="new-password"
            /></label>
          </div>
          <button class="btn" [disabled]="busy()">Update password</button>
        </form>
        <section class="panel danger-zone">
          <h2>Delete account</h2>
          <p>Delete your profile, strategies, watchlists and stored trade history.</p>
          <button class="btn danger-ghost" [disabled]="busy()" (click)="deleteAccount()">
            Delete my account
          </button>
        </section>
      </div>
    </div>`,
})
export class Profile implements OnInit {
  api = inject(Api);
  auth = inject(Auth);
  feedback = inject(Feedback);
  route = inject(ActivatedRoute);
  error = signal('');
  busy = signal(false);
  displayName = '';
  bio = '';
  brokerId = '';
  apiKey = '';
  apiSecret = '';
  accessToken = '';
  oldPassword = '';
  newPassword = '';
  incidents = signal<any[]>([]);
  callback = location.origin + '/kite/callback';
  ngOnInit() {
    void this.loadIncidents();
    const u = this.auth.user();
    this.displayName = u?.display_name || '';
    this.bio = u?.bio || '';
    this.brokerId = u?.broker_id || '';
    this.apiKey = u?.kite_api_key_stored || '';
    const error = this.route.snapshot.queryParamMap.get('kite_error');
    if (error) this.error.set(error);
  }
  async loadIncidents() {
    try {
      this.incidents.set((await this.api.get('/execution/incidents')).data);
    } catch (e) {
      this.error.set(message(e));
    }
  }
  async reconcile() {
    if (
      await this.feedback.confirm(
        'Reconcile uncertain orders?',
        'The server will verify that Kite has no open positions or pending orders, then stop engines and return to paper mode.',
        'Verify and reconcile',
      )
    )
      await this.act(async () => {
        await this.api.post('/execution/reconcile');
        await this.loadIncidents();
      }, 'Reconciled. Review confirmed fills before enabling trading.');
  }
  async act(fn: () => Promise<unknown>, success: string) {
    this.busy.set(true);
    this.error.set('');
    try {
      await fn();
      await this.auth.restore();
      this.feedback.notify(success);
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
  saveProfile() {
    void this.act(
      () =>
        this.api.post('/auth/profile', {
          display_name: this.displayName,
          bio: this.bio,
          broker_id: this.brokerId,
        }),
      'Profile saved.',
    );
  }
  credentials() {
    void this.act(async () => {
      await this.api.post('/auth/kite-credentials', {
        api_key: this.apiKey,
        api_secret: this.apiSecret,
      });
      this.apiSecret = '';
    }, 'Broker credentials saved.');
  }
  async connect() {
    await this.act(async () => {
      const d = await this.api.get('/auth/kite-login-url');
      location.assign(d.url || d.login_url);
    }, 'Opening Kite…');
  }
  saveToken() {
    void this.act(async () => {
      await this.api.post('/auth/kite-token', { access_token: this.accessToken });
      this.accessToken = '';
    }, 'Kite token connected.');
  }
  async disconnect() {
    if (
      await this.feedback.confirm(
        'Disconnect Kite?',
        'Close open positions first. Trading engines must be stopped before disconnecting.',
        'Disconnect',
        true,
      )
    )
      await this.act(() => this.api.delete('/auth/kite-token'), 'Broker disconnected.');
  }
  password() {
    void this.act(async () => {
      await this.api.post('/auth/profile', {
        old_password: this.oldPassword,
        new_password: this.newPassword,
      });
      this.oldPassword = '';
      this.newPassword = '';
    }, 'Password updated.');
  }
  async photo(event: Event) {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    if (file.size > 500000) {
      this.error.set('Choose an image under 500 KB.');
      return;
    }
    const reader = new FileReader();
    reader.onload = () =>
      void this.act(
        () => this.api.post('/auth/profile', { photo_base64: reader.result }),
        'Photo updated.',
      );
    reader.readAsDataURL(file);
  }
  async deleteAccount() {
    if (
      await this.feedback.confirm(
        'Permanently delete your account?',
        'This cannot be undone. All stored account data will be removed.',
        'Delete account',
        true,
      )
    )
      await this.act(async () => {
        await this.api.delete('/auth/account');
        this.auth.clear();
        await this.auth.router.navigateByUrl('/login');
      }, 'Account deleted.');
  }
}
