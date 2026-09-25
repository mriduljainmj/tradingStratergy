"""
equity_engine.py — Phase-2 multi-strategy: lightweight per-strategy engine
for EQUITY strategies (cash segment), run in parallel with the ORB options
engine. One EquityEngine instance = one strategy on one stock.

Supported engine_type values (Strategy.engine_type):
  EQUITY_ORB — opening-range breakout on the stock: long above OR high,
               initial SL at OR low (or sl_pct), target tgt_pct, EOD square-off.
  EMA_CROSS  — long when EMA(fast) crosses above EMA(slow) on 1-min closes,
               exit on cross-down / stop / target / EOD.

Rules JSON (Strategy.rules) keys — all optional:
  qty (shares, default 1), sl_pct (default 1.0), tgt_pct (default 2.0),
  ema_fast (9), ema_slow (21), eod_exit ("15:15"), slippage_pct (0.0005)

Paper mode fills at LTP ± slippage. Live mode places NSE MIS market orders.
Completed trades are saved with position_type "LONG", the share prices in
entry_prem/exit_prem, and tagged with strategy_id/strategy_name.
"""
import datetime
import logging
import threading
import time

from execution.notify import send_trade_alert

logger = logging.getLogger(__name__)
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _now():
    return datetime.datetime.now(tz=_IST)


def equity_intraday_charges(buy_val: float, sell_val: float) -> float:
    """Zerodha intraday equity round-trip charges (₹)."""
    turnover  = buy_val + sell_val
    brokerage = min(buy_val * 0.0003, 20) + min(sell_val * 0.0003, 20)
    stt       = sell_val * 0.00025
    exch      = turnover * 0.0000297
    sebi      = turnover * 0.000001
    stamp     = buy_val * 0.00003
    gst       = (brokerage + exch + sebi) * 0.18
    return round(brokerage + stt + exch + sebi + stamp + gst, 2)


