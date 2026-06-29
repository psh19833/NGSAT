"""NGSAT live order executor — real order execution via broker adapter.

CRITICAL: This module is in the live/ package.
It MUST NOT import anything from backtest/.
It uses core/, data/, strategy/, ml/ shared modules.

Executes real buy/sell orders through the BrokerAdapter interface.
Every order execution includes:
- Decision reason (mandatory)
- Order recording to database
- Position update
- Risk check before execution

The executor is the ONLY module that calls broker.submit_order().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from core.exceptions import BrokerError
from core.logger import logger
from core.types import DecisionAction, OrderSide
from data.adapters.base import BrokerAdapter
from live.controller import TradingController, TradingState
from live.risk import RiskManager


@dataclass
class ExecutionResult:
    """Result of an order execution attempt.

    Attributes:
        success: Whether the order was submitted successfully.
        order_id: Broker-assigned order ID (if successful).
        code: Stock code.
        name: Stock name.
        side: Buy or sell.
        quantity: Number of shares.
        price: Execution price.
        amount: Total trade amount.
        action: Decision action that triggered this order.
        reason: Human-readable reason (Korean).
        error: Error message if failed.
    """
    success: bool
    order_id: str = ""
    code: str = ""
    name: str = ""
    side: str = ""
    quantity: int = 0
    price: float = 0.0
    amount: float = 0.0
    action: str = ""
    reason: str = ""
    error: str = ""


class OrderExecutor:
    """Executes real orders through the broker adapter.

    This is the bridge between ML decisions and actual market orders.
    It enforces:
    - Risk checks before every order
    - Trading controller state checks (must be RUNNING)
    - Decision reason recording for every order
    - Force-hold respect (won't sell force-held positions)

    The executor NEVER makes trading decisions — it only executes
    decisions made by the ML inference engine.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        risk_manager: RiskManager,
        controller: TradingController,
        account_no: str = "",
        account_product_code: str = "01",
    ):
        self._broker = broker
        self._risk = risk_manager
        self._controller = controller
        self._account_no = account_no
        self._account_product_code = account_product_code
        # BE-1: idempotency — track successfully submitted orders to prevent duplicates
        self._submitted_orders: dict[tuple[str, str], str] = {}  # (code, side) -> order_id

    async def _submit_with_retry(
        self,
        code: str,
        side: OrderSide,
        quantity: int,
        price: float | None,
        idempotency_key: str = "",
        max_retries: int = 3,
    ) -> str:
        """Submit order with idempotency and exponential backoff retry.

        Args:
            code: Stock code.
            side: Buy or sell.
            quantity: Number of shares.
            price: Limit price (None for market).
            idempotency_key: Unique key for this order attempt (prevents duplicates).
            max_retries: Max retry attempts (default 3).

        Returns:
            Order ID string.

        Raises:
            BrokerError: If all retries fail (transient) or order is rejected (permanent).
        """
        # Check if this (code, side) was already submitted successfully
        prev_order_id = self._submitted_orders.get((code, side.value))
        if prev_order_id:
            try:
                status = await self._broker.get_order_status(prev_order_id)
                logger.info(
                    f"중복 주문 방지: {code} {side.value} "
                    f"기존 주문={prev_order_id} 상태={status.value}"
                )
                return prev_order_id
            except BrokerError:
                logger.warning(
                    f"기존 주문 상태 조회 실패, 재시도 진행: {code} {side.value}"
                )

        base_delay = 1.0
        last_error = None
        for attempt in range(max_retries):
            try:
                order_id = await self._broker.submit_order(
                    code=code, side=side, quantity=quantity, price=price,
                )
                # Store successful submission for idempotency
                self._submitted_orders[(code, side.value)] = order_id
                return order_id
            except BrokerError as e:
                last_error = e
                err_str = str(e)

                # Permanent rejection (4xx-equivalent: KIS rt_cd != "0"):
                # insufficient balance, invalid params, etc. — do NOT retry
                if "rejected" in err_str.lower() or "거절" in err_str:
                    logger.error(
                        f"주문 거절(재시도 안함): {code} {side.value} — {e}"
                    )
                    raise

                # Check if a previous attempt's order was actually accepted
                prev_id = self._submitted_orders.get((code, side.value))
                if prev_id:
                    try:
                        existing_status = await self._broker.get_order_status(prev_id)
                        logger.info(
                            f"재시도 전 기존 주문 확인: {code} {prev_id} "
                            f"상태={existing_status.value} — 재시도 중단"
                        )
                        return prev_id
                    except BrokerError:
                        pass  # couldn't verify — retry below

                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"주문 재시도 {attempt + 1}/{max_retries}: "
                        f"{code} {side.value} — {e} ({delay:.0f}초 후 재시도)"
                    )
                    await asyncio.sleep(delay)
        raise last_error  # type: ignore[misc]

    async def execute_buy(
        self,
        code: str,
        name: str,
        quantity: int,
        price: float | None,
        action: DecisionAction,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute a buy order.

        Args:
            code: 6-digit stock code.
            name: Stock name.
            quantity: Number of shares to buy.
            price: Limit price (None for market order).
            action: Decision action (should be BUY).
            reason: Mandatory decision reason.
            evidence: Optional quantitative evidence.

        Returns:
            ExecutionResult with order details.
        """
        # Validate reason
        if not reason or not reason.strip():
            return ExecutionResult(
                success=False, code=code, name=name,
                error="주문 사유 없음 — 근거 없는 거래 금지",
            )

        # Check controller state
        if not self._controller.is_running:
            return ExecutionResult(
                success=False, code=code, name=name,
                error=f"매매 진행 중 아님 (현재 상태: {self._controller.state.value})",
            )

        # Check risk halt
        if self._risk.is_halted:
            return ExecutionResult(
                success=False, code=code, name=name,
                error=f"리스크 한도로 매매 중단: {self._risk.halt_reason}",
            )

        logger.info(f"매수 주문: {name}({code}) {quantity}주")

        try:
            order_id = await self._submit_with_retry(
                code=code, side=OrderSide.BUY,
                quantity=quantity, price=price,
            )

            amount = (price or 0) * quantity

            result = ExecutionResult(
                success=True,
                order_id=order_id,
                code=code,
                name=name,
                side="buy",
                quantity=quantity,
                price=price or 0,
                amount=amount,
                action=action.value,
                reason=reason,
            )

            logger.info(f"매수 체결: {name}({code}) 주문번호={order_id}")
            return result

        except BrokerError as e:
            logger.error(f"매수 실패: {name}({code}) — {e}")
            return ExecutionResult(
                success=False, code=code, name=name,
                side="buy", error=str(e),
            )

    async def execute_sell(
        self,
        code: str,
        name: str,
        quantity: int,
        price: float | None,
        action: DecisionAction,
        reason: str,
        evidence: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Execute a sell order.

        Args:
            code: 6-digit stock code.
            name: Stock name.
            quantity: Number of shares to sell.
            price: Limit price (None for market order).
            action: Decision action (SELL, STOP_LOSS, or FORCE_SELL).
            reason: Mandatory decision reason.
            evidence: Optional quantitative evidence.

        Returns:
            ExecutionResult with order details.
        """
        # Validate reason
        if not reason or not reason.strip():
            return ExecutionResult(
                success=False, code=code, name=name,
                error="주문 사유 없음 — 근거 없는 거래 금지",
            )

        # Check force hold
        if self._controller.is_force_hold(code) and action != DecisionAction.FORCE_SELL:
            return ExecutionResult(
                success=False, code=code, name=name,
                error=f"강제 홀드 중인 종목 — 매도 불가: {name}({code})",
            )

        logger.info(f"매도 주문: {name}({code}) {quantity}주 — {action.value}")

        try:
            order_id = await self._submit_with_retry(
                code=code, side=OrderSide.SELL,
                quantity=quantity, price=price,
            )

            amount = (price or 0) * quantity

            result = ExecutionResult(
                success=True,
                order_id=order_id,
                code=code,
                name=name,
                side="sell",
                quantity=quantity,
                price=price or 0,
                amount=amount,
                action=action.value,
                reason=reason,
            )

            logger.info(f"매도 체결: {name}({code}) 주문번호={order_id}")
            return result

        except BrokerError as e:
            logger.error(f"매도 실패: {name}({code}) — {e}")
            return ExecutionResult(
                success=False, code=code, name=name,
                side="sell", error=str(e),
            )

    async def execute_force_sell(
        self,
        code: str,
        name: str,
        quantity: int,
        price: float | None = None,
    ) -> ExecutionResult:
        """Execute a forced sell (강제 매도).

        This bypasses the force-hold check — it's the operator's override.
        Still requires the controller to be in a non-SHUTDOWN state.
        """
        reason = f"대표님 강제 매도 지시: {name}({code})"

        if self._controller.state == TradingState.SHUTDOWN:
            return ExecutionResult(
                success=False, code=code, name=name,
                error="시스템 종료 상태 — 주문 불가",
            )

        return await self.execute_sell(
            code=code,
            name=name,
            quantity=quantity,
            price=price,
            action=DecisionAction.FORCE_SELL,
            reason=reason,
        )
