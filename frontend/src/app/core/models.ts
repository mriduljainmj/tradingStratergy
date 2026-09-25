export interface User {
  id: number;
  username: string;
  email: string;
  display_name: string;
  bio: string;
  is_admin: boolean;
  broker_id: string;
  has_kite_token: boolean;
  kite_api_key_stored: string;
  trade_confirm_modal: boolean;
  photo_base64: string;
}
export interface Strategy {
  id: number;
  name: string;
  description: string;
  instrument_type: 'OPTIONS' | 'EQUITY';
  symbol: string;
  engine_type: string;
  rules: Record<string, any>;
  is_active: boolean;
  is_running: boolean;
}
export interface Trade {
  id: number;
  date: string;
  symbol: string;
  trade_mode: string;
  position_type: string;
  quantity: number;
  entry_prem: number;
  exit_prem: number;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  exit_reason: string;
  strategy_name: string;
}
export interface Candle {
  time: any;
  open: number;
  high: number;
  low: number;
  close: number;
}
export interface Summary {
  total_trades: number;
  total_net_pnl: number;
  win_rate: number;
  profit_factor: number | null;
  max_drawdown: number;
  total_charges: number;
  avg_trade: number;
  sharpe_ratio: number;
}
export interface ExecutionEvent {
  id: string;
  mode: 'PAPER' | 'LIVE';
  action: 'BUY' | 'SELL';
  symbol: string;
  quantity: number;
  price: number;
  reason: string;
  time: string;
}
export interface MarketState {
  option_token?: number;
  execution_events?: ExecutionEvent[];
  app_mode: string;
  status: string;
  kite_auth_error: boolean;
  engine_running: boolean;
  in_position: boolean;
  execution_blocked: boolean;
  trades_enabled: boolean;
  background_trading: boolean;
  trade_direction: string;
  position_type: string;
  live_nifty_ltp: number;
  live_option_price: number;
  live_pnl: number;
  balance: number;
  qty: number;
  or_high: number;
  or_low: number;
  entry_prem: number;
  net_pnl: number;
  option_label: string;
  option_prices: any[];
  candles: Candle[];
  candles_1m: Candle[];
  logs: string[];
  equity_engines: any[];
}
export interface Stock {
  symbol: string;
  name?: string;
  company_name?: string;
  sector: string;
  ltp: number | null;
  change_pct: number | null;
  volume: number | null;
  rsi14?: number;
  list_name?: string;
}
