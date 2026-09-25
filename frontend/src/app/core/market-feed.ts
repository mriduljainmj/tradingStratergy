import { Injectable, inject, signal } from '@angular/core';
import { Auth } from './api';

@Injectable()
export class MarketFeed {
  private auth = inject(Auth);
  snapshot = signal<any>(null);
  status = signal('Connecting to Kite…');
  private key = '';
  private controller?: AbortController;
  private retry?: ReturnType<typeof setTimeout>;
  configure(tokens: number[]) {
    tokens = [...new Set(tokens)].sort((a, b) => a - b);
    const jwt = this.auth.token();
    const key = tokens.join(',') + jwt;
    if (key === this.key) return;
    this.stop();
    if (!tokens.length || !jwt) return;
    this.key = key;
    this.controller = new AbortController();
    void this.connect(tokens, jwt, this.controller);
  }
  stop() {
    this.key = '';
    this.controller?.abort();
    clearTimeout(this.retry);
    this.snapshot.set(null);
    this.status.set('Live feed disconnected');
  }
  private async connect(tokens: number[], jwt: string, controller: AbortController) {
    if (controller.signal.aborted) return;
    this.status.set('Connecting to Kite…');
    let retry = true;
    try {
      const response = await fetch('/api/market-stream?tokens=' + tokens.join(','), {
        headers: { Authorization: 'Bearer ' + jwt },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        retry = ![400, 401, 403].includes(response.status);
        throw new Error(
          [401, 403, 409].includes(response.status)
            ? 'Reconnect Kite or sign in to restore streaming.'
            : 'Live feed unavailable; showing historical data.',
        );
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = '';
      try {
        while (!controller.signal.aborted) {
          let timeout: ReturnType<typeof setTimeout> | undefined;
          const result = await Promise.race([
            reader.read(),
            new Promise<never>((_, reject) => {
              timeout = setTimeout(
                () => reject(new Error('Live feed stalled; reconnecting…')),
                15000,
              );
            }),
          ]).finally(() => clearTimeout(timeout));
          if (result.done) break;
          pending += decoder.decode(result.value, { stream: true });
          const frames = pending.split('\n\n');
          pending = frames.pop() || '';
          for (const frame of frames) {
            if (!frame.startsWith('data: ')) continue;
            const snapshot = JSON.parse(frame.slice(6));
            if (controller.signal.aborted) return;
            const previous = this.snapshot();
            for (const field of ['candles', 'minute_candles']) {
              for (const [token, rows] of Object.entries(snapshot[field] || {})) {
                const merged = new Map<number, any>(
                  (previous?.[field]?.[token] || []).map((row: any) => [row.time, row]),
                );
                for (const row of rows as any[]) merged.set(row.time, row);
                snapshot[field][token] = [...merged.values()]
                  .sort((a, b) => a.time - b.time)
                  .slice(field === 'candles' ? -90 : -390);
              }
            }
            this.snapshot.set(snapshot);
            const fresh = Object.values(snapshot.quotes || {}).some(
              (q: any) => snapshot.server_time - q.time < 15,
            );
            this.status.set(
              snapshot.status === 'connected'
                ? fresh
                  ? 'Live · Kite stream'
                  : 'Kite connected · waiting for fresh ticks'
                : 'Kite ' + snapshot.status + ' · historical data retained',
            );
          }
        }
      } finally {
        await reader.cancel().catch(() => {});
      }
    } catch (e) {
      if (!controller.signal.aborted)
        this.status.set(e instanceof Error ? e.message : 'Live feed unavailable');
    } finally {
      if (!controller.signal.aborted && retry)
        this.retry = setTimeout(() => void this.connect(tokens, jwt, controller), 3000);
    }
  }
}
