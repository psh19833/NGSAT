"""NGSAT database models (SQLAlchemy).

All persistent data is stored here — trading history, decisions, account state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


class TradeRecord(Base):
    """매매 이력 — every buy/sell with mandatory reason.

    NGSAT core principle: every decision has a reason.
    """
    __tablename__ = "trade_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Trade details
    code: Mapped[str] = mapped_column(String(6), index=True)       # 종목코드
    name: Mapped[str] = mapped_column(String(50))                  # 종목명
    side: Mapped[str] = mapped_column(String(10))                  # buy / sell
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)                   # 총 거래 금액

    # Decision reason (MANDATORY)
    action: Mapped[str] = mapped_column(String(20))                # DecisionAction
    reason: Mapped[str] = mapped_column(Text)                      # 판단 근거 (한글)
    evidence: Mapped[Optional[dict]] = mapped_column(JSON)         # 정량적 근거

    # Metadata
    mode: Mapped[str] = mapped_column(String(10))                  # live / backtest
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    # Relations
    position_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("positions.id"), nullable=True
    )
    position: Mapped[Optional["PositionRecord"]] = relationship(back_populates="trades")


class PositionRecord(Base):
    """보유 포지션 기록."""
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    code: Mapped[str] = mapped_column(String(6), index=True)
    name: Mapped[str] = mapped_column(String(50))
    market: Mapped[str] = mapped_column(String(10))                # kospi / kosdaq
    quantity: Mapped[int] = mapped_column(Integer)
    buy_price: Mapped[float] = mapped_column(Float)
    buy_amount: Mapped[float] = mapped_column(Float)

    # Stop loss
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=3.0)
    stop_loss_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Force controls
    is_force_hold: Mapped[bool] = mapped_column(Boolean, default=False)

    # Status
    status: Mapped[str] = mapped_column(String(20), default="open")  # open / closed
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    final_profit_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Timestamps
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Relations
    trades: Mapped[list[TradeRecord]] = relationship(back_populates="position")


class DailyReport(Base):
    """일일 보고서 — 장 마감 후 생성."""
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), index=True)      # YYYY-MM-DD

    total_asset: Mapped[float] = mapped_column(Float)
    daily_loss: Mapped[float] = mapped_column(Float, default=0.0)
    daily_loss_pct: Mapped[float] = mapped_column(Float, default=0.0)

    trade_count: Mapped[int] = mapped_column(Integer, default=0)
    buy_count: Mapped[int] = mapped_column(Integer, default=0)
    sell_count: Mapped[int] = mapped_column(Integer, default=0)

    summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # 상세 요약
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MarketDataCache(Base):
    """시장 데이터 캐시 — 과거 가격 데이터."""
    __tablename__ = "market_data_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)      # YYYY-MM-DD
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)
    change_pct: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MinuteDataCache(Base):
    """분봉 데이터 캐시 — KIS 당일 분봉 저장.

    분봉 데이터는 대량이므로 code + date + time에 unique index를 건다.
    하루에 종목당 최대 390개(6.5시간×60분)까지 저장된다.
    """
    __tablename__ = "minute_data_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    date: Mapped[str] = mapped_column(String(10))           # YYYY-MM-DD
    time: Mapped[str] = mapped_column(String(8))             # HH:MM:SS
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(Integer)

    accumulated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("code", "date", "time", name="uq_minute_code_date_time"),
    )


class SystemEvent(Base):
    """시스템 이벤트 로그 — 가동 상태, 오류, 상태 변화."""
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(30), index=True)  # start/stop/error/halt/etc
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class SystemConfig(Base):
    """런타임 설정 저장소 (ConfigService). key-value 스토어."""
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now,
    )
