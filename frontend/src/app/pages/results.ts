import { Component, inject, signal, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CurrencyPipe, DecimalPipe } from '@angular/common';
import { Api, message, query } from '../core/api';
import { Feedback } from '../core/feedback';
import { Summary, Strategy, Trade } from '../core/models';
import { Heading, Stat, ErrorBox } from '../shared/ui';
import { Icon } from '../shared/icon';
import { Chart } from '../shared/chart';
@Component({
  selector: 'ax-results',
  imports: [FormsModule, CurrencyPipe, DecimalPipe, Heading, Stat, ErrorBox, Icon, Chart],
  template: `<ax-heading
      title="Know what’s working."
      subtitle="Every trade tells a story. Look at the whole picture."
      eyebrow="PERFORMANCE & JOURNAL"
      ><label class="btn upload"
        ><ax-icon name="plus" />Import trades<input
          type="file"
          accept=".csv"
          [disabled]="busy()"
          (change)="importFile($event)" /></label
      ><button class="btn primary" [disabled]="busy() || !total()" (click)="exportAll()">
        <ax-icon name="download" />Export CSV
      </button></ax-heading
    ><ax-error [text]="error()" />
    <form class="panel toolbar" (ngSubmit)="page.set(1); load()">
      <label>From<input type="date" name="from" [(ngModel)]="from" /></label
      ><label>To<input type="date" name="to" [(ngModel)]="to" /></label
      ><label
        >Mode<select name="mode" [(ngModel)]="mode">
          <option value="ALL">All modes</option>
          <option>PAPER</option>
          <option>LIVE</option>
          <option>IMPORT</option>
        </select></label
      ><label
        >Strategy<select name="strategy" [(ngModel)]="strategy">
          <option value="">All strategies</option>
          @for (s of strategies(); track s.id) {
            <option [value]="s.id">{{ s.name }}</option>
          }
        </select></label
      ><button class="btn primary" [disabled]="busy()">
        {{ busy() ? 'Loading…' : 'Apply filters' }}</button
      ><button class="btn" type="button" (click)="clear()">Clear</button>
    </form>
    <div class="stats-grid">
      <ax-stat
        label="Net P&L"
        prefix="₹"
        [value]="summary()?.total_net_pnl ?? null"
        [tone]="(summary()?.total_net_pnl || 0) >= 0 ? 'positive' : 'negative'"
        note="After recorded charges"
      /><ax-stat
        label="Win rate"
        suffix="%"
        [value]="summary()?.win_rate ?? null"
        [note]="(summary()?.total_trades || 0) + ' completed trades'"
      /><ax-stat
        label="Profit factor"
        [value]="summary()?.profit_factor ?? null"
        note="Gross wins / gross losses"
      /><ax-stat
        label="Max drawdown"
        prefix="₹"
        [value]="summary()?.max_drawdown ?? null"
        note="Peak-to-trough cumulative P&L"
      />
    </div>
    <div class="performance-grid">
      <section class="panel">
        <div class="panel-heading">
          <h2>Cumulative P&L</h2>
          <span class="pill subtle">{{ mode === 'ALL' ? 'ALL MODES' : mode }}</span>
        </div>
        <div class="performance-chart">
          <ax-chart
            type="area"
            [data]="curve()"
            emptyTitle="Your track record starts here"
            emptyText="Completed trades in this date range build your performance curve."
          />
        </div>
      </section>
      <section class="panel">
        <div class="panel-heading">
          <h2>Monthly performance</h2>
          <ax-icon name="results" />
        </div>
        <div class="monthly-list">
          @for (m of monthly(); track m.month) {
            <div class="detail-row">
              <span>{{ m.month }}</span
              ><strong [class.positive]="m.net_pnl >= 0" [class.negative]="m.net_pnl < 0">{{
                m.net_pnl | currency: 'INR'
              }}</strong>
            </div>
          } @empty {
            <div class="compact-empty">
              <strong>No monthly data</strong>
              <p>Try a wider date range.</p>
            </div>
          }
        </div>
        <div class="detail-row">
          <span>Total charges</span
          ><strong>{{ summary()?.total_charges | currency: 'INR' }}</strong>
        </div>
        <div class="detail-row">
          <span>Average trade</span><strong>{{ summary()?.avg_trade | currency: 'INR' }}</strong>
        </div>
        <div class="detail-row">
          <span>Daily P&L Sharpe</span
          ><strong>{{ summary()?.sharpe_ratio | number: '1.2-2' }}</strong>
        </div>
      </section>
    </div>
    <section class="panel">
      <div class="panel-heading">
        <div>
          <h2>
            Trade journal <span class="count">{{ total() }}</span>
          </h2>
          <p class="muted">All figures in INR. Your filters apply to the entire journal.</p>
        </div>
        <button class="btn small" [disabled]="busy()" (click)="syncKite()">
          <ax-icon name="refresh" />Sync today from Kite
        </button>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Date</th>
              <th>Instrument</th>
              <th>Mode</th>
              <th>Side</th>
              <th class="numeric">Qty</th>
              <th class="numeric">Entry</th>
              <th class="numeric">Exit</th>
              <th class="numeric">Charges</th>
              <th class="numeric">Net P&L</th>
              <th>Exit reason</th>
            </tr>
          </thead>
          <tbody>
            @for (t of trades(); track t.id) {
              <tr>
                <td>{{ t.date }}</td>
                <td>
                  <strong>{{ t.symbol || 'NIFTY' }}</strong
                  ><small>{{ t.strategy_name }}</small>
                </td>
                <td>
                  <span class="pill subtle">{{ t.trade_mode }}</span>
                </td>
                <td>{{ t.position_type }}</td>
                <td class="numeric">{{ t.quantity }}</td>
                <td class="numeric">{{ t.entry_prem | number: '1.2-2' }}</td>
                <td class="numeric">{{ t.exit_prem | number: '1.2-2' }}</td>
                <td class="numeric">{{ t.charges | number: '1.2-2' }}</td>
                <td
                  class="numeric"
                  [class.positive]="t.net_pnl >= 0"
                  [class.negative]="t.net_pnl < 0"
                >
                  {{ t.net_pnl | currency: 'INR' }}
                </td>
                <td>{{ t.exit_reason }}</td>
              </tr>
            } @empty {
              <tr>
                <td colspan="10" class="empty-cell">
                  {{ busy() ? 'Loading trades…' : 'No trades match these filters.' }}
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span>Page {{ page() }} of {{ pages() }}</span>
        <div class="button-row">
          <button class="btn small" [disabled]="busy() || page() <= 1" (click)="move(-1)">
            ← Previous</button
          ><button class="btn small" [disabled]="busy() || page() >= pages()" (click)="move(1)">
            Next →
          </button>
        </div>
      </div>
    </section>
    <section class="panel">
      <div class="panel-heading">
        <h2>Paper vs live</h2>
        <button class="btn small" (click)="compare()">Compare modes</button>
      </div>
      @if (comparison()) {
        <div class="form-grid">
          @for (key of ['paper', 'live']; track key) {
            <div class="comparison-card">
              <div class="eyebrow">{{ key }}</div>
              <h2>{{ comparison()[key]?.total_net_pnl | currency: 'INR' }}</h2>
              <p>
                {{ comparison()[key]?.total_trades || 0 }} trades ·
                {{ comparison()[key]?.win_rate || 0 }}% win rate
              </p>
            </div>
          }
        </div>
      } @else {
        <p class="muted">Compare performance across execution modes for your selected dates.</p>
      }
    </section>`,
})
export class Results implements OnInit {
  api = inject(Api);
  feedback = inject(Feedback);
  summary = signal<Summary | null>(null);
  trades = signal<Trade[]>([]);
  curve = signal<any[]>([]);
  monthly = signal<any[]>([]);
  strategies = signal<Strategy[]>([]);
  total = signal(0);
  page = signal(1);
  busy = signal(false);
  error = signal('');
  comparison = signal<any>(null);
  from = '';
  to = '';
  mode = 'ALL';
  strategy = '';
  pages() {
    return Math.max(1, Math.ceil(this.total() / 20));
  }
  params(extra: Record<string, unknown> = {}) {
    return query({
      from: this.from,
      to: this.to,
      mode: this.mode,
      strategy_id: this.strategy,
      ...extra,
    });
  }
  async ngOnInit() {
    void this.load();
    try {
      this.strategies.set((await this.api.get('/strategies')).strategies);
    } catch (e) {
      this.error.set(message(e));
    }
  }
  async load() {
    this.busy.set(true);
    this.error.set('');
    this.comparison.set(null);
    try {
      const [s, c, m, t] = await Promise.all([
        this.api.get('/analytics/summary' + this.params()),
        this.api.get('/analytics/equity-curve' + this.params()),
        this.api.get('/analytics/monthly' + this.params()),
        this.api.get('/trades' + this.params({ page: this.page(), per_page: 20 })),
      ]);
      this.summary.set(s);
      this.curve.set(c.data.map((r: any) => ({ time: r.time, value: r.cumulative_pnl })));
      this.monthly.set(m.data);
      this.trades.set(t.trades);
      this.total.set(t.total);
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
  clear() {
    this.from = '';
    this.to = '';
    this.mode = 'ALL';
    this.strategy = '';
    this.page.set(1);
    void this.load();
  }
  move(n: number) {
    this.page.update((p) => p + n);
    void this.load();
  }
  async compare() {
    try {
      this.comparison.set((await this.api.get('/analytics/compare' + this.params())).data);
    } catch (e) {
      this.error.set(message(e));
    }
  }
  async importFile(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    this.busy.set(true);
    try {
      const data = new FormData();
      data.append('file', file);
      const d = await this.api.post('/analytics/import-csv', data);
      this.feedback.notify(d.message || 'Trades imported.');
      await this.load();
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
      input.value = '';
    }
  }
  async syncKite() {
    if (
      !(await this.feedback.confirm(
        'Sync today’s completed Kite trades?',
        'Existing records will be kept. Only missing completed fills will be imported.',
        'Sync trades',
      ))
    )
      return;
    this.busy.set(true);
    try {
      const d = await this.api.post('/kite/trades/sync');
      this.feedback.notify(d.message);
      await this.load();
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
  async exportAll() {
    this.busy.set(true);
    try {
      let rows: Trade[] = [];
      for (let p = 1; p <= Math.ceil(this.total() / 100); p++) {
        const d = await this.api.get('/trades' + this.params({ page: p, per_page: 100 }));
        rows.push(...d.trades);
      }
      const cell = (v: unknown) => {
        const value = typeof v === 'number' ? String(v) : String(v ?? '').replace(/^[=+@-]/, "'$&");
        return '"' + value.replaceAll('"', '""') + '"';
      };
      const csv = [
        ['Date', 'Symbol', 'Mode', 'Side', 'Quantity', 'Entry', 'Exit', 'Charges', 'Net P&L'],
        ...rows.map((t) => [
          t.date,
          t.symbol,
          t.trade_mode,
          t.position_type,
          t.quantity,
          t.entry_prem,
          t.exit_prem,
          t.charges,
          t.net_pnl,
        ]),
      ]
        .map((r) => r.map(cell).join(','))
        .join('\r\n');
      const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = 'axiom-trades.csv';
      a.click();
      URL.revokeObjectURL(url);
      this.feedback.notify('All ' + rows.length + ' filtered trades exported.');
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
}
