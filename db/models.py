import datetime
import json

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id            = Column(Integer, primary_key=True)
    email         = Column(String(255), unique=True, nullable=False, index=True)
    username      = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at    = Column(DateTime, default=datetime.datetime.utcnow)

    # Profile extras (nullable so existing rows are not broken)
    display_name        = Column(String(150), nullable=True)
    bio                 = Column(Text,        nullable=True)
    photo_base64        = Column(Text,        nullable=True)   # data:image/…;base64,…
    trade_confirm_modal = Column(Boolean,     default=True)    # show modal on trade exec
    broker_id           = Column(String(100), nullable=True)   # Zerodha client ID etc.

    # Per-user trading settings (JSON blob of TradingConfig field overrides)
    settings_json = Column(Text, nullable=True)

    # Admin flag — admin users can manage app-level Kite credentials
    is_admin = Column(Boolean, default=False)

    # Kite session persistence (serverless-safe; secrets are Fernet-encrypted)
    kite_api_key_stored   = Column(String(100), nullable=True)  # user's api_key
    kite_api_secret_enc   = Column(Text,        nullable=True)  # encrypted api_secret
    kite_access_token_enc = Column(Text,        nullable=True)  # encrypted access token
    kite_token_date       = Column(Date,        nullable=True)  # token valid for this date

    trades     = relationship("Trade",    back_populates="user", lazy="dynamic")
    strategies = relationship("Strategy", back_populates="user", lazy="dynamic")

    def to_dict(self):
        today = datetime.date.today()
        has_kite_token = bool(
            self.kite_access_token_enc
            and self.kite_token_date == today
        )
        return {
            "id":                 self.id,
            "email":              self.email,
            "username":           self.username,
            "display_name":       self.display_name or self.username,
            "bio":                self.bio or "",
            "photo_base64":       self.photo_base64 or "",
            "trade_confirm_modal": self.trade_confirm_modal if self.trade_confirm_modal is not None else True,
            "broker_id":          self.broker_id or "",
            "created_at":         self.created_at.isoformat() if self.created_at else None,
            "kite_api_key_stored": self.kite_api_key_stored or "",
            "has_kite_secret":    bool(self.kite_api_secret_enc),
            "has_kite_token":     has_kite_token,
            "kite_token_date":    self.kite_token_date.isoformat() if self.kite_token_date else None,
            "is_admin":           bool(self.is_admin),
        }


class Trade(Base):
    __tablename__ = "trades"

    id            = Column(Integer, primary_key=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date          = Column(Date,    nullable=False, index=True)
    trade_mode    = Column(String(10), default="PAPER", index=True)   # PAPER | LIVE
    symbol        = Column(String(50))
    position_type = Column(String(10))   # CALL | PUT
    entry_time    = Column(DateTime)
    exit_time     = Column(DateTime)
    entry_prem    = Column(Float)
    exit_prem     = Column(Float)
    strike        = Column(Integer)
    quantity      = Column(Integer)
    gross_pnl     = Column(Float)
    charges       = Column(Float)
    net_pnl       = Column(Float)
    exit_reason   = Column(String(50))   # Target Hit | Trailing SL Hit | EOD Force Close
    or_high       = Column(Float)
    or_low        = Column(Float)
    created_at    = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", back_populates="trades")

    def to_dict(self):
        return {
            "id":            self.id,
            "date":          self.date.isoformat() if self.date else None,
            "trade_mode":    self.trade_mode or "PAPER",
            "symbol":        self.symbol,
            "position_type": self.position_type,
            "entry_time":    self.entry_time.isoformat() if self.entry_time else None,
            "exit_time":     self.exit_time.isoformat() if self.exit_time else None,
            "entry_prem":    self.entry_prem,
            "exit_prem":     self.exit_prem,
            "strike":        self.strike,
            "quantity":      self.quantity,
            "gross_pnl":     self.gross_pnl,
            "charges":       self.charges,
            "net_pnl":       self.net_pnl,
            "exit_reason":   self.exit_reason,
            "or_high":       self.or_high,
            "or_low":        self.or_low,
        }


class Strategy(Base):
    __tablename__ = "strategies"

    id          = Column(Integer, primary_key=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name        = Column(String(200), nullable=False)
    description = Column(Text)
    rules       = Column(Text)          # JSON blob
    is_active   = Column(Boolean, default=False)
    created_at  = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.datetime.utcnow,
                         onupdate=datetime.datetime.utcnow)

    user = relationship("User", back_populates="strategies")

    def get_rules(self):

        return json.loads(self.rules) if self.rules else {}

    def set_rules(self, rules_dict):
        self.rules = json.dumps(rules_dict)

    def to_dict(self):
        return {
            "id":          self.id,
            "name":        self.name,
            "description": self.description,
            "rules":       self.get_rules(),
            "is_active":   self.is_active,
            "created_at":  self.created_at.isoformat() if self.created_at else None,
            "updated_at":  self.updated_at.isoformat() if self.updated_at else None,
        }


class Watchlist(Base):
    """Per-user stock watchlist — symbols the user wants to monitor."""
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),)

    id           = Column(Integer, primary_key=True)
    user_id      = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    symbol       = Column(String(30), nullable=False)
    company_name = Column(String(200))
    sector       = Column(String(100))
    added_at     = Column(DateTime, default=datetime.datetime.utcnow)

    user = relationship("User", backref="watchlist_items")

    def to_dict(self):
        return {
            "id":           self.id,
            "symbol":       self.symbol,
            "company_name": self.company_name or self.symbol,
            "sector":       self.sector or "",
            "added_at":     self.added_at.isoformat() if self.added_at else None,
        }
