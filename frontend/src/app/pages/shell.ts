import { ThemePicker } from '../shared/theme-picker';
import { Component, inject, signal, OnInit, OnDestroy } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { DatePipe } from '@angular/common';
import { Auth } from '../core/api';
import { Desk } from '../core/desk';
import { Icon } from '../shared/icon';
@Component({
  selector: 'ax-shell',
  imports: [ThemePicker, RouterLink, RouterLinkActive, RouterOutlet, Icon, DatePipe],
  template: ` <div class="app-shell" [class.nav-open]="menu()">
    <aside class="sidebar">
      <a routerLink="/overview" class="brand" (click)="menu.set(false)"
        ><span class="brand-mark">a<span>↗</span></span
        >axiom<span class="brand-tag">TERMINAL</span></a
      >
      <div class="nav-caption">WORKSPACE</div>
      <nav aria-label="Main navigation">
        @for (item of nav; track item.path) {
          @if (item.path) {
            <a [routerLink]="'/' + item.path" routerLinkActive="selected" (click)="menu.set(false)"
              ><ax-icon [name]="item.icon" /><span>{{ item.label }}</span>
              @if (item.path === 'overview') {
                <span class="nav-dot"></span>
              }
            </a>
          }
        }
      </nav>
      <div class="sidebar-bottom">
        <div class="broker-card">
          <span class="connection-dot" [class.connected]="auth.user()?.has_kite_token"></span
          ><strong>Kite Connect</strong>
          <p>
            {{ auth.user()?.has_kite_token ? 'Account connected' : 'Bring your markets to life.' }}
          </p>
          <a routerLink="/profile" (click)="menu.set(false)"
            >{{ auth.user()?.has_kite_token ? 'Manage connection' : 'Connect your broker'
            }}<ax-icon name="arrow"
          /></a>
        </div>
        <a routerLink="/settings" routerLinkActive="selected" (click)="menu.set(false)"
          ><ax-icon name="settings" /><span>Settings</span></a
        ><button class="sidebar-user" routerLink="/profile" (click)="menu.set(false)">
          <span class="avatar">{{ (auth.user()?.username || 'U').slice(0, 1).toUpperCase() }}</span
          ><span
            ><strong>{{ auth.user()?.display_name || auth.user()?.username }}</strong
            ><small>{{ auth.user()?.is_admin ? 'Administrator' : 'Personal account' }}</small></span
          ><ax-icon name="arrow" />
        </button>
      </div>
    </aside>
    @if (menu()) {
      <button class="nav-backdrop" aria-label="Close navigation" (click)="menu.set(false)"></button>
    }
    <div class="main-shell">
      <header class="topbar">
        <div class="topbar-context">
          <button
            class="icon-btn mobile-menu"
            aria-label="Open navigation"
            (click)="menu.set(true)"
          >
            <ax-icon name="menu" /></button
          ><span class="breadcrumb">Workspace <span>/</span> Trading terminal</span>
        </div>
        <div class="topbar-actions">
          <ax-theme-picker />
          <span class="mode-pill" [class.live]="desk.state()?.app_mode === 'LIVE'"
            >{{ desk.state()?.app_mode || 'PAPER' }} MODE</span
          ><span class="topbar-date">{{ today | date: 'EEE, dd MMM' : '+0530' }}</span
          ><button class="icon-btn" title="Sign out" aria-label="Sign out" (click)="auth.logout()">
            <ax-icon name="logout" />
          </button>
        </div>
      </header>
      <main class="page-main" id="main-content"><router-outlet /></main>
      <footer class="statusbar">
        <span
          ><i class="connection-dot" [class.connected]="!desk.error() && !!desk.updated()"></i
          >{{
            desk.error()
              ? 'Server unavailable'
              : desk.updated()
                ? 'Server connected'
                : 'Connecting…'
          }}</span
        ><span>{{
          desk.state()?.kite_auth_error
            ? 'Kite connection required'
            : desk.state()?.engine_running
              ? 'Engine running'
              : 'Engine idle'
        }}</span
        ><span class="status-time">{{ desk.updated() | date: 'HH:mm:ss' : '+0530' }} IST</span>
      </footer>
    </div>
  </div>`,
})
export class Shell implements OnInit, OnDestroy {
  auth = inject(Auth);
  desk = inject(Desk);
  menu = signal(false);
  today = new Date();
  nav = [
    { path: 'overview', icon: 'overview', label: 'Overview' },
    { path: 'portfolio', icon: 'results', label: 'Kite portfolio' },
    { path: 'markets', icon: 'markets', label: 'Market explorer' },
    { path: 'strategies', icon: 'strategies', label: 'Strategies' },
    { path: 'backtests', icon: 'backtests', label: 'Backtest lab' },
    { path: 'results', icon: 'results', label: 'Performance' },
    { path: 'charts', icon: 'charts', label: 'Multi-chart' },
  ];
  ngOnInit() {
    this.desk.start();
  }
  ngOnDestroy() {
    this.desk.stop();
  }
}
