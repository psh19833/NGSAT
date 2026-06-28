"""NGSAT data repository — persistent storage operations.

Repository pattern: all database reads/writes go through here.
Keeps data access logic separate from business logic.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from core.logger import logger
from core.models import (
    DailyReport,
    MarketDataCache,
    MinuteDataCache,
    PositionRecord,
    SystemEvent,
    TradeRecord,
)
from core.types import DecisionAction, OrderSide

_SYNTHETIC_NAME_RE = re.compile(r"^synthetic_\d+$")


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
            ValueError: If reason is empty, or if synthetic data detected.
        """
        if not reason or not reason.strip():
            raise ValueError("Trade reason is mandatory — no decision without a reason")

        # ── Synthetic data guard ──
        if _SYNTHETIC_NAME_RE.match(name):
            logger.error(f"합성 데이터 차단: {name}({code}) — 실거래만 저장 가능")
            raise ValueError(f"Synthetic data blocked: {name}({code})")

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

    def get_recent_trades(self, limit: int = 50) -> list[TradeRecord]:
        """Get most recent trades across all stocks."""
        return (
            self._session.query(TradeRecord)
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


class MinuteDataRepository:
    """분봉 데이터 저장소 — KIS 분봉 수집 및 조회."""

    def __init__(self, session: Session):
        self._session = session

    def save_minute_bar(self, **kwargs) -> MinuteDataCache:
        """분봉 1개 저장. 중복(code+date+time) 시 무시."""
        try:
            record = MinuteDataCache(**kwargs)
            self._session.add(record)
            self._session.flush()
            return record
        except IntegrityError:
            self._session.rollback()
            # 중복 키면 무시 (정상 — 이미 수집된 분봉)
            return None

    def save_minute_bars(self, bars: list[dict]) -> int:
        """여러 분봉을 한 번에 저장. 중복은 건너뜀.

        Returns:
            실제 저장된 개수 (중복 제외).
        """
        saved = 0
        for bar in bars:
            try:
                record = MinuteDataCache(**bar)
                self._session.add(record)
                self._session.flush()
                saved += 1
            except IntegrityError:
                self._session.rollback()
                # 중복 = 정상, 다음으로
        if saved > 0:
            self._session.commit()
        return saved

    def get_minute_bars(
        self, code: str, date: str,
    ) -> list[MinuteDataCache]:
        """특정 종목·특정일의 분봉 데이터를 시간순 조회."""
        return (
            self._session.query(MinuteDataCache)
            .filter(
                MinuteDataCache.code == code,
                MinuteDataCache.date == date,
            )
            .order_by(MinuteDataCache.time)
            .all()
        )

    def get_distinct_dates(self, code: str | None = None) -> list[str]:
        """수집된 분봉 날짜 목록."""
        q = self._session.query(MinuteDataCache.date).distinct()
        if code:
            q = q.filter(MinuteDataCache.code == code)
        rows = q.order_by(MinuteDataCache.date).all()
        return [r[0] for r in rows]

    def get_collected_codes(self) -> list[str]:
        """분봉이 수집된 종목코드 목록."""
        rows = (
            self._session.query(MinuteDataCache.code)
            .distinct()
            .order_by(MinuteDataCache.code)
            .all()
        )
        return [r[0] for r in rows]
