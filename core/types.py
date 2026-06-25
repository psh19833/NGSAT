"""NGSAT common types and enums.

Shared across all modules to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

# Re-export enums from config for convenience
from core.config import Environment, MarketRegime, OrderSide, OrderStatus


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
    is_trading_halted: bool = False    # 매매 중단 여부
