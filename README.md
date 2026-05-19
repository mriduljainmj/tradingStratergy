# NiftySwing: Enterprise ORB Trading Console

A professional-grade, multi-user trading platform designed for NIFTY options. It combines high-fidelity backtesting, paper trading, and live execution into a single, cohesive dashboard.

## 🚀 Key Features

### 📈 Trading Modes
*   **Live Trading:** Execute real orders on Zerodha Kite with automated entry/exit.
*   **Paper Trading:** Simulate trades using live market LTP (Last Traded Price) without financial risk.
*   **Historical Backtesting:** Validate strategies against years of historical data with Black-Scholes Greek modeling.

### 🛠 Strategy Engine
*   **ORB (Opening Range Breakout):** Specialized logic for Nifty breakouts with configurable timeframes.
*   **Fibonacci Trailing SL:** Intelligent exit logic that locks in profits using Fibonacci-based retracement levels on the underlying index.
*   **Dynamic Strike Selection:** Automatically identifies ATM (At-The-Money) strikes for Calls and Puts.
*   **Brokerage Estimation:** Real-time calculation of STT, GST, and exchange charges for accurate Net P&L.

### 📊 Dashboard & Analytics
*   **Real-time Charts:** Integrated TradingView-style charts for NIFTY and selected Options contracts.
*   **Strategy Builder:** No-code UI to adjust targets, stop-losses, and trading windows.
*   **Trade Log:** Comprehensive history of all trades with gross/net P&L and exit reasons.
*   **NSE Screener:** Technical analysis (RSI, Moving Averages) for 200+ NSE stocks with sector-wise classification.
*   **Watchlist:** Personalised monitoring for your favorite equity symbols.

## 🏗 Architecture
- **Backend:** Python 3.11+, Flask, SQLAlchemy (Core Engine)
- **Frontend:** HTML5, Vanilla CSS, JavaScript (Lightweight Charts)
- **Database:** SQLite/PostgreSQL
- **API Integration:** Zerodha KiteConnect, yfinance

## 🛠 Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone <repo-url>
   cd tradingStratergy
   ```

2. **Configure Environment:**
   Create a `.env` file based on `.env.example`:
   ```env
   KITE_API_KEY=your_api_key
   KITE_API_SECRET=your_api_secret
   JWT_SECRET_KEY=your_secret
   APP_MODE=PAPER  # Options: LIVE, PAPER, BACKTEST
   ```

3. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the Application:**
   ```bash
   python main.py
   ```
   Access the dashboard at `http://localhost:5001`.

## 📂 Project Structure
- `core/`: Strategy logic and engine management.
- `execution/`: Real-time trading and order routing.
- `backtesting/`: Historical simulation engine.
- `dashboard/`: Flask routes and web templates.
- `db/`: Database models and persistence layer.
- `config/`: App-wide settings and constants.

## 🛡 Security
- **JWT Authentication:** Secure user sessions.
- **Encrypted Tokens:** Sensitive Kite access tokens are encrypted before storage.
- **Isolated State:** Each user runs an independent trading engine instance.

---
*Disclaimer: Trading in the stock market involves risk. This software is for educational and tools purposes. Always test strategies in Paper mode before going live.*
