/** Revalue a broker snapshot without changing quantities/costs or accumulating tick deltas. */
export function livePortfolio<T extends { holdings: any[]; positions: any[]; fetched_at: string }>(
  base: T,
  feed: any,
): T {
  const baseline = Date.parse(base.fetched_at) / 1000;
  const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
  function update(row: any, holding: boolean) {
    const q = feed?.quotes?.[String(row.instrument_token)];
    if (
      !q ||
      !finite(q.price) ||
      q.price <= 0 ||
      !finite(q.time) ||
      !finite(baseline) ||
      q.time < baseline ||
      q.time > feed.server_time + 5
    )
      return row;
    const units = holding
      ? [row.quantity, row.t1_quantity, row.mtf?.quantity ?? 0]
      : [row.quantity];
    const quantity = units.every(finite) ? units.reduce((a, b) => a + b, 0) : null;
    const multiplier = holding ? 1 : row.multiplier;
    const delta =
      quantity !== null &&
      finite(multiplier) &&
      multiplier > 0 &&
      finite(row.last_price) &&
      row.last_price > 0
        ? (q.price - row.last_price) * quantity * multiplier
        : null;
    return {
      ...row,
      last_price: q.price,
      day_change_percentage:
        holding &&
        finite(row.day_change_percentage) &&
        row.day_change_percentage > -100 &&
        finite(row.last_price) &&
        row.last_price > 0
          ? (q.price / (row.last_price / (1 + row.day_change_percentage / 100)) - 1) * 100
          : row.day_change_percentage,
      pnl: delta !== null && finite(row.pnl) ? row.pnl + delta : null,
      unrealised:
        delta !== null && finite(row.unrealised) ? row.unrealised + delta : row.unrealised,
      live_time: q.time,
    };
  }
  return {
    ...base,
    holdings: base.holdings.map((r) => update(r, true)),
    positions: base.positions.map((r) => (r.quantity === 0 ? r : update(r, false))),
  };
}
