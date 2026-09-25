# Application review — 20 September 2026

The account and dashboard workflows tested locally pass. Live market connectivity,
broker order execution and historical trading results are not verified: those require
a valid Kite session and market data. No real orders were placed. Testing used temporary
databases, separate from the existing trading.db.

## Fixes in this review

- Constrained the app shell to the viewport; fixed mobile controls and scrolling across
  Charts, Stocks, Builder, Results, Multi and Profile.
- Corrected conflicting Builder positioning and an extra CSS closing brace.
- Stacked mobile multi-chart panes and the watchlist; made Results panels scroll together.
- Replaced misleading “Feed OK”, “LIVE” and “WS CONNECTED” indicators with server/Kite
  status. Added polling timeout, failure feedback, recovery and overlapping-request prevention.
- Hid admin-only mode/settings controls from non-admin users, matching backend restrictions.
- Made malformed cached account JSON recoverable; prevented duplicate login submissions.
- Improved keyboard focus visibility, reduced-motion behavior and login branding.
- Fixed Monthly Returns to respect date filters. Cleared stale drawdown data on empty results;
  added loading/empty/failure labels to Results and removed the unconditional “COMPLETE” label.
- Escaped strategy names in Builder cards, removed names from inline delete handlers, and
  surfaced failed delete requests.

## Feature inventory

| Area | Existing features |
| --- | --- |
| Accounts | Registration, login, profile/photo/preferences, password changes, account deletion; admin permissions and per-user data |
| Broker | Zerodha Kite credentials, login/token connection, encrypted stored credentials/tokens, account balance |
| Charts | NIFTY and options candles, timeframe selection, trade markers, opening range, option-chain panel, position/P&L and engine logs |
| Trading | Paper/live modes, option ORB, CALL/PUT/both direction, manual entries/exits, entry toggles and background engine controls |
| Strategy management | Create/edit/delete strategies, choose active strategy, configure options or equity rules, run/stop equity engines |
| Equity strategies | Equity opening-range breakout and EMA crossover; symbol, quantity, direction, target, stop and trading-window settings |
| Risk configuration | Position sizing, target, Fibonacci trailing exits, trading windows, daily loss limit and estimated trading charges/slippage |
| Historical tools | Single-day/date-range option ORB backtests and parameter optimization endpoints; modeled option pricing where real data is unavailable |
| Stocks | Instrument search, sectors, filters, sorting/pagination, technical/momentum scanning and watchlist actions |
| Watchlists | Persistent per-user symbols, named lists, add/remove and chart selection |
| Multi-chart | One/two/four chart layouts, symbol/timeframe selection, synchronized zoom/crosshair, saved browser workspace, watchlist panel |
| Results | Trade history, date/mode/strategy filters, P&L, win rate, profit factor, Sharpe, drawdown, equity curve, monthly breakdown and visible-trade CSV export |
| Trade ingestion | Kite fill preview/sync; CSV import, comparison and deeper insights also have backend endpoints |

Some UI/endpoint features have important boundaries: trade export uses the visible page;
return distribution and friction panels use loaded-page trades. The historical runner is
an options ORB runner, not a general executor for arbitrary visual strategy graphs.
The supported equity engines are ORB and EMA crossover. Backend-only endpoints should
not be assumed to have a complete exposed UI workflow.

## Validation

Run backend checks (only application requirements needed):

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Six integration tests cover login failures/success, registration, role enforcement,
user isolation, page/health responses, state/settings/balance, analytics, strategy CRUD,
watchlist CRUD, profile saving and a populated monthly-date-filter regression.

Run browser checks (optional development dependency):

```sh
.venv/bin/python -m pip install playwright
.venv/bin/python -m playwright install chromium
.venv/bin/python tests/ui_smoke.py
```

The browser test starts its own local server and temporary database. It covers all five
workspaces at 1440, 768, 390 and 320 pixels, login, Profile, role controls, corrupt cached
user data, request failures/recovery, chart rendering and stale drawdown clearing.
Instrument/quote/history fixtures make browser market data deterministic; they do not
validate broker connectivity. External chart/font/date-picker assets still require internet.
Desktop/mobile screenshots were also inspected manually during the review.

Remaining verification: authenticated broker data, live/paper trading engine behavior,
backtest accuracy, trade sync/import datasets, and full cross-browser/accessibility auditing.
