import { SymbolSearch } from '../shared/symbol-search';
import { Component, inject, signal, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Auth, message } from '../core/api';
import { Feedback } from '../core/feedback';
import { Desk } from '../core/desk';
import { Strategy } from '../core/models';
import { Heading, ErrorBox } from '../shared/ui';
import { Icon } from '../shared/icon';
import { RouterLink } from '@angular/router';
@Component({
  selector: 'ax-strategies',
  imports: [FormsModule, Heading, ErrorBox, Icon, RouterLink, SymbolSearch],
  template: `<ax-heading
      title="Turn an idea into a strategy."
      subtitle="Define the rules. Set your limits. Trade with intention."
      eyebrow="STRATEGY STUDIO"
    >
      @if (auth.user()?.is_admin) {
        <button class="btn primary" (click)="edit()"><ax-icon name="plus" />Create strategy</button>
      }</ax-heading
    ><ax-error [text]="error()" />
    <div class="strategy-intro">
      <span class="step-number">01</span><span>Build your strategy</span
      ><span class="step-line"></span><span class="step-number">02</span
      ><span>Test your assumptions</span><span class="step-line"></span
      ><span class="step-number">03</span><span>Run with control</span>
    </div>
    <div class="strategy-grid">
      @for (s of strategies(); track s.id) {
        <article class="panel strategy-card">
          <div class="panel-heading">
            <span class="strategy-icon"
              ><ax-icon [name]="s.instrument_type === 'OPTIONS' ? 'bolt' : 'markets'" /></span
            ><span class="pill" [class.success]="s.is_running || s.is_active">{{
              s.is_running ? 'RUNNING' : s.is_active ? 'SELECTED' : 'READY'
            }}</span>
          </div>
          <div class="eyebrow">
            {{ s.instrument_type }} · {{ s.engine_type.replaceAll('_', ' ') }}
          </div>
          <h2>{{ s.name }}</h2>
          <p class="muted">{{ s.description || 'Your rules. Your trading plan.' }}</p>
          <div class="strategy-metrics">
            <div>
              <span>Instrument</span><strong>{{ s.symbol }}</strong>
            </div>
            <div>
              <span>Direction</span><strong>{{ s.rules['direction'] || 'BOTH' }}</strong>
            </div>
            <div>
              <span>{{ s.instrument_type === 'OPTIONS' ? 'Target points' : 'Target %' }}</span
              ><strong>{{
                s.rules['target_pts'] ||
                  s.rules['tgt_pct'] ||
                  s.rules['exit']?.take_profit?.value ||
                  '—'
              }}</strong>
            </div>
          </div>
          @if (auth.user()?.is_admin) {
            <div class="strategy-actions">
              <button class="btn small" [disabled]="busy()" (click)="edit(s)">Edit</button
              ><button
                class="btn small danger-ghost"
                [disabled]="busy() || s.is_running"
                (click)="remove(s)"
              >
                Delete
              </button>
              @if (s.instrument_type === 'OPTIONS') {
                <button class="btn small primary" [disabled]="busy()" (click)="activate(s)">
                  Use on overview
                </button>
              } @else {
                <button class="btn small primary" [disabled]="busy()" (click)="run(s)">
                  {{ s.is_running ? 'Stop' : 'Run strategy' }}
                </button>
              }
            </div>
            @if (s.instrument_type === 'EQUITY' && s.is_running) {
              <div class="button-row">
                <button class="btn small" (click)="manual(s, 'enter')">Manual entry</button
                ><button class="btn small" (click)="manual(s, 'exit')">Exit position</button>
              </div>
            }
          }
        </article>
      } @empty {
        <div class="panel empty-state">
          <ax-icon name="strategies" />
          <h2>{{ loading() ? 'Loading strategies…' : 'Start with an idea.' }}</h2>
          <p>Create an options ORB or equity strategy to get started.</p>
        </div>
      }
    </div>
    <div class="help-strip">
      <ax-icon name="backtests" />
      <div>
        <strong>Confidence starts with a test.</strong>
        <p>Explore the historical options ORB runner in Backtest Lab.</p>
      </div>
      <a class="btn" routerLink="/backtests">Open the lab →</a>
    </div>
    @if (editor()) {
      <div class="modal-backdrop">
        <section
          class="modal wide"
          role="dialog"
          aria-modal="true"
          aria-labelledby="strategy-title"
        >
          <form (ngSubmit)="save()">
            <div class="panel-heading">
              <div>
                <div class="eyebrow">STRATEGY CONFIGURATION</div>
                <h2 id="strategy-title">{{ editingId ? 'Edit strategy' : 'Create a strategy' }}</h2>
              </div>
              <button
                type="button"
                class="icon-btn"
                aria-label="Close editor"
                (click)="editor.set(false)"
              >
                <ax-icon name="close" />
              </button>
            </div>
            <ax-error [text]="formError()" />
            <div class="form-grid">
              <label class="span-two"
                >Strategy name<input
                  name="name"
                  [(ngModel)]="form.name"
                  required
                  maxlength="120"
                  placeholder="e.g. Morning momentum" /></label
              ><label
                >Instrument type<select
                  name="type"
                  [(ngModel)]="form.instrument_type"
                  [disabled]="!!editingId"
                  (ngModelChange)="typeChanged()"
                >
                  <option>OPTIONS</option>
                  <option>EQUITY</option>
                </select></label
              ><label
                >Engine<select name="engine" [(ngModel)]="form.engine_type">
                  @if (form.instrument_type === 'OPTIONS') {
                    <option value="ORB">Options ORB</option>
                  } @else {
                    <option value="EQUITY_ORB">Equity ORB</option>
                    <option value="EMA_CROSS">EMA crossover</option>
                  }
                </select></label
              ><label
                >Symbol<ax-symbol-search
                  label="Symbol"
                  [(value)]="form.symbol"
                  [equity]="form.instrument_type === 'EQUITY'"
                  [readonly]="form.instrument_type === 'OPTIONS'" /></label
              ><label
                >Direction<select name="direction" [(ngModel)]="form.rules.direction">
                  @if (form.instrument_type === 'OPTIONS') {
                    <option>BOTH</option>
                    <option>CALL</option>
                    <option>PUT</option>
                  } @else {
                    <option>LONG</option>
                    <option>SHORT</option>
                  }
                </select></label
              >
              @if (form.instrument_type === 'OPTIONS') {
                <label
                  >Target points<input
                    name="target"
                    type="number"
                    min="1"
                    [(ngModel)]="form.rules.target_pts"
                    required /></label
                ><label
                  >Fibonacci trail<input
                    name="trail"
                    type="number"
                    min="0"
                    max="1"
                    step="0.01"
                    [(ngModel)]="form.rules.fib_trail"
                    required /></label
                ><label
                  >Lots<input
                    name="lots"
                    type="number"
                    min="1"
                    [(ngModel)]="form.rules.lots"
                    required /></label
                ><label
                  >Opening range end<input
                    name="or_end"
                    type="time"
                    [(ngModel)]="form.rules.or_end_time"
                    required
                /></label>
              } @else {
                <label
                  >Quantity<input
                    name="qty"
                    type="number"
                    min="1"
                    [(ngModel)]="form.rules.qty"
                    required /></label
                ><label
                  >Stop loss %<input
                    name="sl"
                    type="number"
                    min="0.01"
                    max="100"
                    step="0.01"
                    [(ngModel)]="form.rules.sl_pct"
                    required /></label
                ><label
                  >Target %<input
                    name="tgt"
                    type="number"
                    min="0.01"
                    max="100"
                    step="0.01"
                    [(ngModel)]="form.rules.tgt_pct"
                    required /></label
                ><label
                  >Square-off time<input
                    name="eod"
                    type="time"
                    [(ngModel)]="form.rules.eod_exit"
                    required
                /></label>
                @if (form.engine_type === 'EMA_CROSS') {
                  <label
                    >Fast EMA<input
                      name="fast"
                      type="number"
                      min="1"
                      [(ngModel)]="form.rules.ema_fast"
                      required /></label
                  ><label
                    >Slow EMA<input
                      name="slow"
                      type="number"
                      min="2"
                      [(ngModel)]="form.rules.ema_slow"
                      required
                  /></label>
                }
              }
              <label
                >Daily loss limit ₹<input
                  name="loss"
                  type="number"
                  min="0"
                  [(ngModel)]="form.rules.max_daily_loss"
                /><small>0 disables the limit</small></label
              ><label class="span-two"
                >Description<textarea
                  name="description"
                  [(ngModel)]="form.description"
                  maxlength="500"
                  rows="2"
                ></textarea>
              </label>
            </div>
            <div class="modal-actions">
              <button class="btn" type="button" (click)="editor.set(false)">Cancel</button
              ><button class="btn primary" [disabled]="busy()">
                {{ busy() ? 'Saving…' : 'Save strategy' }}
              </button>
            </div>
          </form>
        </section>
      </div>
    }
    @if (runTarget()) {
      <div class="modal-backdrop">
        <section class="modal" role="dialog" aria-modal="true" aria-labelledby="run-title">
          <h2 id="run-title">Run {{ runTarget()?.name }}</h2>
          <p class="muted">Choose how to execute this strategy.</p>
          <label
            >Execution mode<select [(ngModel)]="runMode">
              <option value="paper">Paper · simulated orders</option>
              <option value="live">Live · real broker orders</option>
            </select></label
          >
          <div class="modal-actions">
            <button class="btn" (click)="runTarget.set(null)">Cancel</button
            ><button class="btn primary" [disabled]="busy()" (click)="confirmRun()">
              Start strategy
            </button>
          </div>
        </section>
      </div>
    }`,
})
export class Strategies implements OnInit {
  api = inject(Api);
  auth = inject(Auth);
  desk = inject(Desk);
  feedback = inject(Feedback);
  strategies = signal<Strategy[]>([]);
  error = signal('');
  loading = signal(true);
  busy = signal(false);
  editor = signal(false);
  formError = signal('');
  editingId: number | null = null;
  form: any = {};
  runTarget = signal<Strategy | null>(null);
  runMode = 'paper';
  ngOnInit() {
    void this.load();
  }
  async load() {
    try {
      const d = await this.api.get('/strategies');
      this.strategies.set(d.strategies);
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.loading.set(false);
    }
  }
  async edit(s?: Strategy) {
    let settings: any;
    try {
      settings = await this.api.get('/settings?mode=PAPER');
    } catch (e) {
      this.error.set(message(e));
      return;
    }
    this.editingId = s?.id || null;
    const defaults = {
      direction: s?.instrument_type === 'EQUITY' ? 'LONG' : 'BOTH',
      target_pts: settings.target_pts,
      fib_trail: settings.fib_trail,
      lots: settings.qty_multiplier,
      or_end_time: settings.or_end_time,
      qty: 1,
      sl_pct: 1,
      tgt_pct: 2,
      eod_exit: '15:15',
      ema_fast: 9,
      ema_slow: 21,
      max_daily_loss: settings.max_daily_loss,
    };
    this.form = s
      ? { ...s, rules: { ...defaults, ...s.rules } }
      : {
          name: '',
          description: '',
          instrument_type: 'OPTIONS',
          symbol: 'NIFTY 50',
          engine_type: 'ORB',
          rules: defaults,
        };
    this.formError.set('');
    this.editor.set(true);
  }
  typeChanged() {
    const eq = this.form.instrument_type === 'EQUITY';
    this.form.engine_type = eq ? 'EQUITY_ORB' : 'ORB';
    this.form.symbol = eq ? '' : 'NIFTY 50';
    this.form.rules.direction = eq ? 'LONG' : 'BOTH';
  }
  async save() {
    this.busy.set(true);
    this.formError.set('');
    try {
      const payload = { ...this.form };
      if (this.editingId) await this.api.put('/strategies/' + this.editingId, payload);
      else await this.api.post('/strategies', payload);
      this.editor.set(false);
      await this.load();
      this.feedback.notify('Strategy saved.');
    } catch (e) {
      this.formError.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
  async remove(s: Strategy) {
    if (
      !(await this.feedback.confirm(
        'Delete ' + s.name + '?',
        'This removes the strategy. Existing trade history is retained.',
        'Delete strategy',
        true,
      ))
    )
      return;
    await this.perform(() => this.api.delete('/strategies/' + s.id));
  }
  async activate(s: Strategy) {
    await this.perform(() => this.api.post('/active-strategy', { strategy_id: s.id }));
    await this.desk.refresh();
  }
  async run(s: Strategy) {
    if (s.is_running) {
      if (
        await this.feedback.confirm(
          'Stop ' + s.name + '?',
          'Close any open position first.',
          'Stop strategy',
        )
      )
        await this.perform(() => this.api.post('/strategies/' + s.id + '/stop'));
    } else {
      this.runMode = 'paper';
      this.runTarget.set(s);
    }
  }
  async confirmRun() {
    const s = this.runTarget();
    if (!s) return;
    if (
      this.runMode === 'live' &&
      !(await this.feedback.confirm(
        'Start live execution?',
        'Real orders will be sent to your broker for ' + s.symbol + '.',
        'Start live',
        true,
      ))
    )
      return;
    this.runTarget.set(null);
    await this.perform(() =>
      this.api.post('/strategies/' + s.id + '/start', { mode: this.runMode }),
    );
  }
  async manual(s: Strategy, action: string) {
    if (
      await this.feedback.confirm(
        'Queue ' + action + ' for ' + s.symbol + '?',
        'The running strategy’s execution mode will be used.',
        'Confirm order',
        true,
      )
    )
      await this.perform(() => this.api.post('/strategies/' + s.id + '/manual', { action }));
  }
  async perform(fn: () => Promise<unknown>) {
    this.busy.set(true);
    this.error.set('');
    try {
      await fn();
      await this.load();
      this.feedback.notify('Strategy updated.');
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
}
