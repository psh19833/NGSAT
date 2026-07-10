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

    def get_win_rate(self, date_str: str) -> float:
        """Calculate win rate for a date: profitable sells / total sells."""
        trades = self.get_trades_by_date(date_str)
        sells = [t for t in trades if t.side == "sell"]
        if not sells:
            return 0.0
        # 승리 매도: STOP_LOSS가 아닌 매도 (익절/일반 매도)
        wins = sum(1 for t in sells if t.action not in ("stop_loss",))
        return round(wins / len(sells) * 100, 1)

    def get_daily_pnl(self) -> list[dict]:
        """Calculate realized P&L grouped by date (FIFO matching).

        Returns:
            List of dicts with date, trade_count, realized_pnl, fee_estimate,
            net_pnl, win_rate, and individual trades.
        """
        from collections import defaultdict

        all_trades = self.get_recent_trades(limit=10000)
        if not all_trades:
            return []

        # Group trades by date
        by_date: dict[str, list] = defaultdict(list)
        for t in all_trades:
            d = t.created_at[:10] if hasattr(t, 'created_at') and t.created_at else t.date[:10]
            by_date[d].append(t)

        result = []
        for date in sorted(by_date.keys()):
            trades = by_date[date]
            # Match buys and sells by code (FIFO within same date)
            buys: dict[str, list[dict]] = defaultdict(list)
            total_pnl = 0
            total_sell_amount = 0
            sells_count = 0

            for t in trades:
                if t.side == "buy":
                    buys[t.code].append({"qty": t.quantity, "price": t.price})
                elif t.side == "sell":
                    sell_qty = t.quantity
                    sell_price = t.price
                    total_sell_amount += t.amount
                    sells_count += 1
                    # Match against buys (FIFO)
                    buy_list = buys.get(t.code, [])
                    remaining = sell_qty
                    while remaining > 0 and buy_list:
                        b = buy_list[0]
                        match_qty = min(remaining, b["qty"])
                        pnl = (sell_price - b["price"]) * match_qty
                        total_pnl += pnl
                        remaining -= match_qty
                        b["qty"] -= match_qty
                        if b["qty"] <= 0:
                            buy_list.pop(0)

            fee_est = round(total_sell_amount * -0.00195, 0)  # 0.18% tax + 0.015% fee
            net_pnl = round(total_pnl + fee_est, 0)

            sells = [t for t in trades if t.side == "sell"]
            wins = sum(1 for t in sells if t.action not in ("stop_loss",))
            win_rate = round(wins / len(sells) * 100, 1) if sells else 0.0

            result.append({
                "date": date,
                "trade_count": len(trades),
                "realized_pnl": round(total_pnl, 0),
                "fee_estimate": fee_est,
                "net_pnl": net_pnl,
                "win_rate": win_rate,
                "trades": [
                    {"code": t.code, "name": t.name, "side": t.side,
                     "qty": t.quantity, "price": t.price, "amount": t.amount,
                     "action": t.action}
                    for t in sorted(trades, key=lambda x: x.created_at)
                ],
            })

        return result

    def get_trades_by_code(self, code: str, limit: int = 50) -> list[TradeRecord]:
        """Get recent trades for a specific stock code."""
        return (
            self._session.query(TradeRecord)
            .filter(TradeRecord.code == code)
            .order_by(TradeRecord.created_at.desc())
            .limit(limit)
            .all()
        )

    def get_recent_trades(self, limit: int = 50, offset: int = 0) -> list[TradeRecord]:
        """Get most recent trades across all stocks with pagination."""
        return (
            self._session.query(TradeRecord)
            .order_by(TradeRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count_trades(self) -> int:
        """Get total number of trade records."""
        from sqlalchemy import func
        return self._session.query(func.count(TradeRecord.id)).scalar() or 0


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

    def update_position_quantity(
        self,
        code: str,
        sold_quantity: int,
    ) -> Optional[PositionRecord]:
        """Reduce position quantity after a partial sell (부분 청산)."""
        pos = self.get_position_by_code(code)
        if pos:
            pos.quantity = max(0, pos.quantity - sold_quantity)
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

        SQLite INSERT OR IGNORE를 사용하여 대량 삽입.
        루프 기반 flush/rollback보다 수십 배 빠름.

        Returns:
            실제 저장된 개수 (중복 제외).
        """
        if not bars:
            return 0
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(MinuteDataCache).values(bars)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["code", "date", "time"]
        )
        result = self._session.execute(stmt)
        self._session.commit()
        return result.rowcount

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
