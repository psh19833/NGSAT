"""NGSAT common types and enums.

Shared across all modules to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

# Re-export enums from config for convenience
from core.config import Environment, MarketRegime, OrderSide, OrderStatus

KST = timedelta(hours=9)


def now_kst() -> datetime:
    """Return current datetime in KST (Korea Standard Time)."""
    return datetime.now(timezone.utc) + KST


class Market(str, Enum):
    """Korean stock market."""
    KOSPI = "kospi"
    KOSDAQ = "kosdaq"


class DecisionAction(str, Enum):
    """Trading decision action types.

    Every action MUST have a reason — no exceptions.
    """
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    FORCE_SELL = "force_sell"      # 강제 매도
    FORCE_HOLD = "force_hold"       # 강제 홀드
    STOP_LOSS = "stop_loss"         # 손절
    NONE = "none"                   # 관망


class TradingMode(str, Enum):
    """Trading mode — live vs backtest must never mix."""
    LIVE = "live"
    BACKTEST = "backtest"


class StrategyMode(str, Enum):
    """매매 전략 모드 (하이브리드 2단계).

    SWING: 며칠~몇 주 보유 (일봉 ML)
    SHORT_TERM: 당일 진입/청산 (분봉 ML)
    HOLD: 신규 진입 금지, 기존 포지션만 청산
    """
    SWING = "swing"
    SHORT_TERM = "short_term"
    HOLD = "hold"


def is_market_hours(dt: datetime | None = None) -> bool:
    """Check if the Korean stock market is currently in trading hours.

    KOSPI/KOSDAQ trading hours: 평일 09:00 ~ 15:30 KST.

    Args:
        dt: Datetime to check (None = now).

    Returns:
        True if within market hours.
    """
    from datetime import timezone, timedelta
    now = dt or datetime.now()
    # Convert to KST (UTC+9)
    kst = now.astimezone(timezone(timedelta(hours=9)))
    weekday = kst.weekday()
    if weekday >= 5:  # Saturday=5, Sunday=6
        return False
    hour = kst.hour
    minute = kst.minute
    if hour < 9 or hour > 15:
        return False
    if hour == 15 and minute > 30:
        return False
    return True


@dataclass
class DecisionReason:
    """Mandatory reason for every trading decision.

    NGSAT core principle: NO decision without a reason.
    This object is stored in the database and included in notifications.
    """
    action: DecisionAction
    reason: str                        # Human-readable reason (Korean)
    evidence: dict[str, Any] = field(default_factory=dict)  # Quantitative evidence
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        if not self.reason or not self.reason.strip():
            raise ValueError("DecisionReason.reason cannot be empty — every decision needs a reason")


@dataclass
class StockInfo:
    """Basic stock information."""
    code: str                          # 종목코드 (6-digit)
    name: str                          # 종목명
    market: Market                     # 코스피/코스닥
    sector: str = ""                   # 업종 코드/명


@dataclass
class PriceData:
    """Real-time or historical price data."""
    code: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    change_pct: float = 0.0            # 전일 대비 등락률


@dataclass
class Position:
    """Current holding position."""
    code: str
    name: str
    quantity: int
    buy_price: float                   # 평단가
    current_price: float
    market: Market
    buy_amount: float                  # 총 매수 금액
    eval_amount: float                 # 평가 금액
    profit_loss: float                 # 평가 손익
    profit_loss_pct: float             # 평가 손익률 (%)
    stop_loss_pct: float               # 현재 적용 중인 손절선 (%)
    stop_loss_reason: Optional[str] = None  # 손절선 조정 시 근거
    sector: str = ""                   # 업종 (TR-5: 섹터 집중도 체크용)
    # ── 트레일링 스탑 (P1-1) ──
    trailing_stop_price: Optional[float] = None       # 현재 트레일링 스탑 가격
    trailing_stop_high_water: Optional[float] = None  # 보유 중 최고가
    # ── 부분 청산 (P1-2) ──
    partial_tp1_executed: bool = False                 # 1차 익절 완료 여부
    partial_tp2_executed: bool = False                 # 2차 익절 완료 여부
    original_quantity: Optional[int] = None            # 최초 매수 수량 (잔량 계산용)


@dataclass
class AccountSummary:
    """Account overview."""
    total_asset: float                 # 총 자산
    deposit: float                     # 예수금
    total_eval: float                  # 총 평가 금액
    total_profit_loss: float           # 총 평가 손익
    total_profit_loss_pct: float       # 총 평가 손익률 (%)
    daily_loss: float = 0.0            # 당일 손실액
    daily_loss_pct: float = 0.0        # 당일 손실률 (%)


@dataclass
class UnfilledOrder:
    """미체결 주문 정보."""
    code: str                          # 종목코드
    name: str                          # 종목명
    side: str                          # buy / sell
    quantity: int                      # 미체결 수량
    price: float                       # 주문 가격
    order_id: str                      # KIS 주문번호
    order_time: str                    # 주문 시각 (HHMMSS)
    order_dvsn: str = "00"             # 00=지정가, 01=시장가
    is_trading_halted: bool = False    # 매매 중단 여부


@dataclass
class MinuteScore:
    """분봉 스크리닝 결과 (단일 종목)."""
    code: str
    name: str
    score: float                       # 0~100 종합 점수
    minute_rsi: float = 50.0           # 분봉 RSI (14)
    momentum_5m: float = 0.0           # 5분 등락률 (%)
    volume_spike: float = 1.0          # 거래량 급등 비율
    volatility_pct: float = 0.0        # 분봉 변동성 (%)
    reasons: list[str] = field(default_factory=list)
