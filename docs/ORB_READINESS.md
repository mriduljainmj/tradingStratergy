# Modes, backtests and live ORB readiness

Mode selection and order execution are separate actions. Selecting Paper, Live or Backtest leaves the ORB engine paused. A connected Kite session is required to enable Paper/Live and to fetch historical backtest data. Changing mode is blocked while a position or uncertain order needs review.

Signed-in personal accounts can select Paper and Backtest and use Backtest Lab. Live execution remains administrator-only; account administration, strategy editing and saved settings permissions have not been broadened. Signing in to the application does not grant administrator privileges or refresh the daily Kite session.

## Running a backtest

1. Open Profile and connect Kite for the current trading day.
2. Open Backtest Lab from the sidebar (or select Backtest on Overview).
3. Choose Single session or Date range, using completed sessions. The API accepts at most 365 calendar days per range.
4. Set the direction, target points and opening-range end; press Run backtest.
5. Review trades, losses, charges and data quality. Expired option contracts may be unavailable: the engine can use Black–Scholes estimates. Parameter optimization uses estimated option pricing. These results are not verified live returns.

Backtest Lab runs independently of execution mode and does not place broker orders. Opening it directly does not stop an already-running engine; pause/manage that engine separately if desired.

## Hardening implemented

- Mode changes require a separate enable action, preserve Kite authentication errors and clear queued manual actions.
- Ordinary users cannot bypass live restrictions by adding a different mode to an enable request.
- Fresh live startup verifies broker positions and orders; unmanaged exposure, pending orders and malformed/failed responses block startup.
- Mid-session live backfill reconstructs only the opening range, verifies completed opening-range minutes and cannot replay simulated buys or sells into live state.
- Live exits use the confirmed entry symbol and quantity. Broker contract metadata validates NFO lot multiples before submission.
- Live target exits require an observed option price, not a Black–Scholes estimate. Underlying stop and time exits remain available if the option quote fails.
- Existing persistent uncertain-order protection still blocks retries after an unconfirmed fill.
- `AUTO_START_TRADING=0` disables startup auto-resume without changing user preferences or preventing explicit starts. The local maintenance run uses this override while restoring Kite sessions.

## Not certified for unattended real-money use

No live order or full market-session acceptance test was performed. Stops remain application-managed; server failure, sleep, network loss or broker outages can leave positions unmanaged. External orders and manual broker changes can race with preflight checks. Broker lot sizes, permissions, margin and order acceptance must be verified against the actual account. Market hours/holiday behavior, fees and historical pricing assumptions need ongoing validation. The code changes do not establish profitability or make losses impossible.

Before using real money, complete a supervised full-session paper test, verify entry/exit behavior and logs against Kite quotes, test interruption/reconciliation paths, and review actual lot size and risk settings. If a live order becomes uncertain, inspect the broker order book and positions rather than repeatedly enabling the engine.
