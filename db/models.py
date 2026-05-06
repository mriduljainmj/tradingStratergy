import datetime
import json

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, ForeignKey,
    Integer, String, Text,
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

    trades     = relationship("Trade",    back_populates="user", lazy="dynamic")
    strategies = relationship("Strategy", back_populates="user", lazy="dynamic")

    def to_dict(self):
        return {"id": self.id, "email": self.email, "username": self.username,
                "created_at": self.created_at.isoformat() if self.created_at else None}


class Trade(Base):
    __tablename__ = "trades"

    id            = Column(Integer, primary_key=True)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date          = Column(Date,    nullable=False, index=True)
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
