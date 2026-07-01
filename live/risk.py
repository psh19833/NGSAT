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
from core.logger import logger
from core.types import AccountSummary, DecisionAction, Position


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

    # ── 트레일링 스탑 (P1-1) ──────────────────────────────────────────────

    def update_trailing_stop(
        self,
        position: Position,
        current_price: float,
        atr_value: float | None = None,
    ) -> Position:
        """트레일링 스탑 가격을 업데이트하고 새 Position 반환.

        ratchet 동작: 가격이 오르면 trailing_stop_price도 올라가지만,
        가격이 내려가면 trailing_stop_price는 내려가지 않는다.

        Args:
            position: 현재 포지션 (frozen dataclass).
            current_price: 현재가.
            atr_value: ATR 값 (None이면 트레일링 스탑 계산 불가 → 스킵).

        Returns:
            업데이트된 새 Position (dataclasses.replace).
        """
        from dataclasses import replace as dc_replace

        # 비활성시 아무 변경 없음
        if not self._strategy.trailing_stop_enabled:
            return position

        # 수익이 활성화 기준 미만이면 트레일링 스탑 미활성
        if position.profit_loss_pct < self._strategy.trailing_stop_activate_pct:
            return position

        # ATR 데이터 없으면 스킵 (fallback: 기존 손절선 유지)
        if atr_value is None or atr_value <= 0:
            return position

        # 최고가 업데이트
        high_water = position.trailing_stop_high_water or current_price
        if current_price > high_water:
            high_water = current_price

        # 트레일링 스탑 가격 계산
        trail_distance = atr_value * self._strategy.trailing_stop_atr_multiplier
        new_trail_price = high_water - trail_distance

        # ratchet: 기존 트레일링 스탑보다만 올림 (내리지 않음)
        old_trail_price = position.trailing_stop_price
        if old_trail_price is not None and new_trail_price < old_trail_price:
            new_trail_price = old_trail_price

        logger.debug(
            f"트레일링 스탑 업데이트: {position.name}({position.code}) "
            f"최고가 {high_water:.0f}원 → 스탑 {new_trail_price:.0f}원 "
            f"(ATR {atr_value:.0f} × {self._strategy.trailing_stop_atr_multiplier})"
        )

        return dc_replace(
            position,
            trailing_stop_high_water=high_water,
            trailing_stop_price=new_trail_price,
        )

    def check_trailing_stop(self, position: Position) -> RiskCheckResult:
        """트레일링 스탑 트리거 여부 확인.

        현재가가 trailing_stop_price 이하면 청산 권고.

        Returns:
            RiskCheckResult (is_safe=False → 청산, is_safe=True → 유지).
        """
        if not self._strategy.trailing_stop_enabled:
            return RiskCheckResult(
                is_safe=True,
                reason="트레일링 스탑 비활성",
                action=DecisionAction.NONE,
            )

        if position.trailing_stop_price is None:
            return RiskCheckResult(
                is_safe=True,
                reason="트레일링 스탑 미활성 (수익 기준 미달 또는 ATR 부족)",
                action=DecisionAction.NONE,
            )

        if position.current_price <= position.trailing_stop_price:
            trail_drop_pct = (
                (position.trailing_stop_high_water - position.current_price)
                / position.trailing_stop_high_water * 100
                if position.trailing_stop_high_water
                else 0.0
            )
            reason = (
                f"트레일링 스탑 트리거: {position.name}({position.code}) "
                f"현재가 {position.current_price:.0f}원 ≤ "
                f"스탑 {position.trailing_stop_price:.0f}원 "
                f"(최고가 대비 -{trail_drop_pct:.1f}%)"
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
                f"트레일링 스탑 유지: {position.name}({position.code}) "
                f"현재가 {position.current_price:.0f}원 > "
                f"스탑 {position.trailing_stop_price:.0f}원"
            ),
            action=DecisionAction.NONE,
        )

    # ── 부분 청산 (P1-2) ──────────────────────────────────────────────

    def check_partial_take_profit(self, position: Position) -> dict:
        """부분 익절 여부 판단.

        Returns:
            dict: {
                should_sell: bool — 부분 매도 권고,
                sell_quantity: int — 매도할 수량 (0이면 매도 없음),
                tp_stage: int — 1=1차, 2=2차, 0=해당 없음,
                reason: str — 판단 근거 (한글),
            }
        """
        if not self._strategy.partial_tp_enabled:
            return {"should_sell": False, "sell_quantity": 0, "tp_stage": 0, "reason": "부분 청산 비활성"}

        profit_pct = position.profit_loss_pct
        orig_qty = position.original_quantity or position.quantity
        result = {"should_sell": False, "sell_quantity": 0, "tp_stage": 0, "reason": ""}

        # 1차 익절
        if not position.partial_tp1_executed and profit_pct >= self._strategy.partial_tp1_pct:
            sell_qty = max(1, int(orig_qty * self._strategy.partial_tp1_ratio))
            sell_qty = min(sell_qty, position.quantity)  # 보유 수량 초과 방지
            result = {
                "should_sell": True,
                "sell_quantity": sell_qty,
                "tp_stage": 1,
                "reason": (
                    f"부분 익절 1차: {position.name}({position.code}) "
                    f"수익 +{profit_pct:.1f}% >= {self._strategy.partial_tp1_pct:.1f}% "
                    f"→ {sell_qty}주 매도 (원래 {orig_qty}주 중)"
                ),
            }
            logger.info(result["reason"])
            return result

        # 2차 익절
        if not position.partial_tp2_executed and profit_pct >= self._strategy.partial_tp2_pct:
            sell_qty = max(1, int(orig_qty * self._strategy.partial_tp2_ratio))
            sell_qty = min(sell_qty, position.quantity)
            result = {
                "should_sell": True,
                "sell_quantity": sell_qty,
                "tp_stage": 2,
                "reason": (
                    f"부분 익절 2차: {position.name}({position.code}) "
                    f"수익 +{profit_pct:.1f}% >= {self._strategy.partial_tp2_pct:.1f}% "
                    f"→ {sell_qty}주 매도 (잔여 {position.quantity}주 중)"
                ),
            }
            logger.info(result["reason"])
            return result

        result["reason"] = (
            f"부분 익절 대기: {position.name}({position.code}) "
            f"수익 +{profit_pct:.1f}% "
            f"(1차 {self._strategy.partial_tp1_pct:.1f}%, "
            f"2차 {self._strategy.partial_tp2_pct:.1f}%)"
        )
        return result
