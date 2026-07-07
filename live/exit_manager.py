"""NGSAT live trading — Exit Manager (Phase 2 분할).

Responsible for:
- Trailing stop update & trigger (via RiskManager)
- Partial take profit (via RiskManager)
- Stop loss check
- Minute-based exit refinement
- ML exit prediction
- Sell execution (via OrderExecutor)
- Trade recording to DB (via TradeRecorder)

Orchestrator creates one ExitManager and calls `check_exits()` each cycle.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from core.logger import logger
from core.types import (
    DecisionAction,
    Position,
    PriceData,
)
from live.executor import OrderExecutor
from live.models import CycleContext
from live.risk import RiskManager
from live.trade_recorder import TradeRecorder
from ml.inference import MLInference
from strategy.exit_timing import ExitDecision, ExitUrgency, refine_exit
from strategy.indicators import atr as calc_atr


class ExitManager:
    """포지션 청산 파이프라인.

    한 사이클에서 orchestrator로부터 CycleContext를 받아:
    1. 트레일링 스탑 업데이트 + 트리거 검사
    2. 부분 익절 검사
    3. 일봉 손절선 도달 검사
    4. 분봉 선제 청산
    5. ML 청산 예측
    각 단계마다 execute_sell + DB 기록까지 처리.
    """

    def __init__(
        self,
        executor: OrderExecutor,
        inference: MLInference,
        risk: RiskManager,
        trade_recorder: TradeRecorder,
    ) -> None:
        self._executor = executor
        self._inference = inference
        self._risk = risk
        self._trade_recorder = trade_recorder

    async def check_exits(
        self,
        ctx: CycleContext,
        broker,
        stock_universe: list[tuple[Any, list[PriceData]]],
    ) -> dict:
        """Execute the full exit pipeline for all held positions.

        Returns:
            dict with keys: sells_executed, errors
        """
        result: dict = {
            "sells_executed": 0,
            "errors": [],
        }

        if not ctx.current_positions:
            return result

        for position in ctx.current_positions:
            exit_result = await self._check_single_exit(ctx, position, broker, stock_universe)
            if exit_result:
                if exit_result.get("error"):
                    result["errors"].append(exit_result["error"])
                elif exit_result.get("sold"):
                    result["sells_executed"] += 1

        return result

    async def _check_single_exit(
        self,
        ctx: CycleContext,
        position: Position,
        broker,
        stock_universe: list[tuple[Any, list[PriceData]]],
    ) -> dict | None:
        """Check exit conditions for a single position."""
        prices = self._find_prices(stock_universe, position.code)
        if prices is None or len(prices) < 60:
            return None

        exit_ref = await self._refine_exit(broker, position.code, position.profit_loss_pct, ctx)
        sell_price = None if exit_ref.urgency == ExitUrgency.IMMEDIATE else exit_ref.limit_price

        # 0) 트레일링 스탑
        result = await self._check_trailing_stop(ctx, position, prices, sell_price, exit_ref)
        if result:
            return result

        # 0.5) 부분 익절
        result = await self._check_partial_tp(ctx, position, prices, sell_price, exit_ref)
        if result and result.get("sold"):
            if position.quantity <= 0:
                return result
        if result:
            return result

        # 1) 일봉 손절선
        loss_pct = abs(min(position.profit_loss_pct, 0))
        effective_stop = self._risk.effective_stop_loss_pct or position.stop_loss_pct
        if loss_pct >= effective_stop:
            return await self._execute_exit(
                ctx, position, sell_price, DecisionAction.STOP_LOSS,
                f"손절: {position.name}({position.code}) 손실 {loss_pct:.1f}% >= 손절선 {effective_stop:.1f}% "
                f"|| 청산정밀화: {exit_ref.reason}",
                exit_ref,
            )

        # 2) 분봉 선제 청산
        if exit_ref.should_exit:
            return await self._execute_exit(
                ctx, position, sell_price, DecisionAction.SELL,
                f"분봉 청산: {exit_ref.reason}",
                exit_ref,
            )

        # 3) ML 청산 예측
        if ctx.is_short_term:
            minute_prices = await self._fetch_minute_prices(broker, position.code, ctx)
            if minute_prices and len(minute_prices) >= 60:
                exit_pred = self._inference.predict_minute_exit(
                    position.code, position.name, minute_prices, position.profit_loss_pct
                )
            else:
                exit_pred = self._inference.predict_exit(
                    position.code, position.name, prices, position.profit_loss_pct
                )
        else:
            exit_pred = self._inference.predict_exit(
                position.code, position.name, prices, position.profit_loss_pct
            )

        if exit_pred and exit_pred.action == DecisionAction.SELL:
            return await self._execute_exit(
                ctx, position, sell_price, DecisionAction.SELL,
                f"{exit_pred.reason} || 청산정밀화: {exit_ref.reason}",
                exit_ref,
            )

        return None

    async def _check_trailing_stop(
        self,
        ctx: CycleContext,
        position: Position,
        prices: list[PriceData],
        sell_price: float | None,
        exit_ref: ExitDecision,
    ) -> dict | None:
        """트레일링 스탑 업데이트 + 트리거 검사."""
        try:
            highs = [p.high for p in prices[-20:]]
            lows = [p.low for p in prices[-20:]]
            closes = [p.close for p in prices[-20:]]
            atr_vals = calc_atr(highs, lows, closes, period=14)
            atr_val = float(atr_vals[-1]) if len(atr_vals) > 0 and not np.isnan(atr_vals[-1]) else None
        except Exception:
            atr_val = None

        position = self._risk.update_trailing_stop(position, position.current_price, atr_val)
        trail_result = self._risk.check_trailing_stop(position)
        if not trail_result.is_safe:
            return await self._execute_exit(
                ctx, position, sell_price, DecisionAction.STOP_LOSS,
                f"{trail_result.reason} || 청산정밀화: {exit_ref.reason}",
                exit_ref,
            )
        return None

    async def _check_partial_tp(
        self,
        ctx: CycleContext,
        position: Position,
        prices: list[PriceData],
        sell_price: float | None,
        exit_ref: ExitDecision,
    ) -> dict | None:
        """부분 익절 검사."""
        partial_tp = self._risk.check_partial_take_profit(position)
        if not partial_tp["should_sell"] or partial_tp["sell_quantity"] <= 0:
            return None

        exec_result = await self._executor.execute_sell(
            code=position.code,
            name=position.name,
            quantity=partial_tp["sell_quantity"],
            price=sell_price,
            action=DecisionAction.SELL,
            reason=partial_tp["reason"],
        )
        if not exec_result.success:
            return {"error": f"부분 익절 실패 {position.code}: {exec_result.error}"}

        # DB 기록 via TradeRecorder
        self._trade_recorder.record_sell(
            exec_result, position, sell_price,
            action=DecisionAction.SELL,
            reason=partial_tp["reason"],
            partial_sold_qty=partial_tp["sell_quantity"],
        )

        # position 객체 업데이트 (호출자에서 잔량 확인)
        remaining = position.quantity - partial_tp["sell_quantity"]
        tp1_done = position.partial_tp1_executed or (partial_tp["tp_stage"] == 1)
        tp2_done = position.partial_tp2_executed or (partial_tp["tp_stage"] == 2)
        position.quantity = remaining
        position.partial_tp1_executed = tp1_done
        position.partial_tp2_executed = tp2_done
        position.original_quantity = position.original_quantity or (position.quantity + partial_tp["sell_quantity"])

        logger.info(
            f"부분 익절 완료: {position.name}({position.code}) "
            f"{partial_tp['sell_quantity']}주 매도, 잔여 {remaining}주"
        )
        return {"sold": True}

    async def _execute_exit(
        self,
        ctx: CycleContext,
        position: Position,
        sell_price: float | None,
        action: DecisionAction,
        reason: str,
        exit_ref: ExitDecision,
    ) -> dict:
        """매도 실행 + DB 기록."""
        exec_result = await self._executor.execute_sell(
            code=position.code,
            name=position.name,
            quantity=position.quantity,
            price=sell_price,
            action=action,
            reason=reason,
        )
        # DB 기록 via TradeRecorder
        self._trade_recorder.record_sell(
            exec_result, position, sell_price,
            action=action,
            reason=reason,
            partial_sold_qty=None,
        )
        if exec_result.success:
            return {"sold": True}
        return {"error": f"매도 실패 {position.code}: {exec_result.error}"}

    async def _refine_exit(
        self, broker, code: str, profit_pct: float, ctx: CycleContext
    ) -> ExitDecision:
        """분봉으로 청산 긴급도/가격 정밀화."""
        try:
            minute_prices = ctx.minute_cache.get(code)
            if minute_prices is None:
                minute_prices = await self._fetch_minute_prices(broker, code, ctx)
        except NotImplementedError:
            return ExitDecision(
                should_exit=False, urgency=ExitUrgency.NONE, limit_price=None,
                reason="분봉 미지원 — 청산 정밀화 생략", evidence={},
            )
        except Exception as e:
            logger.warning(f"분봉 조회 실패({code}) — 청산 정밀화 생략: {type(e).__name__}")
            return ExitDecision(
                should_exit=False, urgency=ExitUrgency.NONE, limit_price=None,
                reason="분봉 조회 실패 — 청산 정밀화 생략", evidence={},
            )
        if minute_prices is None:
            return ExitDecision(
                should_exit=False, urgency=ExitUrgency.NONE, limit_price=None,
                reason="분봉 데이터 없음 — 청산 정밀화 생략", evidence={},
            )
        return refine_exit(minute_prices, profit_pct)

    async def _fetch_minute_prices(self, broker, code: str, ctx: CycleContext) -> list[PriceData] | None:
        if hasattr(broker, '_minute_builder') and broker._minute_builder is not None:
            bars = broker._minute_builder.get_bars(code, 60)
            if len(bars) >= 30:
                return bars
        try:
            return await broker.get_minute_history(code)
        except Exception as e:
            logger.warning(f"분봉 조회 실패({code}): {type(e).__name__}")
            return None

    @staticmethod
    def _find_prices(
        universe: list[tuple[Any, list[PriceData]]],
        code: str,
    ) -> list[PriceData] | None:
        for info, prices in universe:
            if info.code == code:
                return prices
        return None
