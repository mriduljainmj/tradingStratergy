import { Component, inject, signal, OnInit, OnDestroy } from '@angular/core';
import { CurrencyPipe, DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { Api, Auth, message, query } from '../core/api';
import { Desk } from '../core/desk';
import { Heading, ErrorBox } from '../shared/ui';

interface Instrument {
  tradingsymbol: string;
  name: string;
  exchange: string;
  tick_size: number;
  lot_size: number;
}
interface Ticket {
  exchange: string;
  tradingsymbol: string;
  transaction_type: string;
  product: string;
  variety: string;
  order_type: string;
  quantity: number;
  price: number;
  trigger_price: number;
  validity: string;
  disclosed_quantity: number;
  market_protection: number;
}
interface BrokerOrder extends Ticket {
  order_id: string;
  status: string;
  status_message: string;
  filled_quantity: number;
  pending_quantity: number;
  average_price: number;
  order_timestamp: string;
}
interface Review {
  review_token: string;
  order: Ticket;
  action: string;
  last_price: number | null;
  quote_time: string;
  margin: number | null;
  filled_quantity: number;
}
@Component({
  selector: 'ax-equity-orders',
  imports: [Heading, ErrorBox, FormsModule, CurrencyPipe, DatePipe, RouterLink],
  template: `
    <ax-heading
      title="Equity trading."
      eyebrow="KITE ACCOUNT"
      subtitle="Buy shares, sell holdings and manage your cash-equity orders."
    >
      <a routerLink="/portfolio" class="btn">View holdings</a>
    </ax-heading>
    <ax-error [text]="error()" />
    @if (!canTrade()) {
      <p class="notice">
        Real orders require an administrator account, a connected Kite session and Live mode.
        <a routerLink="/overview">Open trading controls</a>. Stop automated strategies before manual
        trading.
      </p>
    }
    <div class="trading-grid">
      <section class="panel ticket" aria-label="Equity order ticket">
        <div class="panel-heading">
          <h2>{{ editing() ? 'Modify order' : 'New equity order' }}</h2>
          <span class="live-label">REAL MONEY</span>
        </div>
        @if (editing()) {
          <p class="muted">Order {{ editing() }} · quantity is the total, including any fills.</p>
        }
        <fieldset [disabled]="busy() || !!review()" class="ticket-fields">
          <div class="two">
            <label
              >Exchange<select
                aria-label="Exchange"
                [(ngModel)]="ticket.exchange"
                (ngModelChange)="clearSymbol()"
                [disabled]="!!editing()"
              >
                <option>NSE</option>
                <option>BSE</option>
              </select></label
            >
            <label
              >Side<select
                aria-label="Side"
                [(ngModel)]="ticket.transaction_type"
                [disabled]="!!editing()"
              >
                <option value="BUY">Buy</option>
                <option value="SELL">Sell</option>
              </select></label
            >
          </div>
          <div class="search-wrap">
            <label
              >Stock or company<input
                [(ngModel)]="searchText"
                (ngModelChange)="search()"
                [disabled]="!!editing()"
                placeholder="Search stocks…"
                autocomplete="off"
                aria-controls="equity-results"
            /></label>
            @if (searching()) {
              <small role="status">Searching instruments…</small>
            }
            @if (results().length) {
              <div class="suggestions" id="equity-results" aria-label="Matching stocks">
                @for (item of results(); track item.tradingsymbol) {
                  <button type="button" (click)="choose(item)">
                    <strong>{{ item.tradingsymbol }}</strong
                    ><small>{{ item.name }} · {{ item.exchange }}</small>
                  </button>
                }
              </div>
            }
          </div>
          @if (instrument(); as stock) {
            <small class="muted"
              >{{ stock.exchange }} · Tick ₹{{ stock.tick_size }} · Lot {{ stock.lot_size }}</small
            >
          }
          <div class="two">
            <label
              >Product<select
                aria-label="Product"
                [(ngModel)]="ticket.product"
                [disabled]="!!editing()"
              >
                <option value="CNC">Delivery · CNC</option>
                <option value="MIS">Intraday · MIS</option>
              </select></label
            >
            <label
              >Quantity<input
                aria-label="Quantity"
                type="number"
                min="1"
                [step]="instrument()?.lot_size || 1"
                [(ngModel)]="ticket.quantity"
            /></label>
            <label
              >Order type<select
                aria-label="Order type"
                [(ngModel)]="ticket.order_type"
                (ngModelChange)="adjustValidity()"
              >
                <option value="MARKET">Market</option>
                <option value="LIMIT">Limit</option>
                <option value="SL">Stop-loss limit</option>
                <option value="SL-M">Stop-loss market</option>
              </select></label
            >
            <label
              >Session<select
                aria-label="Session"
                [(ngModel)]="ticket.variety"
                (ngModelChange)="adjustValidity()"
                [disabled]="!!editing()"
              >
                <option value="regular">Regular</option>
                <option value="amo">After market · AMO</option>
              </select></label
            >
            @if (ticket.order_type === 'LIMIT' || ticket.order_type === 'SL') {
              <label
                >Limit price (₹)<input
                  aria-label="Limit price (₹)"
                  type="number"
                  min="0.01"
                  [step]="instrument()?.tick_size || 0.01"
                  [(ngModel)]="ticket.price"
              /></label>
            }
            @if (ticket.order_type === 'SL' || ticket.order_type === 'SL-M') {
              <label
                >Trigger price (₹)<input
                  aria-label="Trigger price (₹)"
                  type="number"
                  min="0.01"
                  [step]="instrument()?.tick_size || 0.01"
                  [(ngModel)]="ticket.trigger_price"
              /></label>
            }
          </div>
          <p class="muted explanation">{{ typeHelp() }}</p>
          <details>
            <summary>More order options</summary>
            <div class="two advanced">
              <label
                >Validity<select aria-label="Validity" [(ngModel)]="ticket.validity">
                  <option>DAY</option>
                  @if (
                    ticket.variety === 'regular' && ['MARKET', 'LIMIT'].includes(ticket.order_type)
                  ) {
                    <option>IOC</option>
                  }
                </select></label
              >
              <label
                >Disclosed quantity<input
                  aria-label="Disclosed quantity"
                  type="number"
                  min="0"
                  [max]="ticket.quantity"
                  step="1"
                  [(ngModel)]="ticket.disclosed_quantity"
                /><small>0 shows the full quantity.</small></label
              >
              @if (ticket.order_type === 'MARKET' || ticket.order_type === 'SL-M') {
                <label
                  >Market protection %<input
                    aria-label="Market protection %"
                    type="number"
                    min="-1"
                    max="100"
                    step="0.01"
                    [(ngModel)]="ticket.market_protection"
                  /><small>−1 automatic, or 0.01–100%.</small></label
                >
              }
            </div>
          </details>
        </fieldset>
        @if (ticket.transaction_type === 'SELL' && ticket.product === 'CNC') {
          <p class="muted">
            Selling delivery shares may require CDSL authorisation.
            <a href="https://kite.zerodha.com/holdings" target="_blank" rel="noopener noreferrer"
              >Authorise holdings in Kite ↗</a
            >, then return here. Pledged and MTF holdings may require separate handling in Kite.
          </p>
        }
        <div class="button-row">
          <button
            class="btn primary"
            [disabled]="
              busy() ||
              !!review() ||
              !ticket.tradingsymbol ||
              !canTrade() ||
              unresolved().length > 0
            "
            (click)="prepare()"
          >
            {{
              busy()
                ? 'Checking with Kite…'
                : editing()
                  ? 'Review changes'
                  : 'Review ' + ticket.transaction_type.toLowerCase() + ' order'
            }}
          </button>
          @if (editing()) {
            <button class="btn" [disabled]="busy()" (click)="reset()">New order</button>
          }
        </div>
      </section>
      <section class="panel guide">
        <h2>Your execution desk</h2>
        <p>
          Review the exact symbol, side, quantity and prices before every submission. These orders
          go to your connected Kite account.
        </p>
        <div class="step">
          <b>1</b>
          <div>
            <strong>Choose your stock</strong>
            <p>Search NSE or BSE, or start a sale from your holdings.</p>
          </div>
        </div>
        <div class="step">
          <b>2</b>
          <div>
            <strong>Review the order</strong>
            <p>
              Check the latest available broker quote and estimated margin. Market orders can fill
              at a different price.
            </p>
          </div>
        </div>
        <div class="step">
          <b>3</b>
          <div>
            <strong>Follow the fills</strong>
            <p>
              Accepted does not mean executed. The order book shows fills, pending quantities and
              rejection reasons.
            </p>
          </div>
        </div>
        <p class="muted">
          Regular and AMO · CNC and MIS · DAY and IOC. GTT, iceberg, cover orders and MTF are not
          included in this ticket. Broker restrictions and market sessions still apply.
        </p>
      </section>
    </div>
    @if (result()) {
      <p class="notice" role="status">{{ result() }}</p>
    }
    @for (item of unresolved(); track item.request_id) {
      <p class="notice negative" role="alert">
        {{ item.order.tradingsymbol }} · {{ item.action }} · {{ item.message }} Reference:
        {{ item.request_id }}. New submissions are blocked until the broker outcome is reconciled.
        <a routerLink="/profile">Review in Profile</a>.
      </p>
    }
    <section class="panel order-book">
      <div class="panel-heading">
        <h2>Today's equity orders</h2>
        <button class="btn" [disabled]="refreshing()" (click)="refresh()">
          {{ refreshing() ? 'Refreshing…' : 'Refresh orders' }}
        </button>
      </div>
      <ax-error [text]="bookError()" />
      <p class="muted">
        All CNC/MIS equity orders in your Kite account. Refreshes every 10 seconds while this page
        is open.
        @if (updated()) {
          <span>Updated {{ updated() | date: 'HH:mm:ss' : '+0530' }} IST.</span>
        }
      </p>
      <div class="book-scroll">
        <table>
          <thead>
            <tr>
              <th>Stock / order</th>
              <th>Side / product</th>
              <th>Type / price</th>
              <th>Filled / total</th>
              <th>Average fill</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            @for (o of orders(); track o.order_id) {
              <tr>
                <td>
                  <strong>{{ o.tradingsymbol }}</strong
                  ><small>{{ o.exchange }} · {{ o.order_id }}</small>
                </td>
                <td [class.negative]="o.transaction_type === 'SELL'">
                  {{ o.transaction_type }}<small>{{ o.product }} · {{ o.variety }}</small>
                </td>
                <td>
                  {{ o.order_type }} · {{ o.price ? (o.price | currency: 'INR') : 'Market' }}
                  @if (o.trigger_price) {
                    <small>Trigger {{ o.trigger_price | currency: 'INR' }}</small>
                  }
                </td>
                <td>
                  {{ o.filled_quantity }} / {{ o.quantity
                  }}<small>Pending {{ o.pending_quantity }}</small>
                </td>
                <td>{{ o.average_price ? (o.average_price | currency: 'INR') : '—' }}</td>
                <td>
                  <strong>{{ o.status }}</strong>
                  @if (o.status_message) {
                    <small class="rejection">{{ o.status_message }}</small>
                  }
                </td>
                <td>
                  @if (editable(o)) {
                    <div class="button-row">
                      <button
                        class="btn"
                        [disabled]="busy() || !!review() || !canTrade() || unresolved().length > 0"
                        (click)="edit(o)"
                      >
                        Modify</button
                      ><button
                        class="btn"
                        [disabled]="busy() || !!review() || !canTrade() || unresolved().length > 0"
                        (click)="prepare('cancel', o.order_id)"
                      >
                        Cancel order
                      </button>
                    </div>
                  }
                </td>
              </tr>
            } @empty {
              <tr>
                <td colspan="7">
                  {{
                    refreshing()
                      ? 'Loading orders…'
                      : bookError()
                        ? 'Order book unavailable.'
                        : 'No equity orders today.'
                  }}
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
    </section>
    @if (review(); as r) {
      <div class="review-backdrop" (click)="dismissReview()">
        <section
          class="panel review"
          role="dialog"
          aria-modal="true"
          aria-labelledby="review-title"
          (click)="$event.stopPropagation()"
          (keydown.escape)="dismissReview()"
          (keydown.tab)="trapFocus($event)"
        >
          <ax-error [text]="error()" />
          <h2 id="review-title">
            {{
              r.action === 'cancel'
                ? 'Cancel this order?'
                : r.action === 'modify'
                  ? 'Review order changes'
                  : 'Review live order'
            }}
          </h2>
          <p class="live-label">REAL MONEY · {{ r.order.exchange }}:{{ r.order.tradingsymbol }}</p>
          <dl>
            <dt>Action</dt>
            <dd>
              {{ r.action === 'cancel' ? 'Cancel unfilled quantity' : r.order.transaction_type }}
            </dd>
            <dt>Total quantity</dt>
            <dd>{{ r.order.quantity }}</dd>
            <dt>Product / session</dt>
            <dd>{{ r.order.product }} / {{ r.order.variety }}</dd>
            <dt>Order type</dt>
            <dd>{{ r.order.order_type }}</dd>
            @if (r.order.price) {
              <dt>Limit price</dt>
              <dd>{{ r.order.price | currency: 'INR' }}</dd>
            }
            @if (r.order.trigger_price) {
              <dt>Trigger price</dt>
              <dd>{{ r.order.trigger_price | currency: 'INR' }}</dd>
            }
            @if (r.action !== 'cancel') {
              <dt>Validity / disclosed</dt>
              <dd>{{ r.order.validity }} / {{ r.order.disclosed_quantity || 'Full quantity' }}</dd>
              @if (r.order.market_protection) {
                <dt>Market protection</dt>
                <dd>
                  {{
                    r.order.market_protection === -1 ? 'Automatic' : r.order.market_protection + '%'
                  }}
                </dd>
              }
              <dt>Last traded price</dt>
              <dd>{{ r.last_price | currency: 'INR' }}</dd>
              <dt>Quote time</dt>
              <dd>{{ r.quote_time || 'Not supplied by Kite' }}</dd>
              <dt>Estimated margin</dt>
              <dd>
                {{
                  r.margin === null
                    ? 'Broker rechecks on modification'
                    : (r.margin | currency: 'INR')
                }}
              </dd>
            }
            @if (r.filled_quantity) {
              <dt>Already filled</dt>
              <dd>{{ r.filled_quantity }} (cannot be cancelled)</dd>
            }
          </dl>
          <p>Execution and prices are confirmed only by Kite. Review expires in two minutes.</p>
          <div class="button-row">
            <button class="btn" id="review-back" [disabled]="busy()" (click)="dismissReview()">
              Go back</button
            ><button class="btn primary" [disabled]="busy()" (click)="submit()">
              {{
                busy()
                  ? 'Submitting…'
                  : r.action === 'cancel'
                    ? 'Confirm cancellation'
                    : 'Confirm ' + r.order.transaction_type.toLowerCase()
              }}
            </button>
          </div>
        </section>
      </div>
    }
  `,
  styles: `
    .trading-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(260px, 0.8fr);
      gap: 22px;
      margin: 22px 0;
    }
    .ticket,
    .guide,
    .order-book,
    .review {
      padding: 24px;
    }
    .ticket-fields {
      border: 0;
      padding: 0;
      margin: 0;
      min-width: 0;
    }
    .two {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
      margin: 16px 0;
    }
    label {
      display: flex;
      flex-direction: column;
      gap: 7px;
      font-size: 13px;
      font-weight: 600;
      min-width: 0;
    }
    input,
    select {
      width: 100%;
      min-width: 0;
    }
    small {
      display: block;
      font-size: 12px;
      font-weight: 400;
      color: var(--muted);
    }
    .live-label {
      color: var(--negative, #d13f57);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.05em;
    }
    .search-wrap {
      position: relative;
    }
    .suggestions {
      position: absolute;
      top: 100%;
      left: 0;
      right: 0;
      z-index: 5;
      max-height: 280px;
      overflow: auto;
      background: var(--panel, var(--surface));
      border: 1px solid var(--line);
      border-radius: 10px;
      box-shadow: 0 10px 25px #0003;
    }
    .suggestions button {
      display: block;
      width: 100%;
      text-align: left;
      padding: 12px;
      border: 0;
      border-bottom: 1px solid var(--line);
      background: var(--surface);
      color: var(--ink);
    }
    .suggestions button:hover,
    .suggestions button:focus-visible {
      background: var(--line);
    }
    p {
      line-height: 1.6;
    }
    .explanation {
      font-size: 13px;
    }
    summary {
      cursor: pointer;
      padding: 12px 0;
    }
    .notice {
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--surface);
    }
    .step {
      display: flex;
      gap: 14px;
      margin: 24px 0;
    }
    .step b {
      width: 28px;
      height: 28px;
      border: 1px solid var(--line);
      border-radius: 50%;
      text-align: center;
      flex-shrink: 0;
    }
    .step p {
      margin: 5px 0;
      color: var(--muted);
      font-size: 14px;
    }
    .book-scroll {
      overflow: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 900px;
    }
    th,
    td {
      padding: 14px 10px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
    }
    th {
      color: var(--muted);
    }
    .rejection {
      max-width: 260px;
    }
    .review-backdrop {
      position: fixed;
      inset: 0;
      background: #07121dcc;
      z-index: 500;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .review {
      width: min(540px, 100%);
      max-height: 90dvh;
      overflow: auto;
      box-shadow: 0 20px 80px #0005;
    }
    dl {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      font-size: 14px;
    }
    dt {
      color: var(--muted);
    }
    dd {
      margin: 0;
      text-align: right;
      overflow-wrap: anywhere;
    }
    @media (max-width: 900px) {
      .trading-grid {
        grid-template-columns: 1fr;
      }
      .guide {
        display: none;
      }
    }
    @media (max-width: 480px) {
      .ticket,
      .order-book,
      .review {
        padding: 16px;
      }
      .two {
        gap: 10px;
      }
      .panel-heading {
        flex-wrap: wrap;
      }
    }
  `,
})
export class EquityOrders implements OnInit, OnDestroy {
  api = inject(Api);
  auth = inject(Auth);
  desk = inject(Desk);
  route = inject(ActivatedRoute);
  ticket: Ticket = this.empty();
  searchText = '';
  instrument = signal<Instrument | null>(null);
  results = signal<Instrument[]>([]);
  searching = signal(false);
  busy = signal(false);
  refreshing = signal(false);
  error = signal('');
  bookError = signal('');
  result = signal('');
  editing = signal('');
  orders = signal<BrokerOrder[]>([]);
  unresolved = signal<any[]>([]);
  updated = signal('');
  review = signal<Review | null>(null);
  private timer?: ReturnType<typeof setInterval>;
  private searchTimer?: ReturnType<typeof setTimeout>;
  private generation = 0;
  private destroyed = false;
  private originFocus?: HTMLElement;
  empty(): Ticket {
    return {
      exchange: 'NSE',
      tradingsymbol: '',
      transaction_type: 'BUY',
      product: 'CNC',
      variety: 'regular',
      order_type: 'MARKET',
      quantity: 1,
      price: 0,
      trigger_price: 0,
      validity: 'DAY',
      disclosed_quantity: 0,
      market_protection: -1,
    };
  }
  canTrade() {
    return !!this.auth.user()?.is_admin && this.desk.state()?.app_mode === 'LIVE';
  }
  ngOnInit() {
    const q = this.route.snapshot.queryParamMap;
    this.ticket.exchange = q.get('exchange') === 'BSE' ? 'BSE' : 'NSE';
    this.ticket.transaction_type = q.get('side') === 'SELL' ? 'SELL' : 'BUY';
    this.ticket.product = q.get('product') === 'MIS' ? 'MIS' : 'CNC';
    this.ticket.quantity = Math.max(1, Number(q.get('quantity')) || 1);
    if (q.get('symbol')) {
      this.searchText = q.get('symbol')!;
      this.search(true);
    }
    void this.refresh();
    this.timer = setInterval(() => {
      if (!document.hidden) void this.refresh();
    }, 10000);
  }
  ngOnDestroy() {
    this.destroyed = true;
    this.generation++;
    clearInterval(this.timer);
    clearTimeout(this.searchTimer);
  }
  clearSymbol() {
    this.ticket.tradingsymbol = '';
    this.instrument.set(null);
    this.search();
  }
  search(selectExact = false) {
    const generation = ++this.generation;
    clearTimeout(this.searchTimer);
    this.results.set([]);
    this.ticket.tradingsymbol = '';
    this.instrument.set(null);
    if (!this.searchText.trim()) {
      this.searching.set(false);
      return;
    }
    this.searching.set(true);
    this.searchTimer = setTimeout(async () => {
      try {
        const r = await this.api.get(
          '/equity/instruments' + query({ q: this.searchText, exchange: this.ticket.exchange }),
        );
        if (generation !== this.generation || this.destroyed) return;
        this.results.set(r.instruments);
        const exact = r.instruments.find(
          (i: Instrument) => i.tradingsymbol === this.searchText.toUpperCase(),
        );
        if (selectExact && exact) this.choose(exact);
        if (!r.instruments.length)
          this.error.set('No matching equity found. Try another symbol or exchange.');
      } catch (e) {
        if (generation === this.generation) this.error.set(message(e));
      } finally {
        if (generation === this.generation) this.searching.set(false);
      }
    }, 250);
  }
  choose(i: Instrument) {
    this.instrument.set(i);
    this.ticket.tradingsymbol = i.tradingsymbol;
    this.searchText = i.tradingsymbol;
    this.results.set([]);
    this.error.set('');
  }
  adjustValidity() {
    if (this.ticket.variety === 'amo' || !['MARKET', 'LIMIT'].includes(this.ticket.order_type))
      this.ticket.validity = 'DAY';
  }
  typeHelp() {
    return (
      {
        MARKET:
          'Executes at the available market price; the last traded price is not a guaranteed fill.',
        LIMIT: 'Executes at your limit price or better, if a matching order is available.',
        SL: 'Activates a limit order when the trigger is reached. Buy limit ≥ trigger; sell limit ≤ trigger.',
        'SL-M': 'Activates a market order when the trigger is reached.',
      } as Record<string, string>
    )[this.ticket.order_type];
  }
  editable(o: BrokerOrder) {
    return (
      ['OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'].includes(o.status) &&
      ['regular', 'amo'].includes(o.variety)
    );
  }
  edit(o: BrokerOrder) {
    this.reset();
    this.editing.set(o.order_id);
    this.ticket = { ...this.empty(), ...o, market_protection: -1 };
    this.searchText = o.tradingsymbol;
    document.querySelector('.ticket')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  reset() {
    this.editing.set('');
    this.ticket = this.empty();
    this.searchText = '';
    this.instrument.set(null);
    this.results.set([]);
    this.review.set(null);
    this.error.set('');
  }
  async prepare(action = this.editing() ? 'modify' : 'place', orderId = this.editing()) {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    try {
      const r = await this.api.post<Review>('/equity/preview', {
        action,
        order_id: orderId,
        order: this.ticket,
      });
      this.originFocus = document.activeElement as HTMLElement;
      this.review.set(r);
      setTimeout(() => document.getElementById('review-back')?.focus());
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
  dismissReview() {
    if (!this.busy()) {
      this.review.set(null);
      this.originFocus?.focus();
    }
  }
  trapFocus(event: Event) {
    const e = event as KeyboardEvent;
    const nodes = Array.from(
      (e.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('button:not(:disabled)'),
    );
    if (!nodes.length) {
      e.preventDefault();
      return;
    }
    if (e.shiftKey && document.activeElement === nodes[0]) {
      e.preventDefault();
      nodes.at(-1)?.focus();
    } else if (!e.shiftKey && document.activeElement === nodes.at(-1)) {
      e.preventDefault();
      nodes[0].focus();
    }
  }
  async submit() {
    const r = this.review();
    if (!r || this.busy()) return;
    this.busy.set(true);
    try {
      const out = await this.api.post('/equity/execute', { review_token: r.review_token });
      this.result.set(
        `${out.state.toUpperCase()}${out.order_id ? ' · Order ' + out.order_id : ''} · ${out.message}`,
      );
      this.review.set(null);
      this.originFocus?.focus();
      if (out.state === 'submitted' && r.action === 'place') this.ticket.quantity = 1;
    } catch (e) {
      // Keep the SAME signed request: retrying it can only retrieve the original outcome.
      this.error.set(message(e));
      this.result.set(
        'Response unconfirmed. Refresh orders before doing anything else. This review retains the same request reference to prevent duplicate submission.',
      );
    } finally {
      this.busy.set(false);
      await this.refresh();
    }
  }
  async refresh() {
    if (this.refreshing()) return;
    this.refreshing.set(true);
    try {
      const d = await this.api.get('/equity/orders');
      if (!this.destroyed) {
        this.orders.set(d.orders.slice().reverse());
        this.unresolved.set(d.unresolved);
        this.updated.set(d.fetched_at);
        this.bookError.set('');
      }
    } catch (e) {
      if (!this.destroyed) this.bookError.set(message(e));
    } finally {
      this.refreshing.set(false);
    }
  }
}
