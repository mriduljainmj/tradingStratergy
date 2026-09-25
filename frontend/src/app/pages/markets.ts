import { SymbolSearch } from '../shared/symbol-search';
import { Component, inject, signal, computed, OnInit, OnDestroy } from '@angular/core';
import { CurrencyPipe, DecimalPipe, KeyValuePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Api, message, query } from '../core/api';
import { Feedback } from '../core/feedback';
import { Stock } from '../core/models';
import { Heading, ErrorBox } from '../shared/ui';
import { Icon } from '../shared/icon';
@Component({
  selector: 'ax-markets',
  imports: [
    SymbolSearch,
    Heading,
    ErrorBox,
    Icon,
    FormsModule,
    CurrencyPipe,
    DecimalPipe,
    KeyValuePipe,
    RouterLink,
  ],
  template: `<ax-heading
      title="Find your next opportunity."
      subtitle="Explore the market. Keep the right names in sight."
      eyebrow="MARKET EXPLORER"
      ><button class="btn" [disabled]="loading()" (click)="load()">
        <ax-icon name="refresh" />Refresh</button
      ><button class="btn primary" [disabled]="scanBusy()" (click)="scan()">
        <ax-icon name="bolt" />{{ scanBusy() ? 'Scanning…' : 'Momentum scan' }}
      </button></ax-heading
    ><ax-error [text]="error()" />
    @if (scanStatus()) {
      <div class="alert info">{{ scanStatus() }}</div>
    }
    <div class="panel">
      <div class="toolbar">
        <div class="search-input">
          <ax-icon name="search" /><ax-symbol-search
            label="Search stocks"
            [value]="search()"
            (valueChange)="search.set($event); reset()"
            [equity]="true"
            [required]="false"
          />
        </div>
        <select
          aria-label="Sector"
          [ngModel]="sector()"
          (ngModelChange)="sector.set($event); reset()"
        >
          <option value="">All sectors</option>
          @for (s of sectors(); track s) {
            <option>{{ s }}</option>
          }
        </select>
        <div class="segmented">
          <button [class.active]="!watchOnly()" (click)="watchOnly.set(false); reset()">
            All stocks</button
          ><button [class.active]="watchOnly()" (click)="watchOnly.set(true); reset()">
            Watchlist <span class="count">{{ watch().length }}</span>
          </button>
        </div>
      </div>
      <div class="toolbar secondary">
        <span
          >{{ filtered().length | number }} instruments
          <span class="muted"
            >· {{ live() ? 'Broker quotes' : 'Quotes unavailable · connect Kite' }}</span
          ></span
        >
        <div class="inline-field">
          <label for="watch-list">Watchlist</label
          ><input
            id="watch-list"
            [(ngModel)]="listName"
            placeholder="My Watchlist"
            maxlength="100"
          />
        </div>
        <select
          aria-label="Sort stocks"
          [ngModel]="sort()"
          (ngModelChange)="sort.set($event); reset()"
        >
          <option value="symbol">Symbol A–Z</option>
          <option value="change">Change: high to low</option>
        </select>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th></th>
              <th>Instrument</th>
              <th>Sector</th>
              <th class="numeric">Last price</th>
              <th class="numeric">Change</th>
              <th class="numeric">Volume</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            @for (s of visible(); track s.symbol) {
              <tr>
                <td>
                  <button
                    class="icon-btn star"
                    [class.starred]="isWatched(s.symbol)"
                    [attr.aria-label]="(isWatched(s.symbol) ? 'Remove ' : 'Watch ') + s.symbol"
                    [disabled]="pending() === s.symbol"
                    (click)="toggle(s)"
                  >
                    <ax-icon name="star" />
                  </button>
                </td>
                <td>
                  <div class="stock-name">
                    <span class="stock-monogram">{{ s.symbol.slice(0, 2) }}</span
                    ><span
                      ><strong>{{ s.symbol }}</strong
                      ><small>{{ s.name || s.company_name }}</small></span
                    >
                  </div>
                </td>
                <td>
                  <span class="pill subtle">{{ s.sector || 'Other' }}</span>
                </td>
                <td class="numeric">{{ s.ltp == null ? '—' : (s.ltp | currency: 'INR') }}</td>
                <td
                  class="numeric"
                  [class.positive]="(s.change_pct || 0) > 0"
                  [class.negative]="(s.change_pct || 0) < 0"
                >
                  {{ s.change_pct == null ? '—' : (s.change_pct | number: '1.2-2') + '%' }}
                </td>
                <td class="numeric">{{ s.volume == null ? '—' : (s.volume | number) }}</td>
                <td><button class="btn small" (click)="details(s)">Technicals</button></td>
              </tr>
            } @empty {
              <tr>
                <td colspan="7" class="empty-cell">
                  {{
                    loading()
                      ? 'Loading instruments…'
                      : 'No matching instruments. Try a different search or add to your watchlist.'
                  }}
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span>Page {{ page() }} of {{ pages() }}</span>
        <div class="button-row">
          <button class="btn small" [disabled]="page() === 1" (click)="move(-1)">← Previous</button
          ><button class="btn small" [disabled]="page() >= pages()" (click)="move(1)">
            Next →
          </button>
        </div>
      </div>
    </div>
    @if (selected()) {
      <div class="modal-backdrop" (click)="selected.set(null)">
        <section
          class="modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="technical-title"
          (click)="$event.stopPropagation()"
        >
          <div class="panel-heading">
            <h2 id="technical-title">{{ selected()?.symbol }} · Technicals</h2>
            <button class="icon-btn" aria-label="Close technicals" (click)="selected.set(null)">
              <ax-icon name="close" />
            </button>
          </div>
          @if (technicalError()) {
            <ax-error [text]="technicalError()" />
          } @else if (!technical()) {
            <p>Loading technical indicators…</p>
          } @else {
            <div class="details-grid">
              @for (row of technical() | keyvalue; track row.key) {
                @if (isMetric(row.value)) {
                  <div class="detail-row">
                    <span>{{ row.key }}</span
                    ><strong>{{ row.value | number: '1.0-2' }}</strong>
                  </div>
                }
              }
            </div>
          }
          <a
            class="btn primary full"
            routerLink="/charts"
            [queryParams]="{ symbol: selected()?.symbol, view: 'single' }"
            (click)="selected.set(null)"
            >Open chart<ax-icon name="arrow"
          /></a>
        </section>
      </div>
    }`,
})
export class Markets implements OnInit, OnDestroy {
  api = inject(Api);
  feedback = inject(Feedback);
  stocks = signal<Stock[]>([]);
  watch = signal<Stock[]>([]);
  search = signal('');
  sector = signal('');
  watchOnly = signal(false);
  sort = signal('symbol');
  page = signal(1);
  loading = signal(false);
  live = signal(false);
  error = signal('');
  pending = signal('');
  listName = 'My Watchlist';
  selected = signal<Stock | null>(null);
  technical = signal<any>(null);
  technicalError = signal('');
  scanBusy = signal(false);
  scanStatus = signal('');
  private scanTimer: any;
  private quoteRequest = 0;
  sectors = computed(() =>
    [
      ...new Set(
        this.stocks()
          .map((s) => s.sector)
          .filter(Boolean),
      ),
    ].sort(),
  );
  filtered = computed(() => {
    const q = this.search().toLowerCase();
    return (this.watchOnly() ? this.watch() : this.stocks())
      .filter(
        (s) =>
          (!this.sector() || s.sector === this.sector()) &&
          (!q || (s.symbol + ' ' + s.name + ' ' + s.company_name).toLowerCase().includes(q)),
      )
      .sort((a, b) =>
        this.sort() === 'change'
          ? (b.change_pct ?? -Infinity) - (a.change_pct ?? -Infinity)
          : a.symbol.localeCompare(b.symbol),
      );
  });
  pages = computed(() => Math.max(1, Math.ceil(this.filtered().length / 25)));
  visible = computed(() => this.filtered().slice((this.page() - 1) * 25, this.page() * 25));
  ngOnInit() {
    void this.load();
  }
  private destroyed = false;
  ngOnDestroy() {
    this.destroyed = true;
    clearTimeout(this.scanTimer);
  }
  async load() {
    this.loading.set(true);
    this.error.set('');
    try {
      const [s, w] = await Promise.all([
        this.api.get('/screener/all-instruments'),
        this.api.get('/screener/watchlist'),
      ]);
      this.stocks.set(s.data);
      this.watch.set(w.data);
      await this.quotes();
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.loading.set(false);
    }
  }
  reset() {
    this.page.set(1);
    void this.quotes();
  }
  move(delta: number) {
    this.page.update((v) => v + delta);
    void this.quotes();
  }
  async quotes() {
    const id = ++this.quoteRequest;
    const symbols = this.visible()
      .map((s) => s.symbol)
      .join(',');
    if (!symbols) return;
    try {
      const d = await this.api.get('/screener/batch-quotes' + query({ symbols }));
      if (id !== this.quoteRequest) return;
      this.live.set(d.live);
      const patch = (rows: Stock[]) => rows.map((s) => ({ ...s, ...d.data[s.symbol] }));
      this.stocks.update(patch);
      this.watch.update(patch);
    } catch (e) {
      this.error.set(message(e));
    }
  }
  isWatched(symbol: string) {
    return this.watch().some((s) => s.symbol === symbol);
  }
  async toggle(s: Stock) {
    this.pending.set(s.symbol);
    try {
      if (this.isWatched(s.symbol)) {
        await this.api.delete('/screener/watchlist/' + encodeURIComponent(s.symbol));
        this.watch.update((rows) => rows.filter((r) => r.symbol !== s.symbol));
      } else {
        await this.api.post('/screener/watchlist', {
          symbol: s.symbol,
          list: this.listName || 'My Watchlist',
        });
        this.watch.update((rows) => [...rows, s]);
      }
      this.feedback.notify('Watchlist updated.');
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.pending.set('');
    }
  }
  async details(s: Stock) {
    this.selected.set(s);
    this.technical.set(null);
    this.technicalError.set('');
    try {
      const d = await this.api.get('/screener/technicals' + query({ symbol: s.symbol }));
      if (this.selected()?.symbol === s.symbol) this.technical.set(d);
    } catch (e) {
      this.technicalError.set(message(e));
    }
  }
  isMetric(value: unknown): value is number {
    return typeof value === 'number';
  }
  async scan() {
    this.scanBusy.set(true);
    try {
      await this.api.post('/screener/momentum-scan');
      await this.pollScan();
    } catch (e) {
      this.error.set(message(e));
      this.scanBusy.set(false);
    }
  }
  async pollScan() {
    try {
      const d = await this.api.get('/screener/momentum-scan');
      if (this.destroyed) return;
      this.scanStatus.set(
        `${d.stage || d.status} · ${d.done}/${d.total} instruments · ${d.qualifiers} qualifiers`,
      );
      if (d.status === 'running') {
        this.scanTimer = setTimeout(() => void this.pollScan(), 3000);
      } else {
        this.scanBusy.set(false);
        if (d.error) this.error.set(d.error);
        if (d.results?.length) {
          this.stocks.set(d.results.map((r: any) => ({ ...r, name: r.name || r.symbol })));
          this.reset();
        }
      }
    } catch (e) {
      this.scanBusy.set(false);
      this.error.set(message(e));
    }
  }
}
