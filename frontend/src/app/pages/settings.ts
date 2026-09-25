import { Component, inject, signal, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Auth, message } from '../core/api';
import { Feedback } from '../core/feedback';
import { Heading, ErrorBox } from '../shared/ui';
import { Icon } from '../shared/icon';
@Component({
  selector: 'ax-settings',
  imports: [FormsModule, Heading, ErrorBox, Icon],
  template: `<ax-heading
      title="A workspace that works your way."
      subtitle="Fine-tune your strategy, risk and execution assumptions."
      eyebrow="TRADING SETTINGS"
    /><ax-error [text]="error()" />
    <div class="settings-layout">
      <aside class="panel settings-guide">
        <ax-icon name="settings" />
        <h2>Set your boundaries.</h2>
        <p>Settings are saved separately for Paper, Live and Backtest modes.</p>
        <p>
          Changes to the active mode apply to its engine. Running positions must be closed before
          changing risk settings.
        </p>
        <span class="pill subtle">{{
          auth.user()?.is_admin ? 'ADMIN CONTROLS' : 'READ ONLY'
        }}</span>
      </aside>
      <form class="panel" (ngSubmit)="save()">
        <div class="toolbar">
          <h2>Strategy configuration</h2>
          <select
            name="mode"
            aria-label="Settings mode"
            [(ngModel)]="mode"
            (ngModelChange)="load()"
          >
            <option>PAPER</option>
            <option>LIVE</option>
            <option>BACKTEST</option>
          </select>
        </div>
        <fieldset [disabled]="!auth.user()?.is_admin || busy()">
          <h3 class="form-section-title">Entry & exit</h3>
          <div class="form-grid">
            @for (f of strategyFields; track f.key) {
              <label
                >{{ f.label
                }}<input
                  [name]="f.key"
                  [type]="f.type || 'number'"
                  [(ngModel)]="form[f.key]"
                  [min]="f.min ?? 0"
                  [step]="f.step || '1'"
                  required
              /></label>
            }
            <label
              >Trade direction<select name="direction" [(ngModel)]="form['trade_direction']">
                <option>BOTH</option>
                <option>CALL</option>
                <option>PUT</option>
              </select></label
            >
          </div>
          <h3 class="form-section-title">Position & risk</h3>
          <div class="form-grid">
            @for (f of riskFields; track f.key) {
              <label
                >{{ f.label
                }}<input
                  [name]="f.key"
                  type="number"
                  [(ngModel)]="form[f.key]"
                  [min]="f.min"
                  [step]="f.step || 1"
                  required
              /></label>
            }
          </div>
          <details>
            <summary>Pricing, brokerage & slippage assumptions</summary>
            <div class="form-grid">
              @for (f of costFields; track f.key) {
                <label
                  >{{ f.label
                  }}<input
                    [name]="f.key"
                    type="number"
                    [(ngModel)]="form[f.key]"
                    min="0"
                    step="0.000001"
                    required
                /></label>
              }
            </div>
          </details>
          <div class="modal-actions">
            <button class="btn primary" [disabled]="busy()">
              {{ busy() ? 'Saving…' : 'Save ' + mode.toLowerCase() + ' settings' }}
            </button>
          </div>
        </fieldset>
      </form>
    </div>`,
})
export class Settings implements OnInit {
  api = inject(Api);
  auth = inject(Auth);
  feedback = inject(Feedback);
  error = signal('');
  busy = signal(false);
  mode = 'PAPER';
  form: Record<string, any> = {};
  strategyFields: any[] = [
    { key: 'target_pts', label: 'Target premium points', min: 1 },
    { key: 'fib_trail', label: 'Fibonacci trailing ratio', step: '.01' },
    { key: 'or_end_time', label: 'Opening range end', type: 'time' },
    { key: 'entry_end_time', label: 'Last entry time', type: 'time' },
    { key: 'eod_exit_time', label: 'Forced exit time', type: 'time' },
    { key: 'strike_spacing', label: 'Strike spacing', min: 1 },
  ];
  riskFields: any[] = [
    { key: 'paper_starting_balance', label: 'Paper starting balance ₹ (new sessions)', min: 0 },
    { key: 'lot_size', label: 'Contract lot size', min: 1 },
    { key: 'qty_multiplier', label: 'Number of lots', min: 1 },
    { key: 'max_daily_loss', label: 'Daily loss limit ₹ (0 = off)', min: 0 },
  ];
  costFields = [
    { key: 'risk_free_rate', label: 'Risk-free rate (fraction)' },
    { key: 'assumed_iv', label: 'Fallback implied volatility' },
    { key: 'brokerage_per_order', label: 'Brokerage per order ₹' },
    { key: 'stt_pct', label: 'STT rate (fraction)' },
    { key: 'exchange_charges_pct', label: 'Exchange rate (fraction)' },
    { key: 'gst_pct', label: 'GST rate (fraction)' },
    { key: 'sebi_charges_pct', label: 'SEBI rate (fraction)' },
    { key: 'stamp_duty_pct', label: 'Stamp duty (fraction)' },
    { key: 'slippage_pct', label: 'Slippage per side (fraction)' },
  ];
  ngOnInit() {
    void this.load();
  }
  async load() {
    this.busy.set(true);
    try {
      this.form = await this.api.get('/settings?mode=' + this.mode);
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
  async save() {
    this.busy.set(true);
    this.error.set('');
    try {
      const data: Record<string, any> = {
        mode: this.mode,
        trade_direction: this.form['trade_direction'] || 'BOTH',
      };
      for (const f of [...this.strategyFields, ...this.riskFields, ...this.costFields])
        data[f.key] = this.form[f.key];
      await this.api.post('/settings', data);
      this.feedback.notify('Settings saved for ' + this.mode + '.');
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
}
