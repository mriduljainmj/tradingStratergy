import { Candle } from '../core/models';

/** Combine historical OHLC with streamed five-minute bars, without mutating history. */
export function liveCandles(history: Candle[], updates: any[], fetchedAt = 0): Candle[] {
  const bars = new Map<number, Candle>();
  for (const row of history) {
    if (typeof row.time !== 'number') continue;
    const bucket = Math.floor(row.time / 300) * 300;
    const old = bars.get(bucket);
    bars.set(
      bucket,
      old
        ? {
            ...old,
            high: Math.max(old.high, row.high),
            low: Math.min(old.low, row.low),
            close: row.close,
          }
        : { ...row, time: bucket },
    );
  }
  for (const row of updates) {
    const old = bars.get(row.time);
    if (old)
      bars.set(row.time, {
        ...old,
        high: Math.max(old.high, row.high),
        low: Math.min(old.low, row.low),
        close: row.last_tick >= fetchedAt ? row.close : old.close,
      });
    else if (!history.length || row.time >= Number(history[history.length - 1].time))
      bars.set(row.time, { ...row });
  }
  return [...bars.values()].sort((a, b) => a.time - b.time);
}
