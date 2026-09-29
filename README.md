# Axiom trading workspace

An Angular frontend and Python Flask API for a multi-user NIFTY options and NSE equity trading workspace. The interface supports Light, Dark and System themes, dark TradingView Lightweight Charts, responsive navigation and explicit loading, empty and error states.

## Features

| Workspace | Features |
| --- | --- |
| Overview | Paper balance, realized/open P&L, win rate, NIFTY candles, opening-range levels, selected option premium chart, option chain with expiry selection, positions, recent trades and engine activity |
| Trading controls | Mode selection with execution paused, personal paper trading and backtests, administrator-only live execution, entry pause, manual call/put entry and exit, optional paper restart auto-resume |
| Kite portfolio | Broker holdings, invested/current value, P&L, Buy/Sell shortcuts and cash-equity position exits; refreshed every 30 seconds while visible |
| Equity trading | NSE/BSE cash-equity search; buy/sell, MARKET/LIMIT/SL/SL-M, CNC/MIS, regular/AMO, DAY/IOC, disclosed quantity, market protection, reviewed submissions, order status, modification and cancellation |
| Market explorer | NSE instrument search, sector filters, quotes, personal watchlists, technical stock details and momentum scanning |
| Strategy studio | Create/edit/delete options ORB, equity ORB and EMA crossover strategies; position size, direction, targets, stops, trading windows and loss limits; select options strategy or run equity engines |
| Backtest lab | Single session, date range and parameter optimization for options ORB; historical candles, cumulative P&L, result tables and full assumptions |
| Performance | Date/mode/strategy filters, summary metrics, equity curve, monthly results, paginated trades, complete filtered CSV export, CSV import, Kite fill synchronization and paper/live comparison |
| Multi-chart | One, two or four dark charts, grid/stacked layouts, focus view, history ranges, fit/latest controls, IST date/time and OHLC readouts, and account-saved database layouts |
| Account | Registration/login, profile/photo, password change, session revocation, broker credentials/OAuth/token connection, disconnect and account deletion |
| Settings | Separate PAPER, LIVE and BACKTEST configurations; strategy, position and estimated transaction-cost settings |
| Order review | Persistent records for uncertain broker responses, execution blocking and explicit broker reconciliation from Profile |

Kite authentication is required for broker market data, historical tests and execution. Paper orders are simulated. Backtests may estimate option prices with Black–Scholes when historical option data is missing; result details identify the source. Cost and contract-size settings are configurable assumptions, not automatically maintained exchange rules.

## Local setup

Requirements: Python 3.11+ (verified with 3.13), Node 22.22.3+ on the 22.x line or Node 24.15+, and npm.

Recommended local startup (macOS/Linux):

```bash
python3 run_local.py
```

This creates `.venv` if needed, installs Python dependencies, installs a project-local Node 22.22.3 when the system Node is incompatible, installs frontend dependencies and builds Angular. It preserves an existing `.env` and database, restores your broker session, and starts port 8080 with engine auto-start disabled. Use `--setup-only` to install/build without starting, or `--port 8081` to choose a different port. First setup needs internet access. Press Ctrl+C to stop the server.

Manual setup:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
test -f .env || cp .env.example .env
cd frontend
npm ci
npm run build
cd ..
AUTO_START_TRADING=0 .venv/bin/python main.py
```

Open http://127.0.0.1:8080. Existing `.env` and database files should be retained during upgrades. The first registered account becomes administrator; subsequent accounts are members. Alternatively configure `DEFAULT_USER_EMAIL` and `DEFAULT_USER_PASSWORD` to provision an administrator. Do not use shared/default passwords.

Sign in with your existing account, or create an account on a fresh installation. If Kite reports an expired session, open Profile and connect Kite again. The app login and Kite login are separate. Market data, portfolio and historical backtests need a working Kite connection; application login itself does not. The local launcher never enables trading automatically.

For frontend development, keep Python running on port 8080 and run `npm start` in `frontend/`. Angular serves port 4200 and proxies `/api`, `/health` and `/kite` to Python. Production builds are served by Flask with deep-link fallback; rebuild after frontend edits. If the Angular build is missing, page requests return HTTP 503 with build instructions.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
cd frontend && npm run build && cd ..
.venv/bin/python tests/ui_smoke.py
```

