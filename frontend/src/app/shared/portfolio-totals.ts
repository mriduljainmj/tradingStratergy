/** Sum a complete broker snapshot; missing figures must not appear as zero. */
export function portfolioTotals(holdings: any[], positions: any[]) {
  const number = (n: any) => typeof n === 'number' && Number.isFinite(n);
  const sum = (values: (number | null)[]) =>
    values.some((v) => v === null) ? null : values.reduce<number>((a, b) => a + b!, 0);
  function value(row: any, current: boolean): number | null {
    const quantity = row.quantity;
    const t1 = row.t1_quantity;
    const mtf = row.mtf?.quantity ?? 0;
    if (![quantity, t1, mtf].every((n) => number(n) && n >= 0)) return null;
    const held = quantity + t1;
    const price = current ? row.last_price : row.average_price;
    const mtfPrice = current ? row.last_price : row.mtf?.average_price;
    if (
      (!current && row.discrepancy) ||
      (held > 0 && (!number(price) || price <= 0)) ||
      (mtf > 0 && (!number(mtfPrice) || mtfPrice <= 0))
    )
      return null;
    // Kite's current quantity already reflects sells; never subtract used_quantity again.
    // Collateral is a subset of holdings, not an additional quantity.
    return (held ? held * price : 0) + (mtf ? mtf * mtfPrice : 0);
  }
  return {
    invested: sum(holdings.map((row) => value(row, false))),
    current: sum(holdings.map((row) => value(row, true))),
    pnl: sum(holdings.map((row) => (number(row.pnl) ? row.pnl : null))),
    positionsPnl: sum(positions.map((row) => (number(row.pnl) ? row.pnl : null))),
  };
}
