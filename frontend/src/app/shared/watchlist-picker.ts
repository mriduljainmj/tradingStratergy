import { Component, input, output, signal, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, message } from '../core/api';

@Component({
  selector: 'ax-watchlist-picker',
  imports: [FormsModule],
  styles: [
    `
      :host {
        display: grid;
        gap: 8px;
        min-width: 0;
      }
      .new-list {
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
      }
      input {
        min-width: 0;
        flex: 1;
        width: 120px;
      }
      select {
        width: 100%;
      }
      p {
        margin: 0;
        font-size: 12px;
      }
    `,
  ],
  template: `
    <label
      >Watchlist
      <select
        aria-label="Selected watchlist"
        [ngModel]="selected()"
        (ngModelChange)="changed.emit($event)"
      >
        @for (name of names(); track name) {
          <option [value]="name">{{ name }}</option>
        }
      </select>
    </label>
    @if (creating()) {
      <div class="new-list">
        <input
          aria-label="New watchlist name"
          placeholder="Watchlist name"
          maxlength="100"
          [(ngModel)]="name"
          (keydown.enter)="create(); $event.preventDefault()"
        />
        <button
          class="btn small"
          type="button"
          [disabled]="busy() || !name.trim()"
          (click)="create()"
        >
          Create
        </button>
        <button class="btn small" type="button" [disabled]="busy()" (click)="creating.set(false)">
          Cancel
        </button>
      </div>
    } @else {
      <button class="btn small" type="button" (click)="creating.set(true); error.set('')">
        + New watchlist
      </button>
    }
    @if (error()) {
      <p role="alert">{{ error() }}</p>
    }
  `,
})
export class WatchlistPicker {
  names = input<string[]>([]);
  selected = input('My Watchlist');
  changed = output<string>();
  created = output<string>();
  creating = signal(false);
  busy = signal(false);
  error = signal('');
  name = '';
  private api = inject(Api);
  async create() {
    if (this.busy() || !this.name.trim()) return;
    this.busy.set(true);
    this.error.set('');
    try {
      const result = await this.api.post('/screener/watchlists', { name: this.name.trim() });
      this.created.emit(result.name);
      this.creating.set(false);
      this.name = '';
    } catch (e) {
      this.error.set(message(e));
    } finally {
      this.busy.set(false);
    }
  }
}
