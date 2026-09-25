import { Component, inject, signal, OnInit, OnDestroy, effect, untracked } from '@angular/core';
import { MarketFeed } from '../core/market-feed';
import { mergeChartStream } from '../shared/stream-candles';
import { chartDate } from '../shared/chart-time';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { Api, message, query } from '../core/api';
import { Feedback } from '../core/feedback';
import { Heading, ErrorBox } from '../shared/ui';
import { Icon } from '../shared/icon';
import { formatChartTime } from '../shared/chart-time';
import { SymbolSearch } from '../shared/symbol-search';
import { Chart } from '../shared/chart';
@Component({
  selector: 'ax-charts',
  providers: [MarketFeed],
  imports: [FormsModule, Heading, ErrorBox, Icon, Chart, SymbolSearch],
  template: `<ax-heading
      title="A wider view of the market."
      subtitle="Your instruments, side by side. Every perspective in one place."
      eyebrow="MULTI-CHART WORKSPACE"
      ><div class="segmented">
        @for (n of [1, 2, 4]; track n) {
          <button [class.active]="count() === n" (click)="layout(n)">
            {{ n }} chart{{ n > 1 ? 's' : '' }}
          </button>
        }
      </div>
      <button class="btn primary" (click)="save()">Save workspace</button></ax-heading
    ><ax-error [text]="error()" />
    <div class="panel chart-workspace-controls">
      <label
        >Arrangement<select aria-label="Arrangement" [(ngModel)]="arrangement">
          <option value="grid">Side by side</option>
          <option value="stack">Stacked</option>
        </select></label
      ><label
        >History<select
          aria-label="History range"
          [disabled]="loading()"
          [(ngModel)]="days"
          (ngModelChange)="changeHistory()"
        >
          <option value="all">All available history</option>
          <option [ngValue]="3">Last 3 trading days · maximum detail</option>
          <option [ngValue]="7">Last 7 days</option>
          <option [ngValue]="30">Last 30 days</option>
          <option [ngValue]="90">Last 90 days</option>
        </select></label
      ><span class="muted">Market time · IST (UTC+05:30)</span
      ><button class="btn" [disabled]="loading()" (click)="refreshAll()">Refresh all</button>
      <span class="muted" role="status">{{ feed.status() }}</span>
      @if (focused() !== null) {
        <button class="btn" (click)="focused.set(null)">Back to layout</button>
      }
    </div>
    <div
      class="multi-charts"
      [class.single]="count() === 1 || focused() !== null || arrangement === 'stack'"
    >
      @for (pane of panes().slice(0, count()); track $index; let i = $index) {
        <section class="chart-panel" [hidden]="focused() !== null && focused() !== i">
          <form class="pane-toolbar" (ngSubmit)="load(i, false, true)">
            <ax-symbol-search
              [label]="'Chart ' + (i + 1) + ' symbol'"
              [(value)]="pane.symbol"
              [disabled]="pane.loading"
              (selected)="load(i)"
            /><select
              [attr.aria-label]="'Chart ' + (i + 1) + ' timeframe'"
              [(ngModel)]="pane.interval"
              [name]="'interval' + i"
              [disabled]="pane.loading"
              (ngModelChange)="load(i)"
            >
              <option value="minute">1 min</option>
              <option value="5minute">5 min</option>
              <option value="15minute">15 min</option>
              <option value="60minute">1 hour</option>
              <option value="day">1 day</option>
              <option value="week">1 week</option></select
            ><button class="chart-load" [disabled]="pane.loading" aria-label="Load chart">
              <ax-icon name="refresh" />
            </button>
          </form>
          <div class="pane-actions">
            @if (days === 'all' && pane.loading) {
              <button type="button" (click)="pause(i)">Pause history loading</button>
            }
            @if (days === 'all' && !pane.loading && pane.nextTo) {
              <button type="button" (click)="load(i, true)">Load remaining history</button>
            }
            <button
              type="button"
              [disabled]="pane.loading || (days === 'all' && !pane.complete)"
              (click)="canvas.fit()"
            >
              Fit data</button
            ><button
              type="button"
              [disabled]="pane.loading || (days === 'all' && !pane.complete)"
              (click)="canvas.latest()"
            >
              Latest</button
            ><button
              type="button"
              [attr.aria-label]="'Focus chart ' + (i + 1)"
              (click)="focused.set(focused() === i ? null : i)"
            >
              {{ focused() === i ? 'Restore' : 'Focus' }}
            </button>
          </div>
          <div class="multi-chart-canvas">
            <ax-chart
              #canvas
              [data]="days === 'all' && !pane.complete ? [] : chartData(pane)"
              [loading]="pane.loading"
              [fitUpdates]="false"
              [emptyTitle]="
                pane.loading
                  ? 'Loading ' + pane.symbol + '…'
                  : pane.error
                    ? 'Market data unavailable'
                    : days === 'all' && !pane.complete && pane.nextTo
                      ? 'History loading paused'
                      : 'No candles for this instrument'
              "
              [emptyText]="
                pane.error ||
                (pane.nextTo
                  ? 'Load remaining history to display the complete chart.'
                  : 'Load an instrument to see its chart.')
              "
            />
          </div>
          <div class="pane-footer">
            <span
              >{{
                pane.historySource || (pane.cacheHit ? 'History from cache' : 'History from Kite')
              }}
              · {{ quoteStatus(pane) }}</span
            >
            <span>{{ pane.loadedSymbol || pane.symbol }} · NSE</span
            ><span
              >{{ pane.data.length }} candles
              @if (pane.interval === 'week') {
                · Weekly
                @if (days !== 'all') {
                  · {{ days === 3 ? 90 : days }} days
                }
              } @else if (days === 3) {
                · {{ pane.sessions || 0 }} trading sessions
              }
            </span>
            @if (days === 'all') {
              <span>{{
                pane.loading
                  ? 'Loading older history…'
                  : pane.complete
                    ? 'All available history loaded'
                    : 'Partial history'
              }}</span>
            }
            @if (pane.error && pane.data.length) {
              <span role="alert">{{ pane.error }}</span>
            }
            @if (pane.data.length) {
              <span
                >From: {{ formatTime(pane.data[0].time) }} · Last:
                {{ formatTime(pane.data.at(-1).time) }}</span
              >
            }
          </div>
        </section>
      }
    </div>
    <div class="help-strip">
      <ax-icon name="charts" />
      <p>
        Click a chart to activate pan and zoom. Double-click to unselect, click outside or press
        Escape to scroll the page again. Saved symbols and layouts follow your account across
        browsers. Candles appear after the selected history finishes loading. You can pause and
        resume older history. Three-day detail uses one-minute candles. Weekly candles start on
        Monday; the current week may be incomplete.
      </p>
      <a href="https://www.tradingview.com/" target="_blank" rel="noopener"
        >TradingView Lightweight Charts ↗</a
      >
    </div>`,
})
export class Charts implements OnInit, OnDestroy {
  private mergedHistory = new WeakMap<any[], any>();
  feed = inject(MarketFeed);
  private refreshTimer = setInterval(() => {
    if (!document.hidden)
      for (let i = 0; i < this.count(); i++)
        if (this.focused() === null || this.focused() === i) void this.refreshTail(i);
  }, 60000);
  constructor() {
    effect(() => {
      const tokens = this.panes()
        .slice(0, this.count())
        .filter((pane, i) => (this.focused() === null || this.focused() === i) && pane.token)
        .map((p) => p.token);
      untracked(() => this.feed.configure(tokens));
    });
  }
  chartData(pane: any) {
    const minutes = this.feed.snapshot()?.minute_candles?.[pane.token];
    if (!minutes?.length) return pane.data;
    const previous = this.mergedHistory.get(pane.data);
    if (
      previous &&
      previous.minutes === minutes &&
      previous.interval === pane.interval &&
      previous.fetchedAt === pane.fetchedAt
    )
      return previous.data;
    const data = mergeChartStream(pane.data, minutes, pane.interval, pane.fetchedAt || 0);
    this.mergedHistory.set(pane.data, {
      minutes,
      interval: pane.interval,
      fetchedAt: pane.fetchedAt,
      data,
    });
    return data;
  }
  quoteStatus(pane: any) {
    const snapshot = this.feed.snapshot(),
      quote = snapshot?.quotes?.[pane.token];
    return snapshot?.status === 'connected' &&
      quote &&
      Math.max(Date.now() / 1000, snapshot.server_time) - quote.time < 15
      ? 'Live'
      : 'Waiting for fresh ticks';
  }
  async refreshTail(i: number) {
    const pane = this.panes()[i];
    if (
      !pane ||
      pane.loading ||
      pane.tailLoading ||
      !pane.token ||
      (this.days === 'all' && !pane.complete)
    )
      return;
    const token = pane.token,
      symbol = pane.loadedSymbol,
      interval = pane.interval,
      fetchedAt = Date.now() / 1000;
    this.patch(i, { tailLoading: true });
    try {
      const result = await this.api.get(
        '/chart/history' +
          query({
            symbol,
            interval,
            ...(this.days === 3 && interval !== 'week'
              ? { sessions: 3 }
              : { from: new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10) }),
          }),
      );
      if (
        this.destroyed ||
        this.panes()[i].token !== token ||
        this.panes()[i].interval !== interval ||
        this.panes()[i].loading
      )
        return;
      const rows = new Map<number, any>(
        this.panes()[i].data.map((r: any) => [chartDate(r.time).getTime(), r]),
      );
      for (const row of result.data) rows.set(chartDate(row.time).getTime(), row);
      let data = [...rows.values()].sort(
        (a, b) => chartDate(a.time).getTime() - chartDate(b.time).getTime(),
      );
      if (this.days === 3 && interval !== 'week' && result.data.length)
        data = data.filter(
          (r) => chartDate(r.time).getTime() >= chartDate(result.data[0].time).getTime(),
        );
      this.patch(i, {
        data,
        fetchedAt: result.history_as_of || fetchedAt,
        error: '',
        cacheHit: result.cache_hit,
      });
    } catch (e) {
      if (!this.destroyed && this.panes()[i].token === token)
        this.patch(i, { error: 'Live history reconciliation failed: ' + message(e) });
    } finally {
      if (!this.destroyed) this.patch(i, { tailLoading: false });
    }
  }
  api = inject(Api);
  route = inject(ActivatedRoute);
  feedback = inject(Feedback);
  count = signal(2);
  focused = signal<number | null>(null);
  arrangement = 'grid';
  days: number | string = 'all';
  private cancelled = new Set<number>();
  private destroyed = false;
  ngOnDestroy() {
    this.destroyed = true;
    clearInterval(this.refreshTimer);
    this.feed.stop();
  }
  pause(i: number) {
    this.cancelled.add(i);
  }
  changeHistory() {
    if (this.days === 3)
      this.panes.update((panes) => panes.map((p) => ({ ...p, interval: 'minute', data: [] })));
    else this.panes.update((panes) => panes.map((p) => ({ ...p, data: [] })));
    this.refreshAll(false);
  }
  formatTime = formatChartTime;
  loading() {
    return this.panes().some((p) => p.loading);
  }
  refreshAll(bypass = true) {
    for (let i = 0; i < this.count(); i++) void this.load(i, false, bypass);
  }
  error = signal('');
  panes = signal<any[]>([]);
  async ngOnInit() {
    try {
      const data = await this.api.get('/workspace/charts');
      const saved = data.workspace;
      this.count.set(saved.count);
      this.arrangement = saved.arrangement;
      this.days = saved.days;
      this.panes.set(saved.panes.map((p: any) => ({ ...p, data: [], error: '', loading: false })));
      const symbol = this.route.snapshot.queryParamMap.get('symbol');
      if (symbol) this.patch(0, { symbol });
      this.layout(this.count());
    } catch (e) {
      this.error.set(message(e));
    }
  }
  layout(n: number) {
    this.focused.set(null);
    this.count.set(n);
    for (let i = 0; i < n; i++) if (!this.panes()[i].data.length) void this.load(i);
  }
  async load(i: number, resume = false, bypass = false) {
    const pane = this.panes()[i];
    if (pane.loading) return;
    this.cancelled.delete(i);
    const symbol = pane.symbol.trim().toUpperCase();
    const interval = pane.interval;
    const all = this.days === 'all';
    const days = Number(this.days);
    let cursor: string | null = resume ? pane.nextTo : null;
    let candles: any[] = resume ? pane.data : [];
    let loadedPages = resume ? pane.loadedPages || 0 : 0;
    let cachedPages = resume ? pane.cachedPages || 0 : 0;
    let historyAsOf = resume ? pane.fetchedAt : 0;
    this.patch(i, {
      loading: true,
      error: '',
      data: candles,
      complete: false,
      nextTo: cursor,
      token: resume ? pane.token : null,
    });
    try {
      do {
        const params = all
          ? { range: 'all', batch: 128, ...(cursor ? { to: cursor } : {}) }
          : {
              ...(days === 3 && interval !== 'week' ? { sessions: 3 } : {}),
              from: new Date(
                Date.now() -
                  Math.min(days === 3 ? 90 : days, interval === 'minute' ? 30 : 90) * 86400000,
              )
                .toISOString()
                .slice(0, 10),
            };
        const fetchedAt = cursor ? this.panes()[i].fetchedAt : Date.now() / 1000;
        const d = await this.api.get(
          '/chart/history' +
            query({ symbol, interval, ...params, ...(bypass && !cursor ? { refresh: 1 } : {}) }),
        );
        if (this.destroyed) return;
        if (!cursor) historyAsOf = d.history_as_of || fetchedAt;
        loadedPages += d.pages_loaded || 1;
        cachedPages += d.cached_pages ?? (d.cache_hit ? 1 : 0);
        candles = [...d.data, ...candles];
        cursor = d.next_to || null;
        this.patch(i, {
          data: candles,
          token: d.token,
          cacheHit: cachedPages === loadedPages,
          loadedPages,
          cachedPages,
          historySource:
            cachedPages === loadedPages
              ? 'History from cache'
              : cachedPages
                ? 'History from Kite + cache'
                : 'History from Kite',
          fetchedAt: historyAsOf,
          loadedSymbol: symbol,
          sessions: d.session_dates?.length,
          nextTo: cursor,
          complete: all && !cursor,
        });
      } while (all && cursor && !this.cancelled.has(i) && !this.destroyed);
    } catch (e) {
      this.patch(i, { error: message(e) });
    } finally {
      this.patch(i, { loading: false });
    }
  }
  patch(i: number, values: any) {
    this.panes.update((p) => p.map((x, j) => (i === j ? { ...x, ...values } : x)));
  }
  async save() {
    try {
      await this.api.put('/workspace/charts', {
        count: this.count(),
        arrangement: this.arrangement,
        days: this.days,
        panes: this.panes().map((p) => ({ symbol: p.symbol, interval: p.interval })),
      });
      this.feedback.notify('Chart workspace saved to your account.');
    } catch (e) {
      this.error.set(message(e));
    }
  }
}
