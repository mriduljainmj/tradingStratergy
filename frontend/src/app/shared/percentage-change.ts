/** A percentage needs a known, positive reference value. */
export function percentageChange(current: unknown, reference: unknown): number | null {
  if (
    typeof current !== 'number' ||
    typeof reference !== 'number' ||
    !Number.isFinite(current) ||
    !Number.isFinite(reference) ||
    reference <= 0
  )
    return null;
  const value = ((current - reference) / reference) * 100;
  return Number.isFinite(value) ? value : null;
}

export function formatPercentage(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—';
  const rounded = Math.round(value * 100) / 100;
  return `${rounded > 0 ? '+' : ''}${rounded.toFixed(2)}%`;
}

export function chartChange(rows: any[], row: any, area = false) {
  if (!row) return null;
  const stamp = (t: any): number =>
    typeof t === 'number'
      ? t * 1000
      : typeof t === 'string'
        ? Date.parse(t)
        : t
          ? Date.UTC(t.year, t.month - 1, t.day)
          : NaN;
  const target = stamp(row.time);
  if (!Number.isFinite(target)) return null;
  // Locate the preceding loaded candle without scanning the full history on every hover.
  let lo = 0,
    hi = rows.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (stamp(rows[mid].time) < target) lo = mid + 1;
    else hi = mid;
  }
  const previous = lo > 0 ? rows[lo - 1] : null;
  const reference = previous ? (area ? previous.value : previous.close) : area ? null : row.open;
  const current = area ? row.value : row.close;
  const percent = percentageChange(current, reference);
  return percent === null
    ? null
    : {
        percent,
        amount: current - reference,
        label: previous ? (area ? 'vs previous' : 'vs prev close') : 'vs open',
      };
}
