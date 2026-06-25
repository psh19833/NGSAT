"""NGSAT data repository — persistent storage operations.

Repository pattern: all database reads/writes go through here.
Keeps data access logic separate from business logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from core.models import (
    DailyReport,
    MarketDataCache,
    PositionRecord,
    SystemEvent,
    TradeRecord,
)
from core.types import DecisionAction, OrderSide


class TradeRepository:
    """Trade record storage and retrieval."""

    def __init__(self, session: Session):
        self._session = session

    def save_trade(
        self,
        code: str,
        name: str,
        side: OrderSide,
        quantity: int,
        price: float,
        amount: float,
        action: DecisionAction,
        reason: str,
        evidence: dict | None = None,
        mode: str = "live",
        position_id: int | None = None,
    ) -> TradeRecord:
        """Save a trade record with mandatory decision reason.
        
        Raises:
            ValueError: If reason is empty.
        """
        if not reason or not reason.strip():
            raise ValueError("Trade reason is mandatory — no decision without a reason")

        record = TradeRecord(
            code=code,
            name=name,
            side=side.value,
            quantity=quantity,
            price=price,
            amount=amount,
            action=action.value,
            reason=reason,
            evidence=evidence,
            mode=mode,
            position_id=position_id,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_trades_by_date(self, date_str: str) -> list[TradeRecord]:
        """Get all trades for a specific date (YYYY-MM-DD)."""
        return (
            self._session.query(TradeRecord)
            .filter(TradeRecord.created_at.startswith(date_str))
            .order_by(TradeRecord.created_at)
            .all()
        )

    def get_trades_by_code(self, code: str, limit: int = 50) -> list[TradeRecord]:
        """Get recent trades for a specific stock code."""
        return (
            self._session.query(TradeRecord)
            .filter(TradeRecord.code == code)
            .order_by(TradeRecord.created_at.desc())
            .limit(limit)
            .all()
        )


class PositionRepository:
    """Position storage and retrieval."""

    def __init__(self, session: Session):
        self._session = session

    def save_position(self, **kwargs) -> PositionRecord:
        """Create or update a position record."""
        record = PositionRecord(**kwargs)
        self._session.add(record)
        self._session.flush()
        return record

    def get_open_positions(self) -> list[PositionRecord]:
        """Get all currently open positions."""
        return (
            self._session.query(PositionRecord)
            .filter(PositionRecord.status == "open")
            .all()
        )

    def get_position_by_code(self, code: str) -> Optional[PositionRecord]:
        """Get open position by stock code."""
        return (
            self._session.query(PositionRecord)
            .filter(PositionRecord.code == code, PositionRecord.status == "open")
            .first()
        )

    def close_position(
        self,
        code: str,
        final_profit_loss: float,
    ) -> Optional[PositionRecord]:
        """Mark a position as closed."""
        pos = self.get_position_by_code(code)
        if pos:
            pos.status = "closed"
            pos.closed_at = datetime.now()
            pos.final_profit_loss = final_profit_loss
            self._session.flush()
        return pos


class DailyReportRepository:
    """Daily report storage."""

    def __init__(self, session: Session):
        self._session = session

    def save_report(self, **kwargs) -> DailyReport:
        record = DailyReport(**kwargs)
        self._session.add(record)
        self._session.flush()
        return record

    def get_report_by_date(self, date_str: str) -> Optional[DailyReport]:
        return (
            self._session.query(DailyReport)
            .filter(DailyReport.date == date_str)
            .first()
        )


class SystemEventRepository:
    """System event log storage."""

    def __init__(self, session: Session):
        self._session = session

    def log_event(
        self,
        event_type: str,
        message: str,
        details: dict | None = None,
    ) -> SystemEvent:
        """Log a system event (start, stop, error, halt, etc.)."""
        record = SystemEvent(
            event_type=event_type,
            message=message,
            details=details,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_recent_events(self, limit: int = 20) -> list[SystemEvent]:
        return (
            self._session.query(SystemEvent)
            .order_by(SystemEvent.created_at.desc())
            .limit(limit)
            .all()
        )


class MarketDataRepository:
    """Market data cache storage."""

    def __init__(self, session: Session):
        self._session = session

    def save_price_data(self, **kwargs) -> MarketDataCache:
        record = MarketDataCache(**kwargs)
        self._session.add(record)
        self._session.flush()
        return record

    def get_price_history(
        self, code: str, start_date: str, end_date: str
    ) -> list[MarketDataCache]:
        return (
            self._session.query(MarketDataCache)
            .filter(
                MarketDataCache.code == code,
                MarketDataCache.date >= start_date,
                MarketDataCache.date <= end_date,
            )
            .order_by(MarketDataCache.date)
            .all()
        )