Backend tests use a temporary database and blank broker credentials. Browser tests use an isolated Flask server, test accounts and public instrument fixtures. They exercise responsive routes, strategy CRUD, cancellation, profile persistence, session logout, roles and server outage recovery. Screenshots are written to a printed temporary directory.

See [the hard-coded configuration audit](docs/CONFIGURATION_AUDIT.md) for persisted settings, remaining constants and reasons. Stock fields support symbol/company autocomplete backed by a daily database-cached broker catalogue.

See [verification and migration notes](docs/REVAMP.md) for the tested scope and operational limitations.

## Deployment

`render.yaml` installs Python dependencies, runs `npm ci && npm run build` inside `frontend/`, and starts one Gunicorn worker with threads. The current production bundle is also kept in `frontend/dist/` as a fallback for an existing native Render service until its Blueprint build command is synchronized. The multi-stage `Dockerfile` remains available for container deployments. Configure a persistent PostgreSQL `DATABASE_URL`, `APP_ENV=production`, a stable random `JWT_SECRET_KEY` of at least 32 characters and broker keys. Keep `ENCRYPT_KEY` stable if already used to encrypt stored broker credentials. Without an explicit encryption key, the signing secret derives the encryption key; changing it requires reconnecting broker sessions.

Run **one Python worker** with threads: engine state and coordination locks live in that process. Horizontal replicas or multiple Gunicorn workers require a separate shared execution service and distributed locking. The image runs one worker with eight threads on port 8080. Use an always-on host for any unattended engine; a sleeping/free web service cannot reliably manage positions.

New accounts default to paused trading. Startup auto-resume requires administrator access, a current broker session and opt-in, and starts PAPER only. `RESTORE_TRADING_SESSIONS=0` disables restoration. Closing a browser or signing out does not close broker positions. Pausing entries keeps existing position management active.

## Structure

Overview NIFTY and option charts use a per-account Kite WebSocket feed, relayed through authenticated `/api/market-stream` SSE requests. A current Kite session is required. Streaming is read-only and works while trading is paused. Initial five-minute history is reconciled every 30 seconds; streamed quotes update the current candle between refreshes. The chart displays connection/freshness status and retains historical data during outages. Selected option contracts are subscribed by exact instrument token.

Keep SSE proxy buffering disabled (`X-Accel-Buffering: no` is sent), and allow long-lived HTTP responses. Each open streaming page uses a server thread, so size the Gunicorn thread pool for concurrent viewers plus ordinary API requests. WebSocket connections are shared across tabs for the same account within the single worker and close after the last viewer leaves. The multi-chart workspace subscribes to its visible stock/index charts, aggregates live ticks into the selected timeframe, and reconciles recent history every minute. Focusing a chart unsubscribes hidden panes. The endpoint accepts up to 32 resolved instruments per viewer, with six viewers per account.

Chart history and selected option history are cached per account, symbol, interval and date range in `.market-cache/history.sqlite` (override with `HISTORY_CACHE_PATH`). Completed historical ranges expire after 24 hours; ranges including today expire after 10 seconds. Refresh bypasses the latest range's cache. Least-recently-used entries are evicted above 64 MB of stored payload. This caches historical OHLC candles, not downloadable tick-by-tick history; incoming ticks maintain in-memory candles. Trading and backtest broker reads do not use this chart cache.

Multi-chart history combines up to 128 adjacent cached pages per request, with row-count and processing-time limits. Only the first page may fetch from Kite; subsequent pages are cache-only so cold requests remain bounded. Empty past ranges also remain cached for 24 hours. Large history responses use gzip when accepted by the browser. Cache labels reflect the full load, including mixed broker/cache results. Streaming preserves unchanged candle objects and updates the newest bar incrementally; corrections to older bars still redraw the history. Initial uncached loads retain broker pacing and the full requested history, and candles stay hidden until loading completes.

The Charts page opens with one empty slot and fetches history only after symbol selection or an explicit Load action. Additional slots also start empty. Saved history ranges and timeframes remain available, but saved symbols are not automatically loaded on entry. Market Explorer's Open chart shortcut supplies the selected symbol and opens it directly in a single pane.

