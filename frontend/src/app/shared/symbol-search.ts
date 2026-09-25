import { Component, inject, input, output, signal, OnDestroy } from '@angular/core';
import { Api, message, query } from '../core/api';
let nextId = 0;
@Component({
  selector: 'ax-symbol-search',
  template: ` <input
      role="combobox"
      autocomplete="off"
      [attr.aria-label]="label()"
      [attr.aria-controls]="id"
      [attr.aria-expanded]="open()"
      aria-autocomplete="list"
      [attr.aria-activedescendant]="active() >= 0 ? id + '-' + active() : null"
      [value]="value()"
      [disabled]="disabled()"
      [readOnly]="readonly()"
      [required]="required()"
      placeholder="Symbol or company name…"
      (input)="search($any($event.target).value)"
      (keydown)="key($event)"
      (blur)="open.set(false)"
      (focus)="focus()"
    />
    @if (open()) {
      <div
        class="suggestions"
        [id]="id"
        role="listbox"
        [attr.aria-label]="label() + ' suggestions'"
      >
        @for (item of results(); track item.symbol; let i = $index) {
          <button
            type="button"
            role="option"
            [id]="id + '-' + i"
            [attr.aria-selected]="active() === i"
            [class.highlight]="active() === i"
            (mousedown)="$event.preventDefault()"
            (click)="choose(item.symbol)"
          >
            <strong>{{ item.symbol }}</strong
            ><span>{{ item.name }}</span>
          </button>
        }
        @if (loading()) {
          <small>Searching instruments…</small>
        } @else if (error()) {
          <small>{{ error() }}</small>
        } @else if (!results().length) {
          <small>No matching instruments.</small>
        }
      </div>
    }`,
  styles: `
    :host {
      display: block;
      position: relative;
      min-width: 0;
      width: 100%;
    }
    input {
      width: 100%;
      box-sizing: border-box;
    }
    .suggestions {
      position: absolute;
      left: 0;
      right: 0;
      top: 100%;
      background: var(--surface-base, #fff);
      color: var(--text-positive, #203833);
      border: 1px solid var(--edge, #ccd8d2);
      border-radius: 8px;
      box-shadow: 0 12px 25px #101c2c25;
      z-index: 80;
      max-height: 260px;
      overflow: auto;
      min-width: 200px;
    }
    .suggestions button {
      display: block;
      text-align: left;
      width: 100%;
      background: transparent;
      border: 0;
      padding: 10px 12px;
      color: inherit;
    }
    .suggestions button:hover,
    .suggestions button.highlight {
      background: var(--surface-positive, #eaf5ef);
    }
    .suggestions span {
      display: block;
      font-size: 11px;
      margin-top: 4px;
      color: var(--text-secondary, #60716d);
    }
    .suggestions small {
      display: block;
      padding: 12px;
    }
  `,
})
export class SymbolSearch implements OnDestroy {
  api = inject(Api);
  value = input('');
  label = input('Stock symbol');
  equity = input(false);
  disabled = input(false);
  readonly = input(false);
  required = input(true);
  valueChange = output<string>();
  selected = output<string>();
  id = 'symbols-' + nextId++;
  results = signal<any[]>([]);
  open = signal(false);
  loading = signal(false);
  error = signal('');
  active = signal(-1);
  private timer: ReturnType<typeof setTimeout> | undefined;
  private request = 0;
  focus() {
    if (!this.readonly()) this.search(this.value());
  }
  search(text: string) {
    this.valueChange.emit(text);
    clearTimeout(this.timer);
    const request = ++this.request;
    this.results.set([]);
    this.active.set(-1);
    this.error.set('');
    this.open.set(true);
    this.loading.set(true);
    this.timer = setTimeout(async () => {
      try {
        const data = await this.api.get(
          '/symbols/search' + query({ q: text, limit: 12, equity: this.equity() ? 1 : 0 }),
        );
        if (request !== this.request) return;
        this.results.set(data.results);
        this.error.set(data.warning || '');
      } catch (e) {
        if (request === this.request) this.error.set(message(e));
      } finally {
        if (request === this.request) this.loading.set(false);
      }
    }, 250);
  }
  choose(symbol: string) {
    this.request++;
    clearTimeout(this.timer);
    this.valueChange.emit(symbol);
    this.selected.emit(symbol);
    this.open.set(false);
  }
  key(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      this.open.set(false);
      return;
    }
    if (!this.open()) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const size = this.results().length;
      if (size)
        this.active.set((this.active() + (event.key === 'ArrowDown' ? 1 : -1) + size) % size);
    }
    if (event.key === 'Enter' && this.active() >= 0) {
      event.preventDefault();
      this.choose(this.results()[this.active()].symbol);
    }
  }
  ngOnDestroy() {
    this.request++;
    clearTimeout(this.timer);
  }
}
