# Angular/Python revamp: verification and migration

## Delivered

The main application is now a standalone Angular application, with lazy routes, typed API models, centralized authentication/error handling, responsive navigation and reusable dark charts. Python Flask remains the API and execution layer. The full feature inventory and run commands are in the root README.

Existing account, trade and strategy tables remain in use. Additional schema covers persistent session revocation, password-version invalidation and an execution-incident journal. No application database was used for the regression or browser test runs. Existing uncommitted repository changes were retained.

## Backend corrections

- Invalid JSON shapes, string booleans, non-finite numbers, oversized passwords, invalid dates, invalid settings and invalid strategy parameters receive controlled errors.
- Protected APIs verify that the authenticated account still exists. Logout revokes its token; password changes invalidate existing sessions.
- Broker-session errors no longer impersonate an expired application login.
- Undefined analytics ratios serialize as JSON null rather than invalid Infinity/NaN.
- Date filters, pagination and separate mode settings are validated. Backtests read saved BACKTEST configuration rather than mutable live settings.
- Background auto-resume is opt-in for new users. Startup uses one restoration path; live strategies need an explicit restart. Choosing restart auto-resume does not enable current live entries.
- Engine transitions wait for the previous worker to stop and refuse to replace it if still running. Positions and uncertain orders block destructive configuration/account changes.
- Broker order intent is saved before submission. An unconfirmed fill blocks subsequent execution and is never recorded as a successful exit. The position remains visible for review.
- Order reconciliation checks broker positions/orders, requires a flat account and returns to paused paper mode. Previous-day ambiguous orders require an operator audit rather than automatic clearance.
- Signing keys are generated locally or required explicitly in production. Encryption failures do not fall back to plaintext/base64 credential storage.

## Verification

Verified locally with Python 3.13, Node 22 and Chromium:

- Angular production compilation and strict template checking.
- 29 Python integration/regression tests, including cross-user isolation, invalid inputs, saved settings, logout, strategy CRUD, uncertain fills, engine stop behavior and no automatic live equity restart.
- Browser route checks at 1440, 768, 390 and 320 pixels across all eight workspaces.
- Real isolated Flask API round trips for login, strategy creation/edit/delete, deletion cancellation, profile updates, logout and member permissions.
- Server outage/recovery UI and populated historical chart/backtest fixtures.
- Manual screenshot inspection of desktop and mobile layouts, including empty chart states.
- npm production dependency audit: zero reported vulnerabilities at verification time.

No real broker orders were submitted. Successful live market feeds, OAuth against the deployed callback, broker order fills, external market-data availability and full historical-data calculations still require an authenticated Kite session and controlled operational validation. Populated chart fixtures verify rendering, not broker correctness or strategy performance. PostgreSQL and container deployment were not exercised locally; Docker CLI was available but its daemon was not running.

## Deployment and recovery

Build the Angular bundle before starting Flask. The Docker image includes the build and runs `wsgi:app` with one worker; extra workers/replicas are unsupported until engine ownership and locking move to a shared execution service. Avoid sleeping hosts for unattended trading.

Back up the existing database before applying schema updates to a deployed environment. Keep existing signing/encryption secrets stable; changing the encryption derivation requires reconnecting stored broker sessions. Local `.axiom_dev_secret` is ignored by Git and Docker and is not a production secret-management mechanism.

If execution pauses because an order is uncertain, verify the broker's actual orders and positions in Kite. Close positions/cancel pending orders as appropriate, then use Profile → Orders needing review → Verify broker and reconcile. Sync or import confirmed trades in Performance afterward. The app deliberately does not retry an unknown order automatically.

Live positions are not fully reconstructed from local state after a process crash. Review the broker account before explicitly restarting live execution. Optional paper restart auto-resume cannot manage a pre-existing live broker position.

## Post-migration cleanup

Removed the legacy HTML/JavaScript frontend and template fallback after migration to Angular. Missing frontend builds now return a clear HTTP 503 setup message. Removed unused Python imports, the unused Angular Vitest configuration and obsolete Karma debug tasks, and consolidated local startup to initialize the database once through the application factory. The standalone backtester remains available.

### Backend and dependency cleanup

Removed the hard-coded `fix_trade.py` repair script; the authenticated trade-recalculation API remains available. Removed unused settings/session helpers, the unused NFO symbol builder and symbol-list helper, obsolete broker token-file storage and its unused environment configuration, and an unreferenced expiry alias. Broker sessions continue to use encrypted per-user database storage.

Deleted the unused root `venv/` (Python 3.11) and generated Python/Angular/linter caches; `.venv/` (Python 3.13) is the active environment used by documented commands. Kept the standalone backtester, migration compatibility, tests, user data and project settings. All declared Python runtime dependencies have current consumers; Angular compiler/build dependencies remain required. `pip check` and npm pruning/audit passed without missing dependencies or extraneous npm packages.

### Three-session detail and weekly charts

The chart workspace now offers **Last 3 trading days · maximum detail**, which switches panes to one-minute candles. The API selects the latest three distinct market dates from a bounded 30-calendar-day broker lookup and retains every available candle in those sessions. Weekends and holidays do not count as sessions; instruments with less history can return fewer sessions, shown in the footer.

Each pane also supports **1 week** candles, aggregated from daily OHLCV with Monday date keys. When the workspace is in three-session mode, a weekly pane uses 90 calendar days of history; other history selections apply directly. The first weekly bucket includes the entire week containing the start date, and the current week can be incomplete. Both options persist in account workspace settings. Existing saved workspaces are preserved.

Validation: 32 Python regression tests, Angular production build, IST chart-time tests, and the responsive browser smoke suite passed. Browser checks include three-session requests, weekly selection and saved preference reload.

### All available chart history

**All available history** is now the default for new workspaces, with daily candles. Existing saved preferences remain intact; select this history option and save to change them. Daily, weekly and intraday history load progressively through backward date cursors, respecting Kite request windows. Empty pages do not imply that older data is unavailable. The search covers dates back to the Unix epoch (1970), before Kite's NSE/BSE archives. Weekly pagination aligns to Mondays to avoid split or duplicate buckets.

Charts fit the growing dataset while loading. Pause stops after the current request; Load remaining history resumes from the saved cursor. Errors preserve already loaded candles and provide a retry path. Navigating away stops subsequent requests. Paginated requests are paced across panes. Large minute datasets can take substantially longer than daily/weekly history; no candles are sampled or discarded.

Validation: 34 Python regression tests, Angular production build, IST date tests, and browser smoke tests including two-page history accumulation and saved All history preferences.

### Kite portfolio

A read-only `/portfolio` screen and authenticated `/api/portfolio` endpoint show the current user's delivery holdings and open net broker positions independently of the app's execution mode. Broker quantity, T1, used and pledged quantities remain separate; monetary fields use broker values rather than locally inferred totals. No orders or position conversions are submitted.

The screen supports both themes, manual refresh and 30-second refresh while visible. Failed refreshes retain a clearly labelled last-successful snapshot. Missing or expired sessions direct the user to Profile, and malformed upstream responses are errors rather than falsely empty accounts. This view is not an exit control for externally opened positions.
