import { Component, inject, signal, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CurrencyPipe, JsonPipe } from '@angular/common';
import { Api, Auth, message } from '../core/api';
import { Heading, Stat, ErrorBox } from '../shared/ui';
import { Chart } from '../shared/chart';
import { Icon } from '../shared/icon';
@Component({
  selector: 'ax-backtests',
  imports: [FormsModule, CurrencyPipe, JsonPipe, Heading, Stat, ErrorBox, Icon, Chart],
  template: `<ax-heading
      title="Test the idea. Build conviction."
      subtitle="Explore historical outcomes before committing capital."
      eyebrow="BACKTEST LAB"
    /><ax-error [text]="error()" />
    <p class="panel-copy">
      Connect Kite in Profile, choose a completed session or date range, then run the test.
      Backtests place no broker orders and do not change your running execution mode. Estimated
      option prices may be used when historical contracts are unavailable.
    </p>
    <div class="lab-grid">
      <form class="panel lab-form" (ngSubmit)="run()">
        <div class="panel-heading">
          <h2>Configure your test</h2>
          <ax-icon name="backtests" />
        </div>
        <div class="lab-engine">
          <span class="strategy-icon"><ax-icon name="bolt" /></span>
          <div>
            <strong>NIFTY options ORB</strong>
            <p>Opening range breakout</p>
          </div>
        </div>
        <label
          >Test type<select name="type" [(ngModel)]="type">
            <option value="single">Single session</option>
            <option value="range">Date range</option>
            <option value="optimize">Parameter optimization</option>
          </select></label
        >
        @if (type === 'single') {
          <label
            >Session date<input type="date" name="day" [(ngModel)]="day" required [max]="maxDate"
          /></label>
        } @else {
          <div class="form-grid">
            <label
              >From<input
                type="date"
                name="from"
                [(ngModel)]="from"
                required
                [max]="to || maxDate" /></label
            ><label
              >To<input
                type="date"
                name="to"
                [(ngModel)]="to"
                required
                [min]="from"
                [max]="maxDate"
            /></label>
          </div>
        }
        <label
          >Direction<select name="direction" [(ngModel)]="direction">
            <option>BOTH</option>
            <option>CALL</option>
            <option>PUT</option>
          </select></label
        >
        <div class="form-grid">
          <label
            >Target points<input
              type="number"
              name="target"
              [(ngModel)]="target"
              min="1"
              required /></label
          ><label
            >Opening range end<input type="time" name="orEnd" [(ngModel)]="orEnd" required
          /></label>
        </div>
        @if (type === 'optimize') {
          <label
            >Targets to compare<input
              name="targets"
              [(ngModel)]="targets"
              placeholder="80, 100, 130, 160"
              required /></label
          ><label
            >Opening times to compare<input
              name="times"
              [(ngModel)]="times"
              placeholder="09:25, 09:35, 09:45"
              required /></label
          ><label
            >Rank by<select name="metric" [(ngModel)]="metric">
              <option value="total_pnl">Net P&L</option>
              <option value="win_rate">Win rate</option>
              <option value="profit_factor">Profit factor</option>
            </select></label
          >
        }
        <button class="btn primary full" [disabled]="busy()">
          <ax-icon name="backtests" />{{ busy() ? 'Running historical test…' : 'Run backtest' }}
        </button>
        <p class="muted text-small">
          Requires an authenticated Kite historical-data session. Trading charges and model
          assumptions follow your Backtest settings.
        </p>
      </form>
      <div class="lab-results">
        @if (result()) {
          <div class="stats-grid two">
            <ax-stat
              label="Net P&L"
              prefix="₹"
              [value]="result().total_pnl ?? result().net_pnl ?? null"
              note="Historical result"
            /><ax-stat
              label="Win rate"
              suffix="%"
              [value]="result().win_rate ?? null"
              note="Across tested sessions"
            />
          </div>
          <section class="panel">
            @if (result().candles?.length) {
              <div class="performance-chart"><ax-chart [data]="result().candles" /></div>
            }
            @if (result().cumulative?.length) {
              <div class="performance-chart"><ax-chart type="area" [data]="curve()" /></div>
            }
            <p class="muted text-small">
              Options prices may use Black–Scholes estimates when historical contract data is
              unavailable. Inspect the complete result for data-source flags and assumptions.
            </p>
            <div class="panel-heading">
              <h2>Test results</h2>
              <span class="pill success">COMPLETE</span>
            </div>
            @if (rows().length) {
              <div class="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Session / OR end</th>
                      <th>Direction</th>
                      <th>Target</th>
                      <th class="numeric">P&L</th>
                      <th>Result</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (r of rows(); track $index) {
                      <tr>
                        <td>{{ r.date || r.or_end_time || r.or_time }}</td>
                        <td>{{ r.direction || r.position_type || '—' }}</td>
                        <td>{{ r.target_pts || '—' }}</td>
                        <td class="numeric">
                          {{ r.total_pnl ?? r.net_pnl ?? r.pnl | currency: 'INR' }}
                        </td>
                        <td>{{ r.exit_reason || (r.trade_taken === false ? 'No entry' : '—') }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            }
            <details>
              <summary>Complete result and assumptions</summary>
              <pre class="json-output">{{ result() | json }}</pre>
            </details>
          </section>
        } @else {
          <section class="panel lab-empty">
            <div class="lab-grid-art"><ax-icon name="backtests" /></div>
            <span class="eyebrow">A SANDBOX FOR YOUR NEXT IDEA</span>
            <h2>
              {{
                busy()
                  ? 'Putting your strategy to the test…'
                  : 'The best trades start with a question.'
              }}
            </h2>
            <p>
              {{
                busy()
                  ? 'Historical data is being loaded and evaluated. Larger date ranges take longer.'
                  : 'Choose a session or date range, tune the opening range and target, then explore the result here.'
              }}
            </p>
            <div class="lab-tags">
              <span class="pill subtle">Historical sessions</span
              ><span class="pill subtle">Cost-aware results</span
              ><span class="pill subtle">Parameter search</span>
            </div>
          </section>
        }
      </div>
    </div>`,
})
export class Backtests implements OnInit {
  api = inject(Api);
  auth = inject(Auth);
  busy = signal(false);
  error = signal('');
  result = signal<any>(null);
  type = 'single';
  maxDate = new Date(Date.now() - 86400000).toISOString().slice(0, 10);
  day = this.maxDate;
  from = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  to = this.maxDate;
  direction = 'BOTH';
  target: number | null = null;
  orEnd = '';
  async ngOnInit() {
    try {
      const settings = await this.api.get('/settings?mode=BACKTEST');
      this.target = settings.target_pts;
      this.orEnd = settings.or_end_time;
      this.direction = settings.trade_direction;
    } catch (e) {
      this.error.set(message(e));
    }
  }
  targets = '80, 100, 130, 160';
  times = '09:25, 09:35, 09:45';
  metric = 'total_pnl';
  rows() {
    const r = this.result();
    return r?.results || r?.daily_results || r?.daily || r?.cumulative || (r?.date ? [r] : []);
  }
  curve() {
    return (this.result()?.cumulative || []).map((r: any) => ({
      time: r.date,
      value: r.cumulative,
    }));
  }
  async run() {
    this.busy.set(true);
    this.error.set('');
    this.result.set(null);
    try {
      const body =
        this.type === 'optimize'
          ? {
              from_date: this.from,
              to_date: this.to,
              targets: this.targets.split(',').map(Number),
              or_times: this.times.split(',').map((s) => s.trim()),
              directions: [this.direction],
              metric: this.metric,
            }
          : {
              mode: this.type,
              date: this.day,
              from_date: this.from,
              to_date: this.to,
              direction: this.direction,
              target_pts: this.target,
              or_end_time: this.orEnd,
            };
      this.result.set(
        await this.api.post('/backtest/' + (this.type === 'optimize' ? 'optimize' : 'run'), body),
      );
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
}
