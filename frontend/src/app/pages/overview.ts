import { Component, inject, signal, OnInit, OnDestroy, effect, untracked } from '@angular/core';
import { formatChartTime } from '../shared/chart-time';
import { RouterLink, Router } from '@angular/router';
import { DecimalPipe, CurrencyPipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Api, Auth, message } from '../core/api';
import { Desk } from '../core/desk';
import { Feedback } from '../core/feedback';
import { Summary, Trade, Candle } from '../core/models';
import { Heading, Stat, ErrorBox } from '../shared/ui';
import { Icon } from '../shared/icon';
import { Chart } from '../shared/chart';
import { MarketFeed } from '../core/market-feed';
import { liveCandles } from '../shared/live-candles';
@Component({
  selector: 'ax-overview',
  providers: [MarketFeed],
  host: { '(document:fullscreenchange)': 'syncChartFocus()' },
  styles: `
    .overview-chart-frame {
      background: var(--surface);
    }
    .overview-chart-frame:fullscreen {
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      overflow: auto;
    }
    .overview-chart-frame:fullscreen .main-chart,
    .overview-chart-frame:fullscreen .performance-chart {
      flex: 1;
      height: auto;
      min-height: 280px;
    }
    .overview-chart-actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      padding: 8px 0;
    }
    .overview-chart-actions strong {
      margin-right: auto;
    }
  `,
  imports: [
    RouterLink,
    DecimalPipe,
    CurrencyPipe,
    FormsModule,
    Heading,
    Stat,
    ErrorBox,
    Icon,
    Chart,
  ],
  template: ` <ax-heading
      title="Your trading day, at a glance."
      subtitle="A little perspective. A lot more control."
      eyebrow="THE BIG PICTURE"
      ><a class="btn" routerLink="/results"><ax-icon name="results" />View performance</a
      ><a class="btn primary" routerLink="/strategies"
        ><ax-icon name="plus" />New strategy</a
      ></ax-heading
    ><ax-error [text]="error() || desk.error()" />
    @if (desk.state()?.kite_auth_error) {
      <div class="connection-banner">
        <div class="banner-icon"><ax-icon name="bolt" /></div>
        <div>
          <strong>Your workspace is ready. Connect your market.</strong>
          <p>Link Zerodha Kite to unlock market data and your trading engines.</p>
        </div>
        <a class="btn" routerLink="/profile">Connect Kite<ax-icon name="arrow" /></a>
      </div>
    }
    @if (desk.state()?.execution_blocked) {
      <div class="connection-banner">
        <strong>Execution paused: an order needs review.</strong
        ><a class="btn" routerLink="/profile">Review broker orders</a>
      </div>
    }
    <div class="stats-grid">
      <ax-stat
        label="Available balance"
        icon="wallet"
        prefix="₹"
        [value]="desk.state()?.balance ?? null"
        [note]="
          desk.state()?.app_mode === 'LIVE' ? 'Broker available funds' : 'Simulated paper balance'
        "
      /><ax-stat
        label="Realized P&L"
        prefix="₹"
        [value]="summary()?.total_net_pnl ?? null"
        [tone]="(summary()?.total_net_pnl || 0) >= 0 ? 'positive' : 'negative'"
        note="All recorded trades"
      /><ax-stat
        label="Win rate"
        suffix="%"
        [value]="summary()?.win_rate ?? null"
        [note]="(summary()?.total_trades || 0) + ' completed trades'"
      /><ax-stat
        label="Open P&L"
        prefix="₹"
        [value]="desk.state()?.live_pnl ?? null"
        note="Current options position"
        [tone]="(desk.state()?.live_pnl || 0) >= 0 ? 'positive' : 'negative'"
      />
    </div>
    <div class="overview-grid">
      <section class="panel market-panel">
        <div class="panel-heading">
          <div>
            <div class="eyebrow">MARKET OVERVIEW</div>
            <h2>NIFTY 50 <span class="exchange">NSE</span></h2>
          </div>
          <div class="market-price">
            {{
              streamPrice(256265) ||
                (desk.state()?.engine_running ? desk.state()?.live_nifty_ltp : null) ||
                preview().at(-1)?.close ||
                null | currency: 'INR' : 'symbol' : '1.2-2'
            }}<small>{{
              streamPrice(256265)
                ? 'Latest streamed price'
                : desk.state()?.engine_running
                  ? 'Engine market feed'
                  : 'Latest recorded close'
            }}</small>
          </div>
        </div>
        <div class="chart-toolbar">
          <span class="pill subtle">5 MIN</span><span>Candlesticks</span
          ><a routerLink="/charts">Expand chart ↗</a>
        </div>
        <div class="overview-chart-frame" #niftyFrame>
          <div class="overview-chart-actions">
            <strong>NIFTY 50</strong>
            <button
              class="btn small"
              aria-label="Fit NIFTY chart data"
              [disabled]="previewBusy() || !preview().length"
              (click)="niftyChart.fit()"
            >
              Fit data
            </button>
            <button
              class="btn small"
              aria-label="Show latest NIFTY candles"
              [disabled]="previewBusy() || !preview().length"
              (click)="niftyChart.latest()"
            >
              Latest
            </button>
            <button
              class="btn small"
              aria-label="Focus NIFTY chart"
              (click)="focusChart(niftyFrame)"
            >
              {{ focusedChart() === niftyFrame ? 'Restore' : 'Focus' }}
            </button>
          </div>
          <div class="main-chart">
            <ax-chart
              #niftyChart
              [loading]="previewBusy()"
              [data]="niftyCandles()"
              [emptyTitle]="previewBusy() ? 'Loading market history…' : 'Your market, in focus'"
              [emptyText]="
                desk.state()?.kite_auth_error
                  ? 'Connect your Kite account to see market data here.'
                  : previewError() || 'No candles returned for the selected period.'
              "
            />
          </div>
        </div>
        @if (!desk.state()?.kite_auth_error) {
          <div class="button-row">
            <small class="muted"
              >5-minute market history · auto-refresh every 30 seconds · {{ feed.status() }}
              @if (preview().length) {
                · Latest candle: {{ formatTime(preview().at(-1)!.time) }}
              }
              @if (previewError()) {
                · Refresh failed; last successful data shown
              }</small
            ><button class="btn small" [disabled]="previewBusy()" (click)="loadMarketPreview()">
              Refresh market data
            </button>
          </div>
        }
        <ax-error [text]="previewError()" />
        <div class="market-levels">
          <span
            >OR high <strong>{{ desk.state()?.or_high || null | number: '1.2-2' }}</strong></span
          ><span
            >OR low <strong>{{ desk.state()?.or_low || null | number: '1.2-2' }}</strong></span
          ><span>Source <strong>Kite Connect</strong></span>
        </div>
        <a
          class="chart-attribution"
          href="https://www.tradingview.com/"
          target="_blank"
          rel="noopener"
          >Charts by TradingView Lightweight Charts</a
        >
      </section>
      <div class="overview-side">
        <section class="panel">
          <div class="panel-heading">
            <h2>Trading controls</h2>
            <span class="pill">{{ desk.state()?.engine_running ? 'Running' : 'Idle' }}</span>
          </div>
          <p class="panel-copy">{{ desk.state()?.status || 'Preparing your workspace…' }}</p>
          <label
            >Execution mode<select
              aria-label="Execution mode"
              [(ngModel)]="mode"
              [disabled]="busy()"
              (ngModelChange)="changeMode($event)"
            >
              <option value="PAPER">Paper · simulated orders</option>
              @if (auth.user()?.is_admin) {
                <option value="LIVE">Live · real orders</option>
              }
              <option value="BACKTEST">Backtest · historical data</option>
            </select></label
          >
          @if (mode === 'BACKTEST') {
            <a class="btn primary full" routerLink="/backtests">Open Backtest Lab</a>
          } @else {
            <button
              class="btn full"
              role="switch"
              aria-label="Trade execution popups"
              [attr.aria-checked]="auth.user()?.trade_confirm_modal !== false"
              [disabled]="popupSaving()"
              (click)="toggleExecutionPopups()"
            >
              Trade execution popups:
              {{ auth.user()?.trade_confirm_modal !== false ? 'On' : 'Off' }}
            </button>
            <small class="muted"
              >Show a confirmation after each ORB entry or exit. Applies to Paper and Live.</small
            >
            <button
              class="btn primary full"
              [disabled]="busy() || desk.state()?.kite_auth_error"
              (click)="start()"
            >
              <ax-icon name="bolt" />Enable {{ mode === 'LIVE' ? 'live' : 'paper' }} trading
            </button>
          }
          <button
            class="btn full"
            [disabled]="busy() || !desk.state()?.trades_enabled"
            (click)="pause()"
          >
            Pause new entries</button
          ><small class="muted">Pausing keeps open-position management active.</small>
          @if (auth.user()?.is_admin) {
            <button class="btn full" [disabled]="busy()" (click)="background()">
              {{
                desk.state()?.background_trading
                  ? 'Disable restart auto-resume'
                  : 'Enable paper restart auto-resume'
              }}
            </button>
          } @else {
            <p class="panel-copy">
              Paper and historical backtests are available. Live trading requires an administrator
              account.
            </p>
          }
        </section>
        <section class="panel">
          <div class="panel-heading">
            <h2>Current position</h2>
            <ax-icon name="shield" />
          </div>
          @if (desk.state()?.in_position) {
            <h3>{{ desk.state()?.option_label }}</h3>
            <div class="detail-row">
              <span>Entry</span><strong>{{ desk.state()?.entry_prem | currency: 'INR' }}</strong>
            </div>
            <div class="detail-row">
              <span>Quantity</span><strong>{{ desk.state()?.qty }}</strong>
            </div>
            @if (auth.user()?.is_admin) {
              <button class="btn danger full" [disabled]="busy()" (click)="manual('exit')">
                Exit position
              </button>
            }
          } @else {
            <div class="compact-empty">
              <ax-icon name="shield" /><strong>No open position</strong>
              <p>Your next position will appear here.</p>
            </div>
            @if (auth.user()?.is_admin) {
              <div class="button-row">
                <button
                  class="btn"
                  [disabled]="busy() || !desk.state()?.engine_running"
                  (click)="manual('enter', 'CALL')"
                >
                  Buy call</button
                ><button
                  class="btn"
                  [disabled]="busy() || !desk.state()?.engine_running"
                  (click)="manual('enter', 'PUT')"
                >
                  Buy put
                </button>
              </div>
            }
          }
        </section>
      </div>
    </div>
    <section class="panel">
      <div class="panel-heading">
        <div>
          <div class="eyebrow">OPTIONS WORKSPACE</div>
          <h2>{{ selectedOption || desk.state()?.option_label || 'Option premium & chain' }}</h2>
        </div>
        <button
          class="btn"
          [disabled]="chainBusy() || desk.state()?.kite_auth_error"
          (click)="loadChain()"
        >
          {{ chainBusy() ? 'Loading…' : 'Refresh option chain' }}
        </button>
      </div>
      <div class="toolbar">
        <div class="segmented" aria-label="Option chart source">
          <button
            [class.active]="!browseOptions"
            [attr.aria-pressed]="!browseOptions"
            (click)="followOption()"
          >
            Follow strategy
          </button>
          <button
            [class.active]="browseOptions"
            [attr.aria-pressed]="browseOptions"
            (click)="browseOptionContracts()"
          >
            Explore options
          </button>
        </div>
        @if (browseOptions && optionContracts().length) {
          <label
            >Expiry<select
              aria-label="Option expiry"
              [(ngModel)]="optionExpiry"
              (ngModelChange)="chooseOption()"
            >
              @for (date of optionExpiries(); track date) {
                <option [value]="date">{{ date }}</option>
              }
            </select></label
          >
          <div class="segmented" aria-label="Option type">
            <button
              [class.active]="optionType === 'CE'"
              [attr.aria-pressed]="optionType === 'CE'"
              (click)="optionType = 'CE'; chooseOption()"
            >
              Call (CE)
            </button>
            <button
              [class.active]="optionType === 'PE'"
              [attr.aria-pressed]="optionType === 'PE'"
              (click)="optionType = 'PE'; chooseOption()"
            >
              Put (PE)
            </button>
          </div>
          <label
            >Find strike<input
              aria-label="Find option strike"
              type="search"
              inputmode="numeric"
              placeholder="e.g. 25000"
              [(ngModel)]="strikeSearch"
          /></label>
          <label
            >Strike<select
              aria-label="Option strike"
              [ngModel]="selectedOption"
              (ngModelChange)="changeOption($event)"
            >
              @for (contract of filteredOptions(); track contract.symbol) {
                <option [value]="contract.symbol">
                  {{ contract.strike | number
                  }}{{ contract.symbol === nearestOption()?.symbol ? ' · ATM' : '' }}
                </option>
              }
            </select></label
          >
          <button class="btn" [disabled]="!nearestOption()" (click)="chooseOption(true)">
            Jump to ATM
          </button>
        }
        <button class="btn" [disabled]="contractsBusy()" (click)="loadOptionContracts()">
          {{ contractsBusy() ? 'Loading contracts…' : 'Load / refresh contracts' }}
        </button>
        @if (selectedOption) {
          <button class="btn" [disabled]="optionBusy()" (click)="loadOptionChart()">
            Refresh option chart
          </button>
        }
        <small class="muted"
          >Viewing a contract does not change your strategy or place a trade.</small
        >
      </div>
      @if (browseOptions && optionContracts().length) {
        <div class="toolbar">
          <span class="muted">Nearby strikes</span>
          @for (contract of nearbyOptions(); track contract.symbol) {
            <button
              class="btn small"
              [class.primary]="selectedOption === contract.symbol"
              [attr.aria-pressed]="selectedOption === contract.symbol"
              (click)="strikeSearch = ''; changeOption(contract.symbol)"
            >
              {{ contract.strike | number }}
            </button>
          }
          @if (!filteredOptions().length) {
            <span role="status"
              >No matching strikes. Clear your search to see available strikes.</span
            >
          }
          @if (optionCandles().length && selectedOption) {
            <strong
              >Premium
              {{ streamPrice(optionToken()) || optionCandles().at(-1)!.close | currency: 'INR' }}
              <small class="muted">{{
                streamPrice(optionToken()) ? 'latest streamed price' : 'last candle close'
              }}</small></strong
            >
          }
        </div>
      }
      <ax-error [text]="optionError()" />
      <div class="overview-chart-frame" #optionFrame>
        <div class="overview-chart-actions">
          <strong>{{ selectedOption || desk.state()?.option_label || 'Option premium' }}</strong>
          <button
            class="btn small"
            aria-label="Fit option chart data"
            [disabled]="
              optionBusy() ||
              !(browseOptions ? optionCandles().length : desk.state()?.option_prices?.length)
            "
            (click)="optionChart.fit()"
          >
            Fit data
          </button>
          <button
            class="btn small"
            aria-label="Show latest option candles"
            [disabled]="
              optionBusy() ||
              !(browseOptions ? optionCandles().length : desk.state()?.option_prices?.length)
            "
            (click)="optionChart.latest()"
          >
            Latest
          </button>
          <button
            class="btn small"
            aria-label="Focus option chart"
            (click)="focusChart(optionFrame)"
          >
            {{ focusedChart() === optionFrame ? 'Restore' : 'Focus' }}
          </button>
        </div>
        <div class="performance-chart">
          <ax-chart
            #optionChart
            [loading]="optionBusy()"
            [data]="displayOptionCandles()"
            emptyTitle="Follow your option premium"
            emptyText="Load contracts and choose an option, or follow the contract selected by your strategy."
          />
        </div>
      </div>
      @if (selectedOption && optionCandles().length) {
        <small class="muted"
          >5-minute candles · Latest: {{ formatTime(optionCandles().at(-1)!.time) }} · Auto-refresh
          every 30 seconds</small
        >
      }
      <ax-error [text]="chainError()" />
      @if (chain()) {
        <div class="toolbar">
          <span>NIFTY {{ chain().spot | number: '1.2-2' }}</span
          ><label
            >Expiry<select [(ngModel)]="expiry" (ngModelChange)="loadChain()">
              @for (date of chain().expiries; track date) {
                <option [value]="date">{{ date }}</option>
              }
            </select></label
          >
        </div>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Call OI</th>
                <th>Call LTP</th>
                <th>Strike</th>
                <th>Put LTP</th>
                <th>Put OI</th>
              </tr>
            </thead>
            <tbody>
              @for (row of chain().data; track row.strike) {
                <tr>
                  <td>{{ row.ce?.oi | number }}</td>
                  <td>{{ row.ce?.ltp | number: '1.2-2' }}</td>
                  <td>
                    <strong>{{ row.strike }}</strong>
                    @if (row.strike === chain().atm) {
                      <span class="pill">ATM</span>
                    }
                  </td>
                  <td>{{ row.pe?.ltp | number: '1.2-2' }}</td>
                  <td>{{ row.pe?.oi | number }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    </section>
    <div class="bottom-grid">
      <section class="panel">
        <div class="panel-heading">
          <h2>Recent trades</h2>
          <a routerLink="/results">View all ↗</a>
        </div>
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Instrument</th>
                <th>Date</th>
                <th>Mode</th>
                <th class="numeric">Net P&L</th>
              </tr>
            </thead>
            <tbody>
              @for (t of trades(); track t.id) {
                <tr>
                  <td>
                    <strong>{{ t.symbol || 'NIFTY' }}</strong
                    ><small>{{ t.position_type }}</small>
                  </td>
                  <td>{{ t.date }}</td>
                  <td>
                    <span class="pill">{{ t.trade_mode }}</span>
                  </td>
                  <td
                    class="numeric"
                    [class.positive]="t.net_pnl >= 0"
                    [class.negative]="t.net_pnl < 0"
                  >
                    {{ t.net_pnl | currency: 'INR' }}
                  </td>
                </tr>
              } @empty {
                <tr>
                  <td colspan="4" class="empty-cell">
                    A clean slate. Completed trades will appear here.
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading">
          <h2>Engine activity</h2>
          <span class="pill subtle">SESSION</span>
        </div>
        <div class="activity-list">
          @for (log of (desk.state()?.logs || []).slice(-6).reverse(); track $index) {
            <div class="activity-item">
              <i></i>
              <p>{{ log }}</p>
            </div>
          } @empty {
            <div class="compact-empty">
              <strong>All quiet for now</strong>
              <p>Engine events will appear as they happen.</p>
            </div>
          }
        </div>
      </section>
    </div>`,
})
export class Overview implements OnInit, OnDestroy {
  feed = inject(MarketFeed);
  streamPrice(token: number) {
    const snapshot = this.feed.snapshot();
    const quote = snapshot?.quotes?.[token];
    return snapshot?.status === 'connected' &&
      quote &&
      Math.max(Date.now() / 1000, snapshot.server_time) - quote.time < 15
      ? quote.price
      : null;
  }
  optionToken = signal(0);
  private niftyFetchedAt = 0;
  private optionFetchedAt = 0;
  niftyCandles() {
    return liveCandles(
      this.preview(),
      this.feed.snapshot()?.candles?.['256265'] || [],
      this.niftyFetchedAt,
    );
  }
  displayOptionCandles() {
    const token = this.browseOptions ? this.optionToken() : this.desk.state()?.option_token;
    const history = this.browseOptions
      ? this.optionCandles()
      : this.desk.state()?.option_prices || [];
    return liveCandles(
      history,
      this.feed.snapshot()?.candles?.[String(token)] || [],
      this.browseOptions ? this.optionFetchedAt : 0,
    );
  }
  focusedChart = signal<Element | null>(null);
  syncChartFocus() {
    this.focusedChart.set(document.fullscreenElement);
  }
  async focusChart(element: HTMLElement) {
    try {
      if (document.fullscreenElement === element) await document.exitFullscreen();
      else await element.requestFullscreen();
    } catch (e) {
      this.error.set('Could not focus chart: ' + message(e));
    }
  }
  api = inject(Api);
  auth = inject(Auth);
  desk = inject(Desk);
  feedback = inject(Feedback);
  summary = signal<Summary | null>(null);
  trades = signal<Trade[]>([]);
  error = signal('');
  busy = signal(false);
  popupSaving = signal(false);
  async toggleExecutionPopups() {
    this.popupSaving.set(true);
    try {
      const result = await this.api.post('/auth/profile', {
        trade_confirm_modal: this.auth.user()?.trade_confirm_modal === false,
      });
      this.auth.user.set(result.user);
      if (!result.user.trade_confirm_modal) this.feedback.executions.set([]);
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.popupSaving.set(false);
    }
  }
  chain = signal<any>(null);
  chainBusy = signal(false);
  chainError = signal('');
  expiry = '';
  mode = 'PAPER';
  router = inject(Router);
  private observedMode = '';
  preview = signal<Candle[]>([]);
  previewBusy = signal(false);
  previewError = signal('');
  private previewRequested = false;
  optionContracts = signal<any[]>([]);
  contractsBusy = signal(false);
  selectedOption = '';
  browseOptions = false;
  optionExpiry = '';
  optionType = 'CE';
  strikeSearch = '';
  optionExpiries() {
    return [...new Set<string>(this.optionContracts().map((c) => c.expiry))].sort();
  }
  matchingOptions() {
    return this.optionContracts()
      .filter((c) => c.expiry === this.optionExpiry && c.type === this.optionType)
      .sort((a, b) => a.strike - b.strike);
  }
  filteredOptions() {
    return this.matchingOptions().filter((c) =>
      String(c.strike).includes(this.strikeSearch.trim()),
    );
  }
  nearestOption() {
    const spot =
      (this.desk.state()?.engine_running ? this.desk.state()?.live_nifty_ltp : 0) ||
      this.preview().at(-1)?.close;
    if (!spot) return undefined;
    return this.matchingOptions().reduce(
      (best, c) => (!best || Math.abs(c.strike - spot) < Math.abs(best.strike - spot) ? c : best),
      undefined,
    );
  }
  nearbyOptions() {
    const options = this.matchingOptions();
    const center = options.findIndex(
      (c) => c.symbol === (this.nearestOption()?.symbol || this.selectedOption),
    );
    const start = Math.max(0, Math.min(center - 3, options.length - 7));
    return options.slice(start, start + 7);
  }
  chooseOption(atm = false) {
    const previous = this.optionContracts().find((c) => c.symbol === this.selectedOption);
    this.strikeSearch = '';
    const contract =
      (!atm && this.matchingOptions().find((c) => c.strike === previous?.strike)) ||
      this.nearestOption() ||
      this.matchingOptions()[0];
    this.changeOption(contract?.symbol || '');
  }
  followOption() {
    this.browseOptions = false;
    this.changeOption('');
  }
  async browseOptionContracts() {
    this.browseOptions = true;
    if (!this.optionContracts().length) await this.loadOptionContracts();
    else this.chooseOption();
  }
  optionCandles = signal<Candle[]>([]);
  optionBusy = signal(false);
  optionError = signal('');
  private optionRequest = 0;
  private optionPending = false;
  private previewPending = false;
  async loadOptionContracts() {
    this.contractsBusy.set(true);
    this.optionError.set('');
    try {
      const result = await this.api.get('/option-contracts');
      if (!this.destroyed) {
        this.optionContracts.set(result.contracts);
        if (!this.optionExpiries().includes(this.optionExpiry))
          this.optionExpiry = this.optionExpiries()[0] || '';
        if (this.browseOptions) this.chooseOption();
        if (!result.contracts.length)
          this.optionError.set(
            'No available NIFTY option contracts. Refresh after checking your Kite connection.',
          );
      }
    } catch (e) {
      this.optionError.set(message(e));
    } finally {
      this.contractsBusy.set(false);
    }
  }
  changeOption(symbol: string) {
    this.optionPending = false;
    this.optionToken.set(0);
    this.selectedOption = symbol;
    this.optionRequest++;
    this.optionCandles.set([]);
    this.optionError.set('');
    this.optionBusy.set(false);
    if (symbol) void this.loadOptionChart();
  }
  async loadOptionChart() {
    if (!this.selectedOption || this.optionPending) return;
    this.optionPending = true;
    const request = ++this.optionRequest;
    const fetchedAt = Date.now() / 1000;
    this.optionBusy.set(!this.optionCandles().length);
    this.optionError.set('');
    try {
      const result = await this.api.get(
        '/option-chart?symbol=' + encodeURIComponent(this.selectedOption),
      );
      if (!this.destroyed && request === this.optionRequest) {
        this.optionFetchedAt = result.history_as_of || fetchedAt;
        this.optionCandles.set(result.data);
        this.optionToken.set(result.token || 0);
      }
    } catch (e) {
      if (request === this.optionRequest) this.optionError.set(message(e));
    } finally {
      if (request === this.optionRequest) {
        this.optionBusy.set(false);
        this.optionPending = false;
      }
    }
  }
  formatTime = formatChartTime;
  private previewTimer = setInterval(() => {
    if (!document.hidden && this.desk.state() && !this.desk.state()?.kite_auth_error) {
      void this.loadMarketPreview();
      void this.loadOptionChart();
    }
  }, 30000);
  private destroyed = false;
  ngOnDestroy() {
    this.feed.stop();
    this.destroyed = true;
    clearInterval(this.previewTimer);
  }
  constructor() {
    effect(() => {
      const state = this.desk.state();
      const token = this.browseOptions ? this.optionToken() : state?.option_token;
      const authenticated = this.auth.token();
      untracked(() =>
        this.feed.configure(
          state && !state.kite_auth_error && authenticated
            ? [256265, ...(token ? [token] : [])]
            : [],
        ),
      );
    });
    effect(() => {
      const state = this.desk.state();
      if (state && state.app_mode !== this.observedMode) {
        this.observedMode = state.app_mode;
        this.mode = state.app_mode;
      }
      if (!state || state.kite_auth_error) {
        this.previewRequested = false;
        return;
      }
      if (!this.previewRequested) {
        this.previewRequested = true;
        untracked(() => void this.loadMarketPreview());
      }
    });
  }
  async loadMarketPreview() {
    if (this.previewPending) return;
    this.previewPending = true;
    const fetchedAt = Date.now() / 1000;
    this.previewBusy.set(!this.preview().length);
    this.previewError.set('');
    const from = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
    try {
      const result = await this.api.get(
        '/chart/history?symbol=NIFTY%2050&interval=5minute&from=' + from,
      );
      if (!this.destroyed) {
        this.niftyFetchedAt = fetchedAt;
        this.preview.set(result.data);
      }
    } catch (e) {
      if (!this.destroyed) this.previewError.set(message(e));
    } finally {
      this.previewBusy.set(false);
      this.previewPending = false;
    }
  }
  async ngOnInit() {
    try {
      const [s, t] = await Promise.all([
        this.api.get('/analytics/summary'),
        this.api.get('/trades?per_page=5'),
      ]);
      this.summary.set(s);
      this.trades.set(t.trades);
    } catch (e) {
      this.error.set(message(e));
    }
  }
  async loadChain() {
    this.chainBusy.set(true);
    this.chainError.set('');
    try {
      const d = await this.api.get(
        '/option-chain-nse' + (this.expiry ? '?expiry=' + encodeURIComponent(this.expiry) : ''),
      );
      this.chain.set(d);
      this.expiry = d.expiry;
    } catch (e) {
      this.chainError.set(message(e));
    } finally {
      this.chainBusy.set(false);
    }
  }
  async changeMode(selectedMode: string) {
    await this.run(async () => {
      await this.api.post('/mode', { mode: selectedMode });
      if (selectedMode === 'BACKTEST') await this.router.navigateByUrl('/backtests');
    });
    this.mode = this.desk.state()?.app_mode || 'PAPER';
  }
  async start() {
    if (
      !(await this.feedback.confirm(
        'Enable ' + this.mode.toLowerCase() + ' trading?',
        this.mode === 'LIVE'
          ? 'This enables real orders on your connected broker account.'
          : 'The engine will use live prices with simulated orders.',
        'Enable trading',
        this.mode === 'LIVE',
      ))
    )
      return;
    await this.run(async () => {
      await this.api.post('/mode', { mode: this.mode });
      await this.api.post('/trades-enabled', { enabled: true });
    });
  }
  async background() {
    const enabled = !this.desk.state()?.background_trading;
    if (
      enabled &&
      !(await this.feedback.confirm(
        'Resume paper trading after restart?',
        'With a valid Kite session, the server may automatically start the paper engine. Browser closure does not stop a running engine.',
        'Enable auto-resume',
      ))
    )
      return;
    await this.run(() => this.api.post('/background-trading', { enabled }));
  }
  async pause() {
    await this.run(() => this.api.post('/trades-enabled', { enabled: false }));
  }
  async manual(action: string, direction = 'CALL') {
    if (
      !(await this.feedback.confirm(
        action === 'exit' ? 'Exit current position?' : 'Buy ' + direction + '?',
        `Execution mode: ${this.desk.state()?.app_mode}. This queues a market action for the options engine.`,
        'Confirm order',
        this.desk.state()?.app_mode === 'LIVE',
      ))
    )
      return;
    await this.run(() => this.api.post('/manual-trade', { action, direction }));
  }
  async run(fn: () => Promise<unknown>) {
    this.busy.set(true);
    this.error.set('');
    try {
      await fn();
      await this.desk.refresh();
      this.feedback.notify('Trading controls updated.');
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
}
