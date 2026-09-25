import {
  AfterViewInit,
  Component,
  ElementRef,
  OnChanges,
  OnDestroy,
  ViewChild,
  input,
  signal,
  inject,
} from '@angular/core';
import { formatChartTime, formatChartTick, chartDate } from './chart-time';
import { createChart, IChartApi, ISeriesApi, ColorType } from 'lightweight-charts';
@Component({
  selector: 'ax-chart',
  host: {
    '[class.chart-selected]': 'selected()',
    '[attr.aria-busy]': 'loading()',
    '(document:pointerdown)': 'outside($event)',
    '(document:focusin)': 'outside($event)',
    '(document:keydown.escape)': 'select(false)',
    '(dblclick)': 'select(false)',
  },
  template: `<div
      class="chart-readout"
      aria-live="off"
      [style.visibility]="loading() ? 'hidden' : 'visible'"
    >
      {{ readout() || 'Market time · IST (UTC+05:30)' }}
    </div>
    <div class="chart-host" #host [style.visibility]="loading() ? 'hidden' : 'visible'"></div>
    @if (loading()) {
      <div class="chart-empty chart-loading" role="status">
        <span class="chart-spinner" aria-hidden="true"></span>
        <strong>Loading chart data…</strong><span>Candles appear when loading is complete.</span>
      </div>
    } @else if (!data().length) {
      <div class="chart-empty">
        <span class="chart-empty-glyph">↗</span><strong>{{ emptyTitle() }}</strong
        ><span>{{ emptyText() }}</span>
      </div>
    } @else if (!selected()) {
      <button
        class="chart-activate"
        (click)="select(true)"
        aria-label="Activate chart interactions"
      >
        <span>Click to interact · scroll to move down the page</span>
      </button>
    } @else {
      <button
        class="chart-selection"
        (keydown.enter)="select(false)"
        (keydown.space)="$event.preventDefault(); select(false)"
      >
        Chart selected · scroll to zoom · double-click to unselect
      </button>
    }`,
  styles: `
    :host {
      display: block;
      position: relative;
      min-height: 280px;
      height: 100%;
      background: #101c2c;
      border-radius: 12px;
      overflow: hidden;
    }
    .chart-readout {
      height: 42px;
      box-sizing: border-box;
      padding: 8px 12px;
      color: #bdccdd;
      font: 11px/1.3 system-ui;
      overflow: hidden;
      display: flex;
      align-items: center;
    }
    .chart-host {
      height: calc(100% - 42px);
      min-height: 0;
    }
    :host.chart-selected {
      box-shadow: inset 0 0 0 2px #5ed6b4;
    }
    :host.chart-selected::after {
      content: '';
      position: absolute;
      inset: 0;
      border: 2px solid #5ed6b4;
      border-radius: 12px;
      pointer-events: none;
      z-index: 7;
    }
    .chart-activate {
      position: absolute;
      inset: 42px 0 0;
      z-index: 6;
      background: transparent;
      border: 0;
      cursor: pointer;
      color: #e5edf7;
      display: flex;
      align-items: flex-start;
      justify-content: center;
      padding: 12px;
      touch-action: pan-y;
    }
    .chart-activate span,
    .chart-selection {
      background: #142638ed;
      border: 1px solid #5ed6b4;
      border-radius: 6px;
      padding: 7px 10px;
      color: #e5edf7;
      font: 11px/1.4 system-ui;
    }
    .chart-activate:focus-visible {
      outline: 2px solid #5ed6b4;
      outline-offset: -3px;
    }
    .chart-selection {
      position: absolute;
      top: 48px;
      left: 12px;
      right: 12px;
      width: fit-content;
      max-width: calc(100% - 24px);
      z-index: 6;
      cursor: pointer;
    }
    .chart-loading {
      background: #101c2c;
    }
    .chart-spinner {
      width: 28px;
      height: 28px;
      border: 3px solid #304455;
      border-top-color: #5ed6b4;
      border-radius: 50%;
      animation: chart-spin 0.8s linear infinite;
    }
    @keyframes chart-spin {
      to {
        transform: rotate(360deg);
      }
    }
    @media (prefers-reduced-motion: reduce) {
      .chart-spinner {
        animation: none;
      }
    }
    .chart-empty {
      z-index: 5;
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 12px;
      color: #e5edf7;
      pointer-events: none;
      padding: 28px;
      text-align: center;
    }
    .chart-empty span:last-child {
      font-size: 12px;
      color: #91a3ba;
      max-width: 290px;
      line-height: 1.6;
    }
    .chart-empty-glyph {
      font-size: 30px;
      color: #5ed6b4;
      border: 1px solid #304455;
      border-radius: 15px;
      padding: 8px 18px;
    }
  `,
})
export class Chart implements AfterViewInit, OnChanges, OnDestroy {
  loading = input(false);
  selected = signal(false);
  private element = inject(ElementRef<HTMLElement>);
  select(active: boolean) {
    this.selected.set(active && !this.loading() && !!this.data().length);
    this.chart?.applyOptions({
      handleScroll: this.selected(),
      handleScale: this.selected()
        ? { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: false }
        : false,
    });
    if (this.selected()) this.host.nativeElement.focus({ preventScroll: true });
  }
  outside(event: Event) {
    if (!this.element.nativeElement.contains(event.target as Node)) this.select(false);
  }
  data = input<any[]>([]);
  type = input<'candle' | 'area'>('candle');
  fitUpdates = input(false);
  emptyTitle = input('Your market, in focus');
  emptyText = input('Connect your Kite account to see market data here.');
  @ViewChild('host') host!: ElementRef<HTMLElement>;
  readout = signal('');
  private fitted = false;
  fit() {
    this.chart?.timeScale().fitContent();
  }
  latest() {
    this.chart?.timeScale().scrollToRealTime();
  }
  private describe(row: any) {
    if (!row) return '';
    const number = (v: number) => v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
    return (
      formatChartTime(row.time) +
      (this.type() === 'area'
        ? ' · ' + number(row.value)
        : ' · O ' +
          number(row.open) +
          ' H ' +
          number(row.high) +
          ' L ' +
          number(row.low) +
          ' C ' +
          number(row.close))
    );
  }
  private chart?: IChartApi;
  private series?: ISeriesApi<any>;
  private observer?: ResizeObserver;
  ngAfterViewInit() {
    this.chart = createChart(this.host.nativeElement, {
      handleScroll: false,
      handleScale: false,
      layout: {
        background: { type: ColorType.Solid, color: '#101c2c' },
        textColor: '#8295ac',
        fontFamily: 'Inter, system-ui, sans-serif',
        fontSize: 11,
      },
      grid: { vertLines: { color: '#1b2a3c' }, horzLines: { color: '#1b2a3c' } },
      rightPriceScale: { borderColor: '#26374a' },
      localization: { locale: 'en-GB', timeFormatter: formatChartTime },
      timeScale: {
        borderColor: '#26374a',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: formatChartTick,
      },
      crosshair: { vertLine: { color: '#63758a' }, horzLine: { color: '#63758a' } },
    });
    this.host.nativeElement.tabIndex = -1;
    this.series =
      this.type() === 'area'
        ? this.chart.addAreaSeries({
            lineColor: '#55d9b0',
            topColor: '#27ad873d',
            bottomColor: '#27ad8700',
            lineWidth: 2,
          })
        : this.chart.addCandlestickSeries({
            upColor: '#4ecda6',
            downColor: '#f0798a',
            wickUpColor: '#4ecda6',
            wickDownColor: '#f0798a',
            borderVisible: false,
          });
    this.chart.subscribeCrosshairMove((param) => {
      const row = param.seriesData.get(this.series!) as any;
      this.readout.set(this.describe(row ? { ...row, time: param.time } : this.data().at(-1)));
    });
    this.observer = new ResizeObserver((entries) => {
      const box = entries[0].contentRect;
      this.chart?.applyOptions({
        width: Math.floor(box.width),
        height: Math.max(180, Math.floor(box.height)),
      });
    });
    this.observer.observe(this.host.nativeElement);
    this.paint();
  }
  ngOnChanges() {
    if (this.loading()) this.select(false);
    this.paint();
  }
  private renderedData: any[] | null = null;
  private paint() {
    if (!this.series) return;
    if (this.loading()) {
      if (this.renderedData !== null) this.series.setData([]);
      this.renderedData = null;
      this.readout.set('');
      this.fitted = false;
      return;
    }
    const data = this.data();
    if (data === this.renderedData) return;
    const previous = this.renderedData;
    if (previous?.length && data.length >= previous.length) {
      let prefix = 0;
      while (prefix < previous.length - 1 && data[prefix] === previous[prefix]) prefix++;
      const tail = data.slice(previous.length - 1);
      let last = chartDate(previous[previous.length - 1].time).getTime();
      const valid = tail.every((row, i) => {
        const stamp = chartDate(row.time).getTime();
        const ordered = Number.isFinite(stamp) && (i === 0 ? stamp === last : stamp > last);
        last = stamp;
        return ordered;
      });
      if (prefix === previous.length - 1 && valid && !this.fitUpdates()) {
        for (const row of tail) this.series.update(row);
        this.readout.set(this.describe(data.at(-1)));
        this.renderedData = data;
        return;
      }
    }
    const unique = new Map<number, any>();
    for (const row of this.data()) {
      if (row.time == null) continue;
      const key = chartDate(row.time).getTime();
      if (Number.isFinite(key)) unique.set(key, row);
    }
    const rows = [...unique.entries()].sort(([a], [b]) => a - b).map(([, row]) => row);
    this.readout.set(this.describe(rows.at(-1)));
    this.chart?.applyOptions({ timeScale: { timeVisible: typeof rows[0]?.time === 'number' } });
    this.series.setData(rows);
    this.renderedData = data;
    if (rows.length && (!this.fitted || this.fitUpdates())) {
      this.chart?.timeScale().fitContent();
      this.fitted = true;
    }
    if (!rows.length) this.fitted = false;
  }
  ngOnDestroy() {
    this.observer?.disconnect();
    this.chart?.remove();
  }
}
