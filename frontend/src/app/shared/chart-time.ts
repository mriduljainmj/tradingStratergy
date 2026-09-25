/** Keep data timestamps in UTC; format the display in Indian market time. */
export type ChartTime = number | string | { year: number; month: number; day: number };
export function chartDate(time: ChartTime): Date {
  if (typeof time === 'number') return new Date(time * 1000);
  if (typeof time === 'string') return new Date(time + 'T00:00:00Z');
  return new Date(Date.UTC(time.year, time.month - 1, time.day));
}
export function formatChartTime(time: ChartTime): string {
  const intraday = typeof time === 'number';
  return (
    new Intl.DateTimeFormat('en-GB', {
      timeZone: intraday ? 'Asia/Kolkata' : 'UTC',
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      ...(intraday ? { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' as const } : {}),
    }).format(chartDate(time)) + (intraday ? ' IST' : '')
  );
}
export function formatChartTick(time: ChartTime, kind: number): string {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: typeof time === 'number' ? 'Asia/Kolkata' : 'UTC',
    ...(kind === 0
      ? { year: 'numeric' as const }
      : kind === 1
        ? { month: 'short' as const, year: '2-digit' as const }
        : kind === 2
          ? { day: '2-digit' as const, month: 'short' as const }
          : { hour: '2-digit' as const, minute: '2-digit' as const, hourCycle: 'h23' as const }),
  }).format(chartDate(time));
}