class EquityEngine:
    """Runs one equity strategy on one symbol until stopped or day done."""

    def __init__(self, strat: dict, broker, user_id: int):
        self.sid      = strat["id"]
        self.name     = strat["name"]
        self.symbol   = (strat.get("symbol") or "RELIANCE").upper()
        self.etype    = strat.get("engine_type") or "EQUITY_ORB"
        rules         = strat.get("rules") or {}
        self.qty      = int(rules.get("qty", 1))
        self.sl_pct   = float(rules.get("sl_pct", 1.0)) / 100
        self.tgt_pct  = float(rules.get("tgt_pct", 2.0)) / 100
        self.ema_fast = int(rules.get("ema_fast", 9))
        self.ema_slow = int(rules.get("ema_slow", 21))
        self.direction = (rules.get("direction") or "LONG").upper()      # LONG | SHORT
        self.max_loss  = float(rules.get("max_daily_loss", 0) or 0)      # ₹ cap, 0 = off
        self.slip     = float(rules.get("slippage_pct", 0.0005))
        try:
            h, m = str(rules.get("eod_exit", "15:15")).split(":")[:2]
            self.eod = datetime.time(int(h), int(m))
        except Exception:
            self.eod = datetime.time(15, 15)

        self.broker   = broker
        self.user_id  = user_id
        self.paper    = True
        self._stop    = threading.Event()
        self._thread  = None

        # Live view for the dashboard
        self.status     = "idle"
        self.ltp        = 0.0
        self.in_pos     = False
        self.entry_px   = 0.0
        self.live_pnl   = 0.0
        self.done       = False   # traded & closed for today
        self.last_error = ""
        self.execution_blocked = False

        # strategy internals
        self._or_high = 0.0
        self._or_low  = 0.0
        self._closes: list = []      # 1-min closes for EMA
        self._minute_ts = 0
        self._stop_px   = 0.0
        self._target_px = 0.0
        self._manual = ""   # "ENTER" | "EXIT" (one-shot from dashboard)

    def manual(self, action: str):
        self._manual = action.upper()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self, paper: bool = True):
        if self._thread and self._thread.is_alive():
            return
        self.paper = paper
        self._stop.clear()
        self.done = False
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=f"EqEng-{self.sid}-{self.symbol}"
        )
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError('Equity engine is still stopping. Try again shortly.')
        self.status = "stopped"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> dict:
        return {
            "strategy_id": self.sid, "name": self.name, "symbol": self.symbol,
            "engine_type": self.etype, "mode": "LIVE" if not self.paper else "PAPER",
            "status": self.status, "ltp": self.ltp, "in_position": self.in_pos,
            "entry_px": self.entry_px, "live_pnl": self.live_pnl,
            "stop_px": self._stop_px, "target_px": self._target_px,
            "qty": self.qty, "direction": self.direction,
            "or_high": self._or_high, "or_low": self._or_low,
            "execution_blocked": self.execution_blocked,
            "running": self.running, "done": self.done, "error": self.last_error,
        }

    # ── engine loop ───────────────────────────────────────────────────────────

    def _run(self):
        """Day-spanning loop: once started, the engine works every trading day
        until the user presses Stop — overnight and on weekends it sleeps,
        then resets its day state and re-arms at the next open."""
        try:
            self.status = "backfilling"
            self._backfill()
            key = f"NSE:{self.symbol}"
            self.status = "watching"
            while not self._stop.is_set():
                now = _now()
                t   = now.time()
                if self.execution_blocked:
                    self._nap(5)
                    continue
                if now.weekday() >= 5 or t > datetime.time(15, 30):
                    if self.in_pos:
                        self._exit(self.ltp, "Market Close")
                    if self.in_pos or self.execution_blocked:
                        self._nap(5)
                        continue
                    self._sleep_until_open("market closed — resumes at 09:15")
                    continue
                if t < datetime.time(9, 15):
                    self.status = "awaiting open"
                    self._nap(15)
                    continue
                try:
                    ltp = self.broker.get_ltp(key)
                except Exception as e:
                    self.last_error = str(e)[:120]
                    time.sleep(3)
                    continue
                self.ltp = ltp
                self._on_tick(ltp, t)
                if self.done:
                    self._sleep_until_open("done for today — resumes at 09:15")
                    continue
                time.sleep(2)   # equity pace: every 2 s is plenty
        except Exception as e:
            logger.exception(f"EquityEngine {self.sid} crashed")
            self.last_error = str(e)[:200]
            self.status = "error"

    def _nap(self, secs: int):
        """Sleep in 1 s slices so Stop stays responsive."""
        for _ in range(secs):
            if self._stop.is_set():
                return
            time.sleep(1)

    def _sleep_until_open(self, status: str):
        """Park until the next trading session opens, then reset day state."""
        self.status = status
        while not self._stop.is_set():
            now = _now()
            if now.weekday() < 5 and datetime.time(9, 14) <= now.time() <= datetime.time(15, 30):
                break
            self._nap(30)
        if self._stop.is_set():
            return
        self._reset_day()
        self.status = "watching"

    def _reset_day(self):
        """Fresh slate for a new session: OR, EMA history, done flag."""
        if self.in_pos or self.execution_blocked:
            return
        self._or_high = 0.0
        self._or_low  = 0.0
        self._closes  = []
        self._minute_ts = 0
        self.done     = False
        self.live_pnl = 0.0
        self.entry_px = 0.0
        self._backfill()   # (mostly empty at open; OR builds live)

    def _backfill(self):
        """Load today's 1-min candles to seed OR window and EMA history."""
        try:
            token = None
            from dashboard.routes import _nse_inst_cache
            for inst in (_nse_inst_cache.get("data") or []):
                if inst.get("tradingsymbol") == self.symbol and inst.get("segment") == "NSE":
                    token = inst["instrument_token"]
                    break
            if not token:
                insts = self.broker.kite.instruments("NSE")
                for inst in insts:
                    if inst.get("tradingsymbol") == self.symbol and inst.get("segment") == "NSE":
                        token = inst["instrument_token"]
                        break
            if not token:
                self.last_error = f"{self.symbol}: NSE token not found"
                return
            today = _now().date()
            recs = self.broker.kite.historical_data(
                token, f"{today} 09:15:00", _now().strftime("%Y-%m-%d %H:%M:%S"), "minute")
            for r in recs:
                ts = r["date"].time() if hasattr(r["date"], "time") else None
                if ts and ts < datetime.time(9, 35):
                    self._or_high = max(self._or_high, r["high"])
                    self._or_low  = min(self._or_low or r["low"], r["low"])
                self._closes.append(r["close"])
            self._closes = self._closes[-400:]
            if recs:
                self.ltp = recs[-1]["close"]
        except Exception as e:
            self.last_error = f"backfill: {str(e)[:100]}"

    def _ema(self, period: int):
        if len(self._closes) < period:
            return None
        k = 2 / (period + 1)
        ema = sum(self._closes[:period]) / period
        for c in self._closes[period:]:
            ema = c * k + ema * (1 - k)
        return ema

    def _on_tick(self, ltp: float, t: datetime.time):
        if self.execution_blocked:
            return
        # keep 1-min close series current
        m = int(time.time() // 60)
        if m != self._minute_ts:
            self._minute_ts = m
            self._closes.append(ltp)
            self._closes = self._closes[-400:]
        else:
            if self._closes:
                self._closes[-1] = ltp

        if t < datetime.time(9, 35):
            self._or_high = max(self._or_high, ltp)
            self._or_low  = min(self._or_low or ltp, ltp)

        m = self._manual
        if m:
            self._manual = ""
            if m == "EXIT" and self.in_pos:
                self._exit(ltp, "Manual Exit"); return
            if m == "ENTER" and not self.in_pos and not self.done:
                self._enter(ltp); return

        if self.in_pos:
            sign = 1 if self.direction == "LONG" else -1
            self.live_pnl = round((ltp - self.entry_px) * self.qty * sign, 2)
            stop_hit   = ltp <= self._stop_px   if sign == 1 else ltp >= self._stop_px
            target_hit = ltp >= self._target_px if sign == 1 else ltp <= self._target_px
            if stop_hit:
                self._exit(self._stop_px, "Stop Loss Hit")
            elif target_hit:
                self._exit(self._target_px, "Target Hit")
            elif t >= self.eod:
                self._exit(ltp, "EOD Square-off")
            elif self.etype == "EMA_CROSS":
                ef, es = self._ema(self.ema_fast), self._ema(self.ema_slow)
                if ef is not None and es is not None and (ef < es if sign == 1 else ef > es):
                    self._exit(ltp, "EMA Cross Exit")
            return

        if self.done or t >= self.eod:
            self.status = "done" if self.done else "entry window over"
            return

        # ── entries ───────────────────────────────────────────────────────────
        if self.max_loss > 0 and self._todays_strategy_pnl() <= -self.max_loss:
            self.status = "risk guard: daily loss cap hit"
            self.done = True
            send_trade_alert(
                f"⛔ <b>{self.name}</b>: daily loss cap ₹{self.max_loss:,.0f} hit — "
                f"strategy stopped for the day.")
            return
        long_mode = self.direction == "LONG"
        if self.etype == "EQUITY_ORB":
            if self._or_high > 0 and t >= datetime.time(9, 35):
                if long_mode and ltp > self._or_high:
                    self._enter(ltp)
                elif not long_mode and self._or_low and ltp < self._or_low:
                    self._enter(ltp)
        elif self.etype == "EMA_CROSS":
            ef, es = self._ema(self.ema_fast), self._ema(self.ema_slow)
            if ef is not None and es is not None:
                if (ef > es) if long_mode else (ef < es):
                    self._enter(ltp)

    # ── fills ─────────────────────────────────────────────────────────────────

    def _todays_strategy_pnl(self) -> float:
        """Sum of today's saved net P&L for THIS strategy (₹)."""
        try:
            from db.database import SessionLocal
            from db.models import Trade
            from sqlalchemy import func
            db = SessionLocal()
            try:
                total = (db.query(func.coalesce(func.sum(Trade.net_pnl), 0.0))
                         .filter(Trade.user_id == self.user_id,
                                 Trade.strategy_id == self.sid,
                                 Trade.date == _now().date())
                         .scalar())
                return float(total or 0.0)
            finally:
                db.close()
        except Exception:
            return 0.0

    def _enter(self, px: float):
        if self.execution_blocked or self.in_pos or px <= 0:
            return
        sign = 1 if self.direction == "LONG" else -1
        fill = round(px * (1 + sign * self.slip), 2)
        if not self.paper:
            try:
                txn = (self.broker.kite.TRANSACTION_TYPE_BUY if sign == 1
                       else self.broker.kite.TRANSACTION_TYPE_SELL)
                from execution.order_safety import confirmed_order
                fill = confirmed_order(self.broker, self.user_id, self.symbol, txn,
                                       self.qty, equity=True, strategy_id=self.sid)
            except Exception as e:
                self.last_error = f"order: {str(e)[:100]}"
                self.status = "order needs review — execution paused"
                self.execution_blocked = True
                return
        self.in_pos   = True
        self.entry_px = fill
        self._entry_ts = _now()
        if sign == 1:
            base_sl = self._or_low if (self.etype == "EQUITY_ORB" and self._or_low) else fill * (1 - self.sl_pct)
            self._stop_px   = round(max(base_sl, fill * (1 - self.sl_pct)), 2)
            self._target_px = round(fill * (1 + self.tgt_pct), 2)
        else:
            base_sl = self._or_high if (self.etype == "EQUITY_ORB" and self._or_high) else fill * (1 + self.sl_pct)
            self._stop_px   = round(min(base_sl, fill * (1 + self.sl_pct)), 2)
            self._target_px = round(fill * (1 - self.tgt_pct), 2)
        self.status = f"{self.direction} @ ₹{fill}"
        logger.info(f"[{self.name}] BUY {self.symbol} x{self.qty} @ ₹{fill} "
                    f"SL {self._stop_px} TGT {self._target_px}")
        send_trade_alert(
            f"🟢 <b>{'BUY' if sign == 1 else 'SHORT'} {self.symbol}</b> ×{self.qty} ({'📋 Paper' if self.paper else '🔴 LIVE'})\n"
            f"Strategy: {self.name}\nEntry ₹{fill:,.2f} · SL ₹{self._stop_px:,.2f} · "
            f"Target ₹{self._target_px:,.2f}")

    def _exit(self, px: float, reason: str):
        if self.execution_blocked or not self.in_pos or px <= 0:
            return
        sign = 1 if self.direction == "LONG" else -1
        fill = round(px * (1 - sign * self.slip), 2)
        if not self.paper:
            try:
                txn = (self.broker.kite.TRANSACTION_TYPE_SELL if sign == 1
                       else self.broker.kite.TRANSACTION_TYPE_BUY)
                from execution.order_safety import confirmed_order
                fill = confirmed_order(self.broker, self.user_id, self.symbol, txn,
                                       self.qty, equity=True, strategy_id=self.sid)
            except Exception as e:
                self.last_error = f"exit order: {str(e)[:100]}"
                self.status = "exit needs review — position retained"
                self.execution_blocked = True
                return
        gross   = round((fill - self.entry_px) * self.qty * sign, 2)
        charges = equity_intraday_charges(self.entry_px * self.qty, fill * self.qty)
        net     = round(gross - charges, 2)
        self.in_pos = False
        self.done   = True
        self.live_pnl = 0.0
        self.status = f"done · {reason} · ₹{net:+,.2f}"
        logger.info(f"[{self.name}] EXIT {self.symbol} @ ₹{fill} ({reason}) net ₹{net}")
        try:
            from db.helpers import save_completed_trade
            save_completed_trade(
                user_id=self.user_id,
                trade_mode="LIVE" if not self.paper else "PAPER",
                date=_now().date(),
                position_type=self.direction,
                entry_prem=self.entry_px, exit_prem=fill,
                strike=None, quantity=self.qty,
                gross_pnl=gross, charges=charges, net_pnl=net,
                exit_reason=reason, symbol=self.symbol,
                entry_time=getattr(self, "_entry_ts", None) and self._entry_ts.replace(tzinfo=None),
                exit_time=_now().replace(tzinfo=None),
                strategy_id=self.sid, strategy_name=self.name,
            )
        except Exception:
            logger.exception("equity trade save failed")
        send_trade_alert(
            f"{'✅' if net >= 0 else '🔻'} <b>EXIT {self.symbol}</b> "
            f"({'📋 Paper' if self.paper else '🔴 LIVE'})\n{reason} @ ₹{fill:,.2f}\n"
            f"Net P&L: <b>{'+' if net >= 0 else ''}₹{net:,.2f}</b>")
