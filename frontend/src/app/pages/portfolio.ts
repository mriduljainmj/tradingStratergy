import { Component, inject, signal, computed, OnInit, OnDestroy } from '@angular/core';
import { CurrencyPipe, DatePipe } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Api, message } from '../core/api';
import { Heading, ErrorBox, Stat } from '../shared/ui';
import { portfolioTotals } from '../shared/portfolio-totals';

interface Holding {
  tradingsymbol: string;
  exchange: string;
  product: string;
  quantity: number | null;
  average_price: number | null;
  last_price: number | null;
  pnl: number | null;
  t1_quantity?: number | null;
  used_quantity?: number | null;
  collateral_quantity?: number | null;
  discrepancy?: boolean;
  mtf?: { quantity: number; average_price: number } | null;
}
interface PortfolioSnapshot {
  holdings: Holding[];
  positions: Holding[];
  fetched_at: string;
}
@Component({
  selector: 'ax-portfolio',
  imports: [Heading, ErrorBox, Stat, CurrencyPipe, DatePipe, RouterLink],
  template: `<ax-heading
      title="Your Kite portfolio."
      eyebrow="BROKER ACCOUNT"
      subtitle="Delivery holdings and broker positions from your connected Kite account."
    >
      <button class="btn primary" [disabled]="busy()" (click)="refresh()">
        {{ busy() ? 'Refreshing…' : 'Refresh portfolio' }}
      </button>
    </ax-heading>
    <ax-error [text]="error()" />
    <p class="panel-copy">
      These are your actual broker holdings, regardless of the application's Paper or Live mode.
      This page is read-only; it does not place orders.
    </p>
    <div class="button-row">
      <a class="btn" routerLink="/profile">Manage Kite connection</a>
      @if (data(); as snapshot) {
        <span class="muted" role="status"
          >{{ error() ? 'Last successful snapshot' : 'Updated' }}:
          {{ snapshot.fetched_at | date: 'dd MMM yyyy, HH:mm:ss' : '+0530' }} IST · refreshes every
          30 seconds while open</span
        >
      }
    </div>
    @if (data(); as snapshot) {
      <div class="portfolio-summary">
        <ax-stat
          label="Invested value"
          prefix="₹"
          [value]="totals().invested"
          note="Cost of current holdings, including T1 and MTF"
        />
        <ax-stat
          label="Current value"
          prefix="₹"
          [value]="totals().current"
          note="Current holdings valued at Kite's last prices"
        />
        <ax-stat
          label="Total holdings P&L"
          prefix="₹"
          [value]="totals().pnl"
          [tone]="(totals().pnl || 0) < 0 ? 'negative' : 'positive'"
          note="Sum of holding P&L reported by Kite"
        />
        <ax-stat
          label="Positions P&L"
          prefix="₹"
          [value]="totals().positionsPnl"
          [tone]="(totals().positionsPnl || 0) < 0 ? 'negative' : 'positive'"
          note="All net positions, including closed positions"
        />
      </div>
      @if (
        totals().invested === null ||
        totals().current === null ||
        totals().pnl === null ||
        totals().positionsPnl === null
      ) {
        <p class="panel-copy" role="status">
          Some broker values are missing or have a cost discrepancy. Affected totals show — until
          complete data is available.
        </p>
      }
      <section class="panel portfolio-section">
        <div class="panel-heading">
          <h2>
            Holdings <span class="count">{{ snapshot.holdings.length }}</span>
          </h2>
        </div>
        <p class="panel-copy">
          Values include settled, T1 (unsettled) and MTF holdings. Pledged quantities are shown when
          present.
        </p>
        @if (snapshot.holdings.length) {
          <div class="portfolio-table">
            <table>
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Settled quantity</th>
                  <th>Average price</th>
                  <th>Invested value</th>
                  <th>Last price</th>
                  <th>Current value</th>
                  <th>Holding P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                @for (row of snapshot.holdings; track $index) {
                  <tr>
                    <td>
                      <strong>{{ row.tradingsymbol }}</strong>
                      <div class="muted">{{ row.exchange }} · {{ row.product }}</div>
                      @if (row.collateral_quantity) {
                        <small>Pledged: {{ row.collateral_quantity }}</small>
                      }
                      @if (row.discrepancy) {
                        <small class="negative">Kite reports a price discrepancy</small>
                      }
                      @if (row.mtf?.quantity) {
                        <small>MTF: {{ row.mtf?.quantity }}</small>
                      }
                    </td>
                    <td>{{ row.quantity ?? '—' }}</td>
                    <td>
                      {{ row.average_price === null ? '—' : (row.average_price | currency: 'INR') }}
                    </td>
                    <td>
                      {{
                        investedValue(row) === null ? '—' : (investedValue(row) | currency: 'INR')
                      }}
                    </td>
                    <td>
                      {{ row.last_price === null ? '—' : (row.last_price | currency: 'INR') }}
                    </td>
                    <td>
                      {{ currentValue(row) === null ? '—' : (currentValue(row) | currency: 'INR') }}
                    </td>
                    <td [class.positive]="(row.pnl ?? 0) > 0" [class.negative]="(row.pnl ?? 0) < 0">
                      {{ row.pnl === null ? '—' : (row.pnl | currency: 'INR') }}
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <p class="panel-copy">Kite returned no delivery holdings for this account.</p>
        }
      </section>
      <section class="panel">
        <div class="panel-heading">
          <h2>
            Open broker positions <span class="count">{{ openPositions().length }}</span>
          </h2>
        </div>
        <p class="panel-copy">
          Includes positions opened outside this application. Broker P&amp;L is shown as returned by
          Kite.
        </p>
        @if (openPositions().length) {
          <div class="portfolio-table">
            <table>
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Product</th>
                  <th>Net quantity</th>
                  <th>Average price</th>
                  <th>Last price</th>
                  <th>Broker P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                @for (row of openPositions(); track $index) {
                  <tr>
                    <td>
                      <strong>{{ row.tradingsymbol }}</strong>
                      <div class="muted">{{ row.exchange }}</div>
                    </td>
                    <td>{{ row.product }}</td>
                    <td>{{ row.quantity }}</td>
                    <td>
                      {{ row.average_price === null ? '—' : (row.average_price | currency: 'INR') }}
                    </td>
                    <td>
                      {{ row.last_price === null ? '—' : (row.last_price | currency: 'INR') }}
                    </td>
                    <td [class.positive]="(row.pnl ?? 0) > 0" [class.negative]="(row.pnl ?? 0) < 0">
                      {{ row.pnl === null ? '—' : (row.pnl | currency: 'INR') }}
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        } @else {
          <p class="panel-copy">No open broker positions.</p>
        }
      </section>
    } @else if (busy()) {
      <div class="panel portfolio-section" role="status">Loading your Kite portfolio…</div>
    } `,
  styles: `
    .portfolio-summary {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 16px;
      margin-top: 22px;
    }
    @media (max-width: 1100px) {
      .portfolio-summary {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    @media (max-width: 560px) {
      .portfolio-summary {
        grid-template-columns: minmax(0, 1fr);
      }
    }
    .portfolio-section {
      margin-top: 22px;
    }
    .portfolio-table {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 680px;
    }
    th,
    td {
      text-align: right;
      padding: 13px;
      border-bottom: 1px solid var(--line);
      white-space: nowrap;
    }
    th:first-child,
    td:first-child {
      text-align: left;
    }
    th {
      color: var(--muted);
      font-size: 11px;
    }
    small {
      display: block;
    }
  `,
})
export class Portfolio implements OnInit, OnDestroy {
  currentValue(row: Holding) {
    return portfolioTotals([row], []).current;
  }
  investedValue(row: Holding) {
    return portfolioTotals([row], []).invested;
  }
  api = inject(Api);
  data = signal<PortfolioSnapshot | null>(null);
  totals = computed(() =>
    portfolioTotals(this.data()?.holdings || [], this.data()?.positions || []),
  );
  busy = signal(false);
  error = signal('');
  private timer?: ReturnType<typeof setInterval>;
  private destroyed = false;
  openPositions() {
    return this.data()?.positions.filter((p) => p.quantity !== null && p.quantity !== 0) || [];
  }
  ngOnInit() {
    void this.refresh();
    this.timer = setInterval(() => {
      if (!document.hidden) void this.refresh();
    }, 30000);
  }
  ngOnDestroy() {
    this.destroyed = true;
    clearInterval(this.timer);
  }
  async refresh() {
    if (this.busy()) return;
    this.busy.set(true);
    try {
      const snapshot = await this.api.get<PortfolioSnapshot>('/portfolio');
      if (!this.destroyed) {
        this.data.set(snapshot);
        this.error.set('');
      }
    } catch (e) {
      if (!this.destroyed) this.error.set(message(e));
    } finally {
      if (!this.destroyed) this.busy.set(false);
    }
  }
}
