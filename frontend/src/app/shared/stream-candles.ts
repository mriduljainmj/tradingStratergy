import { Candle } from '../core/models';

export function mergeChartStream(
  history: Candle[],
  minutes: any[],
  interval: string,
  fetchedAt = 0,
): Candle[] {
  if (!minutes.length) return history;
  const key = (t: any): number =>
    typeof t === 'number'
      ? t
      : typeof t === 'string'
        ? Date.parse(t) / 1000
        : Date.UTC(t.year, t.month - 1, t.day) / 1000;
  function bucket(ts: number): any {
    const day = new Date((ts + 19800) * 1000);
    if (['day', 'week', 'month'].includes(interval)) {
      if (interval === 'week') day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
      if (interval === 'month') day.setUTCDate(1);
      return { year: day.getUTCFullYear(), month: day.getUTCMonth() + 1, day: day.getUTCDate() };
    }
    const seconds = (interval === 'minute' ? 1 : parseInt(interval, 10)) * 60;
    const open = Math.floor((ts + 19800) / 86400) * 86400 - 19800 + 9 * 3600 + 15 * 60;
    return open + Math.floor((ts - open) / seconds) * seconds;
  }
  const bars = new Map<number, Candle>(history.map((r) => [key(r.time), { ...r }]));
  const updates = new Map<number, any>();
  for (const row of [...minutes].sort((a, b) => a.time - b.time)) {
    const time = bucket(row.time),
      id = key(time),
      old = updates.get(id);
    updates.set(
      id,
      old
        ? {
            ...old,
            high: Math.max(old.high, row.high),
            low: Math.min(old.low, row.low),
            close: row.close,
            last_tick: row.last_tick,
          }
        : { ...row, time },
    );
  }
  let latest = -Infinity;
  for (const id of bars.keys()) latest = Math.max(latest, id);
  for (const [id, row] of updates) {
    const old = bars.get(id);
    if (old)
      bars.set(id, {
        ...old,
        high: Math.max(old.high, row.high),
        low: Math.min(old.low, row.low),
        close: row.last_tick >= fetchedAt ? row.close : old.close,
      });
    else if (id >= latest) bars.set(id, row);
  }
  return [...bars.entries()].sort(([a], [b]) => a - b).map(([, row]) => row);
}
