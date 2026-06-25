"""NGSAT live orchestrator — the full automated trading cycle.

CRITICAL: This module is in the live/ package.
It MUST NOT import anything from backtest/.

The orchestrator ties everything together:
  1. Fetch account & market data (via BrokerAdapter)
  2. Evaluate market regime (strategy/regime.py)
  3. Screen candidate stocks (strategy/screener.py)
  4. ML prediction for entry/exit (ml/inference.py)
  5. Execute orders (live/executor.py)
  6. Check risk limits (live/risk.py)
  7. Record everything to database with reasons

The orchestrator runs as a loop, executing one full cycle per tick.
The controller (start/stop/shutdown) governs whether the loop runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.config import RiskConfig
from core.logger import logger
from core.types import (
    AccountSummary,
    DecisionAction,
    DecisionReason,
    MarketRegime,
    Position,
    PriceData,
)
from data.adapters.base import BrokerAdapter
from live.controller import TradingController, TradingState
from live.executor import ExecutionResult, OrderExecutor
from live.risk import RiskManager, RiskCheckResult
from ml.inference import ExitPrediction, MLInference, MLPrediction
from strategy.regime import RegimeResult, evaluate_regime
from strategy.screener import ScreenCandidate, ScreenResult, screen_stocks
from strategy.entry_timing import EntryDecision, EntryTiming, refine_entry
from strategy.exit_timing import ExitDecision, ExitUrgency, refine_exit


@dataclass
class CycleResult:
    """Result of a single trading cycle.
    
    Attributes:
        timestamp: When the cycle ran.
        regime: Market regime evaluation.
        candidates_found: Number of screened candidates.
        buys_executed: Number of buy orders executed.
        sells_executed: Number of sell orders executed.
        errors: List of error messages.
        reason: Human-readable cycle summary (Korean).
    """
    timestamp: datetime = field(default_factory=datetime.now)
    regime: MarketRegime = MarketRegime.NEUTRAL
    candidates_found: int = 0
    buys_executed: int = 0
    entries_deferred: int = 0
    sells_executed: int = 0
    errors: list[str] = field(default_factory=list)
    reason: str = ""


class TradingOrchestrator:
    """Orchestrates the full automated trading cycle.
    
    This is the heart of NGSAT's live trading. It runs the 3-stage
    pipeline (regime → screener → ML) and executes real orders.
    
    The orchestrator does NOT make decisions on its own — it follows
    the ML model's predictions. Its job is to wire the pipeline together
    and ensure every order has a reason.
    
    Lifecycle:
        controller.start() → run_cycle() repeatedly → controller.stop()
    
    The orchestrator checks controller state at every step:
    - If not RUNNING → skip cycle
    - If HALTED by risk → skip cycle
    - If SHUTDOWN → clean up and stop
    """
    
    def __init__(
        self,
        broker: BrokerAdapter,
        model,
        risk_config: RiskConfig | None = None,
        buy_threshold: float = 0.65,
        sell_threshold: float = 0.35,
        position_budget_pct: float = 0.10,
    ):
        self._broker = broker
        self._risk = RiskManager(risk_config or RiskConfig())
        self._controller = TradingController()
        self._executor = OrderExecutor(broker, self._risk, self._controller)
        self._inference = MLInference(model, buy_threshold, sell_threshold)
        self._position_budget_pct = position_budget_pct
        
        # State
        self._last_regime: RegimeResult | None = None
        self._cycle_count: int = 0
    
    @property
    def controller(self) -> TradingController:
        """Access the trading controller for start/stop/force operations."""
        return self._controller
    
    @property
    def risk_manager(self) -> RiskManager:
        """Access the risk manager."""
        return self._risk
    
    async def run_cycle(
        self,
        index_prices: list[PriceData],
        stock_universe: list[tuple[Any, list[PriceData]]],
    ) -> CycleResult:
        """Run one full trading cycle.
        
        Args:
            index_prices: Recent index price history for regime evaluation.
            stock_universe: List of (StockInfo, price history) for screening.
        
        Returns:
            CycleResult with summary of what happened.
        """
        result = CycleResult()
        self._cycle_count += 1
        
        # Check controller state
        if not self._controller.is_running:
            result.reason = f"매매 대기 중 (상태: {self._controller.state.value})"
            return result
        
        if self._risk.is_halted:
            result.reason = f"리스크 중단: {self._risk.halt_reason}"
            return result
        
        logger.info(f"=== 매매 사이클 #{self._cycle_count} 시작 ===")
        
        # ── Step 1: Fetch account ──
        try:
            account = await self._broker.get_account_summary()
        except Exception as e:
            err = f"계좌 조회 실패: {type(e).__name__}: {e}"
            logger.error(err)
            result.errors.append(err)
            result.reason = err
            return result
        
        # ── Step 2: Risk check (daily loss) ──
        risk_check = self._risk.check_daily_loss(account)
        if risk_check.halt_trading:
            self._controller.halt_by_risk(risk_check.reason)
            result.reason = f"리스크 자동 중단: {risk_check.reason}"
            return result
        
        # ── Step 3: Regime evaluation ──
        if len(index_prices) < 20:
            result.reason = "인덱스 데이터 부족 — 레짐 평가 불가"
            return result
        
        regime_result = evaluate_regime(
            [p.close for p in index_prices],
            [p.volume for p in index_prices],
        )
        self._last_regime = regime_result
        result.regime = regime_result.regime
        
        logger.info(f"레짐 평가: {regime_result.regime.value} ({regime_result.score:.1f}점)")
        
        # ── Step 4: Screen stocks ──
        screen_result = screen_stocks(stock_universe, regime_result)
        result.candidates_found = len(screen_result.candidates)
        
        logger.info(f"스크리닝: {screen_result.total_scanned}개 → {result.candidates_found}개 후보")
        
        # ── Step 5: ML predictions & buy execution ──
        current_positions = await self._fetch_positions()
        held_codes = {p.code for p in current_positions}
        
        for candidate in screen_result.candidates:
            if candidate.code in held_codes:
                continue  # Already holding
            
            # Find price data for this candidate
            prices = self._find_prices(stock_universe, candidate.code)
            if prices is None or len(prices) < 60:
                continue
            
            pred = self._inference.predict_entry(candidate, prices)
            
            if pred and pred.action == DecisionAction.BUY:
                # 진입 정밀화: 분봉으로 타이밍/가격 판단 (하이브리드 1단계)
                entry = await self._refine_entry(pred.code)
                if not entry.should_enter:
                    result.entries_deferred += 1
                    logger.info(f"진입 보류: {pred.name}({pred.code}) — {entry.reason}")
                    continue

                ref_price = entry.limit_price or prices[-1].close
                budget = account.deposit * self._position_budget_pct
                quantity = int(budget / ref_price) if ref_price > 0 else 0

                if quantity <= 0:
                    continue

                buy_reason = f"{pred.reason} || 진입정밀화: {entry.reason}"
                exec_result = await self._executor.execute_buy(
                    code=pred.code,
                    name=pred.name,
                    quantity=quantity,
                    price=entry.limit_price,
                    action=pred.action,
                    reason=buy_reason,
                )
                
                if exec_result.success:
                    result.buys_executed += 1
                    held_codes.add(pred.code)
                else:
                    result.errors.append(f"매수 실패 {pred.code}: {exec_result.error}")
        
        # ── Step 6: Exit check for existing positions ──
        for position in current_positions:
            if self._controller.is_force_hold(position.code):
                continue
            
            prices = self._find_prices(stock_universe, position.code)
            if prices is None or len(prices) < 60:
                continue
            
            # 청산 정밀화: 분봉으로 매도 긴급도/가격 판단 (하이브리드 1단계)
            exit_ref = await self._refine_exit(position.code, position.profit_loss_pct)
            sell_price = None if exit_ref.urgency == ExitUrgency.IMMEDIATE else exit_ref.limit_price

            # 1) 일봉 손절선 도달 → 손절 (분봉 급락이면 시장가 즉시)
            loss_pct = abs(min(position.profit_loss_pct, 0))
            if loss_pct >= position.stop_loss_pct:
                exec_result = await self._executor.execute_sell(
                    code=position.code,
                    name=position.name,
                    quantity=position.quantity,
                    price=sell_price,
                    action=DecisionAction.STOP_LOSS,
                    reason=(
                        f"손절: {position.name}({position.code}) "
                        f"손실 {loss_pct:.1f}% >= 손절선 {position.stop_loss_pct:.1f}% "
                        f"|| 청산정밀화: {exit_ref.reason}"
                    ),
                )
                if exec_result.success:
                    result.sells_executed += 1
                else:
                    result.errors.append(f"손절 실패 {position.code}: {exec_result.error}")
                continue
            
            # 2) 분봉 선제 청산 (일봉 ML보다 빠른 급락/과열익절 신호)
            if exit_ref.should_exit:
                exec_result = await self._executor.execute_sell(
                    code=position.code,
                    name=position.name,
                    quantity=position.quantity,
                    price=sell_price,
                    action=DecisionAction.SELL,
                    reason=f"분봉 청산: {exit_ref.reason}",
                )
                if exec_result.success:
                    result.sells_executed += 1
                else:
                    result.errors.append(f"매도 실패 {position.code}: {exec_result.error}")
                continue

            # 3) 일봉 ML 청산 → 분봉 현재가 지정가로 매도가 정밀화
            exit_pred = self._inference.predict_exit(
                position.code, position.name, prices, position.profit_loss_pct
            )
            
            if exit_pred and exit_pred.action == DecisionAction.SELL:
                exec_result = await self._executor.execute_sell(
                    code=position.code,
                    name=position.name,
                    quantity=position.quantity,
                    price=sell_price,
                    action=DecisionAction.SELL,
                    reason=f"{exit_pred.reason} || 청산정밀화: {exit_ref.reason}",
                )
                if exec_result.success:
                    result.sells_executed += 1
                else:
                    result.errors.append(f"매도 실패 {position.code}: {exec_result.error}")
        
        # ── Build summary ──
        result.reason = (
            f"사이클 #{self._cycle_count} 완료: "
            f"레짐={regime_result.regime.value}({regime_result.score:.0f}점), "
            f"후보={result.candidates_found}개, "
            f"매수={result.buys_executed}건(보류 {result.entries_deferred}건), "
            f"매도={result.sells_executed}건"
        )
        
        if result.errors:
            result.reason += f", 오류={len(result.errors)}건"
        
        logger.info(result.reason)
        return result
    
    async def _fetch_positions(self) -> list[Position]:
        """Fetch current positions from broker."""
        try:
            return await self._broker.get_positions()
        except Exception as e:
            logger.error(f"포지션 조회 실패: {e}")
            return []

    async def _refine_entry(self, code: str) -> EntryDecision:
        """분봉으로 진입 타이밍/가격을 정밀화. 분봉 미가용 시 시장가 진입 폴백."""
        try:
            minute_prices = await self._broker.get_minute_history(code)
        except NotImplementedError:
            return EntryDecision(
                timing=EntryTiming.ENTER_NOW, should_enter=True, limit_price=None,
                reason="분봉 미지원 어댑터 — 정밀화 생략(시장가 진입)", evidence={},
            )
        except Exception as e:
            logger.warning(f"분봉 조회 실패({code}) — 정밀화 생략: {type(e).__name__}")
            return EntryDecision(
                timing=EntryTiming.ENTER_NOW, should_enter=True, limit_price=None,
                reason="분봉 조회 실패 — 정밀화 생략(시장가 진입)", evidence={},
            )
        return refine_entry(minute_prices)

    async def _refine_exit(self, code: str, profit_pct: float) -> ExitDecision:
        """분봉으로 청산 긴급도/가격을 정밀화. 분봉 미가용 시 정밀화 생략(기존 로직 위임)."""
        try:
            minute_prices = await self._broker.get_minute_history(code)
        except NotImplementedError:
            return ExitDecision(
                should_exit=False, urgency=ExitUrgency.NONE, limit_price=None,
                reason="분봉 미지원 어댑터 — 청산 정밀화 생략", evidence={},
            )
        except Exception as e:
            logger.warning(f"분봉 조회 실패({code}) — 청산 정밀화 생략: {type(e).__name__}")
            return ExitDecision(
                should_exit=False, urgency=ExitUrgency.NONE, limit_price=None,
                reason="분봉 조회 실패 — 청산 정밀화 생략", evidence={},
            )
        return refine_exit(minute_prices, profit_pct)
    
    @staticmethod
    def _find_prices(
        universe: list[tuple[Any, list[PriceData]]],
        code: str,
    ) -> list[PriceData] | None:
        """Find price history for a stock code in the universe."""
        for info, prices in universe:
            if info.code == code:
                return prices
        return None
    
    async def force_sell(self, code: str, name: str = "") -> ExecutionResult:
        """Force sell a position — operator override.
        
        Args:
            code: Stock code to force sell.
            name: Stock name.
        
        Returns:
            ExecutionResult.
        """
        positions = await self._fetch_positions()
        pos = next((p for p in positions if p.code == code), None)
        
        if pos is None:
            return ExecutionResult(
                success=False, code=code, name=name,
                error=f"보유하지 않은 종목: {name}({code})",
            )
        
        return await self._executor.execute_force_sell(
            code=code,
            name=name or pos.name,
            quantity=pos.quantity,
        )
