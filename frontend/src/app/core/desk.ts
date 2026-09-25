import { Injectable, inject, signal } from '@angular/core';
import { Api, Auth, message } from './api';
import { Feedback } from './feedback';
import { MarketState } from './models';
@Injectable({ providedIn: 'root' })
export class Desk {
  api = inject(Api);
  auth = inject(Auth);
  feedback = inject(Feedback);
  private seenEvents = new Set<string>();
  private eventsInitialized = false;
  state = signal<MarketState | null>(null);
  error = signal('');
  updated = signal<Date | null>(null);
  private timer: any;
  private pending = false;
  private generation = 0;
  start() {
    clearInterval(this.timer);
    void this.refresh();
    this.timer = setInterval(() => void this.refresh(), 4000);
  }
  stop() {
    this.seenEvents.clear();
    this.eventsInitialized = false;
    this.feedback.executions.set([]);
    this.generation++;
    this.pending = false;
    clearInterval(this.timer);
    this.state.set(null);
    this.updated.set(null);
    this.error.set('');
  }
  async refresh() {
    if (this.pending) return;
    this.pending = true;
    const generation = this.generation;
    try {
      const state = await this.api.get<MarketState>('/state');
      if (generation !== this.generation) return;
      this.state.set(state);
      const events = state.execution_events || [];
      const fresh = events.filter((event) => !this.seenEvents.has(event.id));
      if (this.eventsInitialized && this.auth.user()?.trade_confirm_modal !== false) {
        this.feedback.executions.update((queued) => [...queued, ...fresh]);
      }
      this.seenEvents = new Set(events.map((event) => event.id));
      this.eventsInitialized = true;
      this.updated.set(new Date());
      this.error.set('');
    } catch (e) {
      if (generation !== this.generation) return;
      this.error.set(message(e));
    } finally {
      if (generation === this.generation) this.pending = false;
    }
  }
}
