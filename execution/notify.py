"""
notify.py — Telegram trade alerts.

Setup (one-time):
  1. Create a bot with @BotFather on Telegram → copy the bot token.
  2. Message your bot once, then open
     https://api.telegram.org/bot<TOKEN>/getUpdates to find your chat id.
  3. Add to .env:
       TELEGRAM_BOT_TOKEN=123456:ABC...
       TELEGRAM_CHAT_ID=987654321

Alerts are fire-and-forget on a daemon thread — a slow or down Telegram API
never blocks the trading loop. If the env vars are absent, alerts are simply
skipped (logged once at DEBUG).
"""
import json
import logging
import os
import threading
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)


def _send(msg: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat  = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        logger.debug("Telegram alert skipped — TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")
        return False
    try:
        url  = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode(
            {"chat_id": chat, "text": msg, "parse_mode": "HTML"}
        ).encode()
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data), timeout=10
        ) as resp:
            ok = json.loads(resp.read().decode()).get("ok", False)
            if not ok:
                logger.warning("Telegram API returned ok=false for trade alert.")
            return ok
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")
        return False


def send_trade_alert(msg: str):
    """Send msg to the configured Telegram chat without blocking the caller."""
    threading.Thread(target=_send, args=(msg,), daemon=True).start()
