"""NGSAT live risk management.

Enforces:
- Daily total loss limit (default -5% → auto halt)
- Per-position stop loss (default -3%, max -5% with justification)
- All stop-loss adjustments MUST have a reason
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.config import RiskConfig
from core.exceptions import RiskLimitHit
from core.logger import logger
from core.types import AccountSummary, DecisionAction, DecisionReason, Position


@dataclass
class RiskCheckResult:
    """Result of a risk check."""
    is_safe: bool                          # 거래 계속 가능?
    reason: str                            # 판단 근거
    action: DecisionAction                 # 권장 행동
    halt_trading: bool = False             # 매매 중단 필요?


class RiskManager:
    """Risk management for live trading.

    Supports mode-aware risk (하이브리드 2단계):
    - SWING mode: 기본 손절 -3%, 일일 -5% (기존)
    - SHORT_TERM mode: 더 타이트한 손절 -1.5%, 일일 -3%
    - HOLD mode: 신규 진입 금지, 기존 포지션만 청산

    Rules:
    1. Daily total loss ≥ limit → halt all trading
    2. Per-position loss ≥ stop loss → trigger stop loss
    3. Stop loss can be extended to max 5% IF there is a justified reason
    4. No reason = no extension
    """

    def __init__(self, config: RiskConfig, strategy_config=None):
        self._config = config
        self._halted = False
        self._halt_reason: Optional[str] = None
        self._mode: str = "swing"  # 기본 스윙 모드
        if strategy_config is None:
            from core.config import StrategyConfig
            strategy_config = StrategyConfig()
        self._strategy = strategy_config

    def _mode_stop_loss_map(self) -> dict[str, float]:
        return {
            "swing": self._strategy.mode_swing_stop_loss_pct,
            "short_term": self._strategy.mode_short_stop_loss_pct,
            "hold": self._strategy.mode_hold_stop_loss_pct,
        }

    def _mode_daily_loss_map(self) -> dict[str, float]:
        return {
            "swing": self._strategy.mode_swing_daily_loss_pct,
            "short_term": self._strategy.mode_short_daily_loss_pct,
            "hold": self._strategy.mode_hold_daily_loss_pct,
        }

    def _mode_position_size_map(self) -> dict[str, float]:
        return {
            "swing": self._strategy.mode_swing_position_size,
            "short_term": self._strategy.mode_short_position_size,
            "hold": self._strategy.mode_hold_position_size,
        }

    @property
    def mode(self) -> str:
        """현재 리스크 모드."""
        return self._mode

    def set_mode(self, mode: str) -> None:
        """매매 모드 변경 → 리스크 파라미터 자동 조정.

        Args:
            mode: "swing", "short_term", "hold"
        """
        if mode not in self._mode_stop_loss_map():
            logger.warning(f"알 수 없는 모드: {mode}, 기본(swing) 유지")
            return
        self._mode = mode
        logger.info(
            f"리스크 모드 변경: {mode} "
            f"(손절 {self._mode_stop_loss_map()[mode]:.1f}%, "
            f"일일한도 {self._mode_daily_loss_map()[mode]:.1f}%, "
            f"포지션크기 {self._mode_position_size_map()[mode]:.0%})"
        )

    @property
    def position_size_pct(self) -> float:
        """현재 모드의 포지션 크기 비율."""
        return self._mode_position_size_map().get(self._mode, 0.10)

    @property
    def effective_stop_loss_pct(self) -> float:
        """현재 모드의 기본 손절선."""
        return self._mode_stop_loss_map().get(self._mode, self._config.default_stop_loss_pct)

    @property
    def effective_daily_loss_limit(self) -> float:
        """현재 모드의 일일 손실 한도."""
        return self._mode_daily_loss_map().get(self._mode, self._config.daily_loss_limit_pct)

    @property
    def is_halted(self) -> bool:
        """Is trading currently halted due to risk limits?"""
        return self._halted

    @property
    def halt_reason(self) -> Optional[str]:
        """Reason for current halt, if any."""
        return self._halt_reason

    def check_daily_loss(self, account: AccountSummary) -> RiskCheckResult:
        """Check if daily loss limit has been reached.

        Uses mode-aware daily loss limit.
        """
        limit_pct = self.effective_daily_loss_limit

        if account.daily_loss_pct >= limit_pct:
            reason = (
                f"일일 총손실 한도 도달: {account.daily_loss_pct:.1f}% >= {limit_pct:.1f}%"
            )
            logger.warning(reason)
            self._halted = True
            self._halt_reason = reason
            return RiskCheckResult(
                is_safe=False,
                reason=reason,
                action=DecisionAction.NONE,
                halt_trading=True,
            )

        return RiskCheckResult(
            is_safe=True,
            reason=f"일일 손실 {account.daily_loss_pct:.1f}% (한도 {limit_pct:.1f}%)",
            action=DecisionAction.NONE,
        )

    def check_stop_loss(self, position: Position) -> RiskCheckResult:
        """Check if a position should be stop-lossed.

        Uses mode-aware stop loss.
        """
        current_loss_pct = abs(min(position.profit_loss_pct, 0))

        # Use position's dynamic stop loss, mode-aware default, or config default
        effective_stop = (
            position.stop_loss_pct
            or self.effective_stop_loss_pct
            or self._config.default_stop_loss_pct
        )

        if current_loss_pct >= effective_stop:
            reason = (
                f"손절선 도달: {position.name}({position.code}) "
                f"현재 손실 {current_loss_pct:.1f}% >= 손절선 {effective_stop:.1f}%"
            )
            logger.warning(reason)
            return RiskCheckResult(
                is_safe=False,
                reason=reason,
                action=DecisionAction.STOP_LOSS,
            )

        return RiskCheckResult(
            is_safe=True,
            reason=(
                f"손실 {current_loss_pct:.1f}% < 손절선 {effective_stop:.1f}% "
                f"({position.name}({position.code}))"
            ),
            action=DecisionAction.NONE,
        )

    def can_extend_stop_loss(
        self,
        position: Position,
        new_stop_loss_pct: float,
        reason: str,
    ) -> tuple[bool, str]:
        """Check if a stop loss can be extended.

        Rules:
        - New stop loss must be > current stop loss
        - New stop loss must not exceed max_stop_loss_pct (5%)
        - Reason MUST be provided and non-empty

        Returns:
            (can_extend, reason_message)
        """
        max_stop = self._config.max_stop_loss_pct

        if new_stop_loss_pct > max_stop:
            return False, f"최대 손절선 초과: {new_stop_loss_pct:.1f}% > {max_stop:.1f}%"

        current_stop = (
            position.stop_loss_pct
            if position.stop_loss_pct is not None
            else self._config.default_stop_loss_pct
        )
        if new_stop_loss_pct <= current_stop:
            return False, "새 손절선이 기존 손절선보다 작거나 같음 — 연장 아님"

        if not reason or not reason.strip():
            return False, "손절선 연장 사유 없음 — 근거 없는 조정 금지"

        return True, f"손절선 연장 승인: {position.stop_loss_pct:.1f}% → {new_stop_loss_pct:.1f}% (근거: {reason})"

    def reset_halt(self) -> None:
        """Reset trading halt (e.g. on new trading day)."""
        self._halted = False
        self._halt_reason = None
        logger.info("매매 중단 상태 해제 — 새 거래일 시작")
