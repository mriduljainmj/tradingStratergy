import { ChartTime, chartDate } from './chart-time';

export const DRAWING_INTERVALS: Record<string, number> = {
  minute: 60,
  '5minute': 300,
  '15minute': 900,
  '60minute': 3600,
  day: 86400,
  week: 604800,
};
export function drawingSymbol(context: string): string {
  const index = context.lastIndexOf(':');
  return DRAWING_INTERVALS[context.slice(index + 1)] ? context.slice(0, index) : context;
}
export function drawingInterval(context: string): string {
  const value = context.slice(context.lastIndexOf(':') + 1);
  return DRAWING_INTERVALS[value] ? value : '5minute';
}
// Daily/weekly candles denote a trading date; map them to that session's 09:15 IST open.
function stamp(time: ChartTime): number {
  return chartDate(time).getTime() / 1000 + (typeof time === 'number' ? 0 : 13500);
}
export function drawingPosition(
  point: any,
  rows: { time: ChartTime }[],
  interval: string,
): number | null {
  if (!rows.length) return null;
  const source = point.interval || interval;
  const anchor = stamp(point.time);
  const target = anchor + (point.offset || 0) * (DRAWING_INTERVALS[source] || 300);
  let lo = 0,
    hi = rows.length;
  // Exact same-timeframe anchors keep their original trading-bar offset across weekends.
  const search = source === interval ? anchor : target;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (stamp(rows[mid].time) < search) lo = mid + 1;
    else hi = mid;
  }
  if (source === interval && lo < rows.length && stamp(rows[lo].time) === anchor)
    return lo + (point.offset || 0);
  if (lo === 0) return (target - stamp(rows[0].time)) / DRAWING_INTERVALS[interval];
  if (lo === rows.length)
    return rows.length - 1 + (target - stamp(rows.at(-1)!.time)) / DRAWING_INTERVALS[interval];
  const left = stamp(rows[lo - 1].time),
    right = stamp(rows[lo].time);
  return lo - 1 + (target - left) / (right - left);
}