All shared candlestick charts (Overview, Multi-chart and backtest candles) include an **Indicators** picker with configurable periods/colors and removable value chips. Available studies: SMA, EMA, WMA, Bollinger bands (2 standard deviations), Donchian channels, IST-session VWAP, RSI, MACD (12/26/9), Stochastic (%K and 3-period %D), ATR, CCI, ROC, OBV and volume. Up to eight studies are supported, with one synchronized lower study panel. Studies use loaded candles; missing warm-up values are gaps. VWAP needs intraday data, and volume studies require real traded volume; price-only live updates leave volume studies incomplete until history refreshes. VWAP and OBV start from available history, so load complete sessions for session VWAP.

The left icon toolbar groups line tools and shapes, alongside Fibonacci retracements, text notes, freehand brush and price/bar measurements. Line tools include trend lines, rays and horizontal/vertical levels; shapes include rectangles and ellipses. Choose a color, then click one or two chart points as prompted, or drag for the brush. Snap attaches points to the nearest candle OHLC. Lock prevents drawing changes, Hide toggles visibility, and the trash action clears drawings (Redo restores them individually). Use Undo/Redo, or **Drawings** to delete an individual object. Escape cancels a drawing; double-click releases chart interaction. Drawings use time/price coordinates and follow pan, zoom and fullscreen resize. The top **Indicators** menu also includes an EMA ribbon preset (10/21/50/200) with a compact value legend. Indicators and drawings are session-local, and drawings clear on instrument/timeframe changes. This is an application-owned toolset built on Lightweight Charts, not the complete TradingView/Pine Script library.

- `frontend/src/app/`: standalone Angular routes, services, forms and shared charts.
- `dashboard/`: Flask routes, authentication, validation, Angular serving and order reconciliation.
- `core/`: per-user engine lifecycle, strategy state and rules.
- `execution/`: broker integration, options/equity execution and historical backtesting.
- `db/`: SQLAlchemy models, migrations and trade persistence.
- `tests/`: isolated Python integration/regression and Playwright browser checks.

## Manual equity trading

Open **Equity trading**, or choose **Buy**, **Sell** or **Close position** in Kite portfolio. Market Explorer also links to the ticket. Select a cash-equity symbol from the NSE/BSE catalogue, choose delivery (CNC) or intraday (MIS), enter quantity and order conditions, then review and confirm. The review shows the last available broker quote and an estimated margin for new orders. Quotes can be stale outside market hours and market-order prices are not guaranteed. Modifications use total quantity including existing fills. Cancellation only affects the unfilled remainder.

Submission requires an administrator account, a current Kite session, and Live mode. A running NIFTY options engine or paper strategy does not block cash-equity orders. Manual orders for a stock with a running live equity strategy or a remaining strategy position are blocked at both review and submission; uncertain broker orders still block all execution. The ticket never enables Live or starts an engine. The order book refreshes every ten seconds and shows today's broker statuses and partial fills, including orders placed through Kite. This ticket supports regular and AMO cash-equity orders; GTT, iceberg, cover, auctions, TTL and MTF are not implemented. Instruments and broker restrictions determine whether an individual order is accepted. Delivery sells may require authorisation in Kite/CDSL; no TPIN is collected by this app.

Every confirmation carries a signed, two-minute review and persists a request ID before contacting Kite. Repeating that request returns its original outcome. Unknown outcomes block additional execution; refreshing Orders reconciles submissions by their broker tag, and modification/cancellation by the observed broker state. An absent order is never automatically assumed to have failed. For remaining uncertainty, Profile exposes explicit broker reconciliation after a two-minute cooldown, only against a flat account with no outstanding orders. Prior-day unknown requests require an operator audit of broker history. The database must persist across deploys for these protections (PostgreSQL or a persistent SQLite volume). Run one application worker as required by the existing per-account engine architecture.

Offline equity checks (never place real orders):

```sh
.venv/bin/python -m unittest discover -s tests -p test_equity_orders.py -v
.venv/bin/python tests/ui_equity_orders.py
```

API contract reference: [Kite orders](https://kite.trade/docs/connect/v3/orders/), [margins](https://kite.trade/docs/connect/v3/margins/) and [holdings authorisation](https://kite.trade/docs/connect/v3/portfolio/).
