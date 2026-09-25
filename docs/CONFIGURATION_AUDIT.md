# Configuration and hard-coded values audit

This audit covers behavior, market assumptions, user preferences and operational limits. UI text, CSS dimensions and mathematical constants are intentionally code, rather than database records.

## Changed in this update

| Value | Previous source | Current source / how to change |
| --- | --- | --- |
| Chart symbols, layout, arrangement, timeframes and history range | Fixed starter symbols and browser localStorage | `chart_workspaces` table per user. Use Multi-chart → Save workspace. Unsaved defaults use the user's watchlist and the configured primary index. |
| Stock autocomplete catalogue | Broker/in-memory lookup | `instrument_catalogs` table, refreshed from Kite on the first daily lookup; saved catalogue remains searchable during a broker outage. Search symbol or company name. |
| New options strategy target, trail, lots, opening time and loss limit | Repeated frontend numeric values | Saved PAPER settings returned by the API. Existing strategy rules retain their own database values. |
| Backtest target, direction and opening time | Fixed frontend values | Saved BACKTEST settings returned by the API. Per-run overrides remain available. |
| Paper starting capital | ₹100,000 repeated in state and engine | `paper_starting_balance` in per-mode user settings. Settings → PAPER → Paper starting balance. Applied to newly created user engines; changing it does not rewrite the current session balance or trade history. |
| Seeded options strategy | Two separate dictionaries with conflicting lot sizes and entry times | One `default_options_rules()` factory based on `TradingConfig`; the created strategy is stored in the database. Existing strategies are not rewritten. |
| Fee-breakdown labels | Fixed percentage/rupee labels, even after settings changed | Labels calculated from the same configured fee rates as the actual charge calculation. |
| Equity symbol form default | RELIANCE | Empty field with searchable stock suggestions. |

Chart preferences are saved explicitly to the account. Old browser-only `axiom_charts` settings are not silently copied between accounts because the old key did not identify its owner; save the desired layout again. The backend accepts only supported layouts/timeframes and isolates saved workspaces by authenticated user.

## Already dynamic or persisted

- Accounts, passwords, administrator role, profiles, photos and background-trading preferences: database.
- Kite credentials and access tokens: encrypted database records, with environment fallbacks for app credentials.
- Strategies, symbols, directions, quantities, targets, stops, windows and EMA periods: strategy JSON in database.
- Options engine settings by PAPER/LIVE/BACKTEST mode: user `settings_json`. Targets, lot size, lot multiplier, strike spacing, loss limits, windows, volatility/rate assumptions, slippage and transaction-cost rates are editable.
- Trades, imported history, watchlists, revoked sessions and uncertain-order incidents: database.
- Market prices/history and actual option contracts: broker data. Instrument search now also persists normalized equity metadata.
- Deployment host/port, database URL, signing/encryption keys and restore toggle: environment configuration. Secrets should stay out of ordinary editable settings tables.

## Still defined in code

| Area | Location | Why / next step |
| --- | --- | --- |
| Default values for unset/new accounts | `config/settings.py` | A default remains necessary before any user saves settings. Most trading values are overridden by persisted mode settings. |
| NIFTY-specific options strategy, default index/token and supported index aliases | `config/settings.py`, `dashboard/routes.py`, options execution modules | The current ORB implementation assumes NIFTY contracts. Generalizing this needs instrument-specific contract, expiry and position-size handling, not just a dropdown. |
| Lot-size/strike-spacing seed values | `config/settings.py` | Editable and persisted, but not automatically synchronized with broker contract specifications. Avoid treating seeded values as current exchange rules. |
| Equity ORB/EMA defaults when a rule is absent | `execution/equity_engine.py`, strategy editor | Actual strategies persist overrides. A future per-user strategy-template editor can make creation defaults fully configurable. |
| Backtest optimization starter targets/times and request caps | Backtest page, `dashboard/api_support.py`, `execution/historical_backtest.py` | Search inputs are editable per run. Saved experiment presets are not implemented. Request caps protect the server. |
| Market session times, trading-day assumptions and fallback expiry calculations | Routes, broker/options math and execution/backtest modules | Need a maintained exchange-calendar/contract reference source. Stored strings alone would not keep holiday/expiry rules accurate. |
| Sector/company reference mappings | `dashboard/nse_data.py` | Broker metadata supplies names/symbols but not complete sector classification. A managed reference-data import/editor would be needed. |
| Momentum-screening thresholds and universe caps | `dashboard/screener_routes.py` | Market-cap ranges, indicator thresholds and shortlist caps remain strategy logic. A validated scan-preset model is a useful next extension. |
| Browser poll/debounce intervals, API timeouts and cache freshness | Frontend services, symbol-search component, broker/routes | Operational defaults. Could become deployment config; they should not be unrestricted per-user DB values. |
| Supported chart layouts, history choices and timeframe enumeration | Workspace API and charts page | UI/SDK/API capabilities; user selections within those choices are persisted. |
| Chart timezone | `frontend/src/app/shared/chart-time.ts` | Explicit Asia/Kolkata for NSE. A per-user display timezone is possible without altering stored UTC timestamps. |
| API input bounds, upload sizes, password/JWT lifetime and permissions | API boundary, auth and app factory | Security/product constraints. Prefer reviewed deployment policy, not arbitrary editable user settings. |
| Indicator formulas, P&L/fee formula structure and engine state transitions | Core and execution modules | Algorithms belong in versioned/tested code; their supported input parameters can be configuration. |
| Standalone CLI backtester defaults | `BacktestConfig`, `backtest_runner.py` | Separate research entry point, not the logged-in application; not bound to user database settings. |
| Visual styling, branding, navigation and feature labels | Angular components and SCSS | Application implementation; a DB-backed CMS/theme editor is not currently needed. |

## Verification

Tests cover workspace persistence and account isolation, invalid preference rejection, company-name search, database catalogue reuse, paper-capital loading, keyboard autocomplete selection, saved-layout reload and the existing browser/backend workflows. Offline tests use temporary databases and stub broker metadata; live-order behavior is not exercised.
