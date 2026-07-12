"""NGSAT live orchestrator — the full automated trading cycle.

CRITICAL: This module is in the live/ package.
It MUST NOT import anything from backtest/.

Phase 2 refactoring: orchestrator is now a thin coordinator.
Actual entry decisions → EntryPlanner
Actual exit decisions  → ExitManager
Common data models   → live/models.py (CycleContext)

The orchestrator ties everything together:
  1. Fetch account & market data (via BrokerAdapter)
  2. Evaluate market regime (strategy/regime.py)
  3. Delegate entry pipeline to EntryPlanner
  4. Delegate exit pipeline to ExitManager
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

import numpy as np

from core.config import RiskConfig, StrategyConfig
from core.logger import logger
from pathlib import Path
from core.types import (
    AccountSummary,
    DecisionAction,
    MarketRegime,
    Position,
    PriceData,
    StrategyMode,
    is_market_hours,
    now_kst,
)
from data.adapters.base import BrokerAdapter
from live.controller import TradingController
from live.executor import ExecutionResult, OrderExecutor
from live.risk import RiskManager
from live.models import CycleContext
from live.entry_planner import EntryPlanner
from live.exit_manager import ExitManager
from live.trade_recorder import TradeRecorder
from ml.inference import MLInference
from strategy.regime import RegimeResult, evaluate_regime
from strategy.mode_selector import estimate_volatility_from_prices, select_mode


@dataclass
class CycleResult:
    """Result of a single trading cycle.

    Attributes:
        timestamp: When the cycle ran.
        regime: Market regime evaluation.
        mode: Selected trading strategy mode (하이브리드 2단계).
        candidates_found: Number of screened candidates.
        buys_executed: Number of buy orders executed.
        sells_executed: Number of sell orders executed.
        errors: List of error messages.
        reason: Human-readable cycle summary (Korean).
    """
    timestamp: datetime = field(default_factory=now_kst)
    regime: MarketRegime = MarketRegime.NEUTRAL
    mode: str = "swing"
    candidates_found: int = 0
    buys_executed: int = 0
    entries_deferred: int = 0
    sells_executed: int = 0
    errors: list[str] = field(default_factory=list)
    reason: str = ""
    # Diagnosis data for dashboard (진단 현황)
    screened: list = field(default_factory=list)       # 스크리너 통과 종목
    predictions: list = field(default_factory=list)    # ML 예측 결과
    deferred_entries: list = field(default_factory=list) # 진입 보류
    mode_decision: dict | None = None                  # 모드 선택 정보
    regime_skipped: bool = False                       # 장 종료로 레짐 스킵
    preset_change: str | None = None                   # 자동 프리셋 변경 (preset name)
    minute_screened: list = field(default_factory=list) # 분봉 스크리닝 결과
    combined_screened: list = field(default_factory=list) # 통합 점수 (일봉+분봉)


class TradingOrchestrator:
    """Orchestrates the full automated trading cycle.

    Phase 2: This is now a thin coordinator.
    - Entry logic → self._entry_planner.plan_entries()
    - Exit logic  → self._exit_manager.check_exits()
    - Risk        → self._risk
    - Execution   → self._executor
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        model,
        risk_config: RiskConfig | None = None,
        buy_threshold: float = 0.65,
        sell_threshold: float = 0.35,
        position_budget_pct: float = 0.10,
        minute_model=None,
        strategy_config=None,
        minute_builder=None,
        db_url: str | None = None,
        db_pool_size: int = 10,
        db_max_overflow: int = 20,
    ):
        self._broker = broker
        self._risk = RiskManager(risk_config or RiskConfig(), strategy_config=strategy_config)
        self._strategy = strategy_config or StrategyConfig()
        self._controller = TradingController()
        self._executor = OrderExecutor(broker, self._risk, self._controller)
        self._inference = MLInference(model, buy_threshold, sell_threshold, minute_model=minute_model, strategy_config=self._strategy)
        self._minute_builder = minute_builder  # MinuteBarBuilder (optional, for WS minute bars)
        self._trading_allowed = True  # 09:10 이후 true (main.py에서 제어)
        self._position_budget_pct = position_budget_pct

        # Database for trade records
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from data.repository import TradeRepository, PositionRepository

        if db_url:
            self._db_engine = create_engine(
                db_url,
                pool_size=db_pool_size,
                max_overflow=db_max_overflow,
                echo=False,
            )
        else:
            db_path = str(Path(__file__).resolve().parent.parent / "data" / "ngsat.db")
            self._db_engine = create_engine(f"sqlite:///{db_path}", echo=False)

        Session = sessionmaker(bind=self._db_engine)
        self._Session = Session  # sessionmaker factory (thread-safe, for per-cycle writes)
        self._db_session = Session()
        self._trade_repo = TradeRepository(self._db_session)
        self._TradeRepo = TradeRepository  # class ref for per-cycle instantiation
        self._PositionRepo = PositionRepository

        # Phase 2 follow-up #1: TradeRecorder — centralized trade DB write
        self._trade_recorder = TradeRecorder(session_factory=Session)

        # Phase 2: EntryPlanner + ExitManager (injected with trade_recorder)
        self._entry_planner = EntryPlanner(
            executor=self._executor,
            inference=self._inference,
            risk=self._risk,
            trade_recorder=self._trade_recorder,
            strategy=self._strategy,
        )
        self._exit_manager = ExitManager(
            executor=self._executor,
            inference=self._inference,
            risk=self._risk,
            trade_recorder=self._trade_recorder,
        )

        # State
        self._last_regime: RegimeResult | None = None
        self._current_mode: str = "swing"
        self._last_diagnosis: dict | None = None  # 진단 현황
        self._preset_router: Any = None  # lazy init in run_cycle
        self._cycle_count: int = 0
        self._regime_skipped: bool = False
        # TR-13: 일일 거래 횟수 제한
        self._daily_trade_date: str = ""
        self._daily_trade_count: int = 0
        # C-2: 보유 포지션 코드 캐시 (main.py universe 구성용)
        self._last_held_codes: set[str] = set()
        # Pending buy trades → managed by TradeRecorder (Phase 2 follow-up #2)

    def refresh_read_session(self) -> None:
        """BE-10: 읽기 전용 세션 갱신 — dashboard 조회 전 호출."""
        try:
            if hasattr(self, '_db_session') and self._db_session:
                self._db_session.close()
        except Exception:
            pass
        Session = self._Session
        self._db_session = Session()
        if hasattr(self, '_TradeRepo'):
            self._trade_repo = self._TradeRepo(self._db_session)

    @property
    def controller(self) -> TradingController:
        return self._controller

    @property
    def risk_manager(self) -> RiskManager:
        return self._risk

    @property
    def entry_planner(self) -> EntryPlanner:
        return self._entry_planner

    @property
    def exit_manager(self) -> ExitManager:
        return self._exit_manager

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

        # P-54: 사이클 시작 시 idempotency 초기화
        self._executor.clear_idempotency()

        # Check controller state
        if not self._controller.is_running:
            result.reason = f"매매 대기 중 (상태: {self._controller.state.value})"
            return result

        if self._risk.is_halted:
            result.reason = f"리스크 중단: {self._risk.halt_reason}"
            return result

        logger.info(f"=== 매매 사이클 #{self._cycle_count} 시작 ===")

        # TR-13: 일일 거래 횟수 리셋
        today = now_kst().strftime("%Y-%m-%d")
        if self._daily_trade_date != today:
            self._daily_trade_date = today
            self._daily_trade_count = 0

        # ── Step 0: Universe sanity check ──
        for info, _ in stock_universe:
            name = getattr(info, 'name', str(info))
            if name.startswith("synthetic_"):
                logger.error(f"합성 유니버스 감지 — 사이클 스킵: {name}({getattr(info, 'code', '?')})")
                result.reason = f"합성 데이터 차단: {name}"
                return result

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

        # ── Build CycleContext ──
        market_open = is_market_hours()
        current_positions = await self._fetch_positions()
        held_codes = {p.code for p in current_positions}
        held_quantities: dict[str, int] = {p.code: p.quantity for p in current_positions}

        sector_lookup: dict[str, str] = {}
        for info, _ in stock_universe:
            sector_lookup[info.code] = info.sector

        held_sector_counts: dict[str, int] = {}
        for p in current_positions:
            sec = p.sector or sector_lookup.get(p.code, "")
            if sec:
                held_sector_counts[sec] = held_sector_counts.get(sec, 0) + 1

        ctx = CycleContext(
            cycle_number=self._cycle_count,
            account=account,
            current_positions=current_positions,
            held_codes=held_codes,
            held_quantities=held_quantities,
            index_prices=index_prices,
            stock_universe=stock_universe,
            sector_lookup=sector_lookup,
            held_sector_counts=held_sector_counts,
            market_open=market_open,
            trading_allowed=self._trading_allowed,
            daily_trade_date=self._daily_trade_date,
            daily_trade_count=self._daily_trade_count,
        )

        # ── Step 3-6: Regime → Entry → Exit (장중만 실행) ──
        regime_result = None
        if not market_open:
            result.regime_skipped = True
            self._regime_skipped = True
            logger.info("장 운영 시간 아님 — 레짐/진입/청산 생략")
            # 청산도 장중에만 실행 (09:00~15:30)
        else:
            self._regime_skipped = False

            # ── Step 3: Regime evaluation ──
            if self._cycle_count < 5 and self._last_regime is not None:
                regime_result = self._last_regime
                result.regime = regime_result.regime
                logger.info(f"레짐 평가 보류 (사이클 #{self._cycle_count}/5): 직전 레짐 유지")
            else:
                if len(index_prices) < 20:
                    result.reason = "인덱스 데이터 부족 — 레짐 평가 불가"
                    return result
                regime_result = evaluate_regime(
                    [p.close for p in index_prices],
                    [p.volume for p in index_prices],
                    [p.high for p in index_prices],
                    [p.low for p in index_prices],
                    config=self._strategy,
                    prev_regime=self._last_regime.regime if self._last_regime else None,
                )
                self._last_regime = regime_result
                result.regime = regime_result.regime
                logger.info(f"레짐 평가: {regime_result.regime.value} ({regime_result.score:.1f}점)")

            # ── TR-16: 장중 KOSPI 등락률 보정 (모드 결정 전에 적용) ──
            if self._entry_planner:
                try:
                    index_price = await self._broker.get_index_price()
                    if index_price is not None and len(index_prices) >= 2:
                        self._entry_planner._apply_intraday_correction(regime_result, index_price, index_prices)
                except Exception:
                    pass

            # ── Mode selection (하이브리드 2단계) ──
            vol = estimate_volatility_from_prices(
                [p.close for p in index_prices],
                [p.high for p in index_prices],
                [p.low for p in index_prices],
            )
            mode_decision = select_mode(regime_result, atr_pct=vol, config=self._strategy)
            self._current_mode = mode_decision.mode.value
            ctx.mode = mode_decision.mode
            ctx.mode_str = mode_decision.mode.value
            ctx.is_short_term = mode_decision.mode.value == "short_term"
            ctx.atr_vol_pct = vol
            self._risk.set_regime_context(regime_result.score, vol)
            self._risk.set_mode(self._current_mode)
            result.mode = self._current_mode
            result.mode_decision = {
                "mode": mode_decision.mode.value,
                "confidence": mode_decision.confidence,
                "reason": mode_decision.reason,
                "forward_days": mode_decision.forward_days,
            }
            logger.info(f"모드 선택: {mode_decision.mode.value} (신뢰도 {mode_decision.confidence:.0%}) — {mode_decision.reason}")

            # HOLD 모드: 신규 진입 금지
            if mode_decision.mode == StrategyMode.HOLD:
                logger.info("HOLD 모드 — 신규 진입 없이 청산만 실행")

            # ── Entry pipeline (EntryPlanner 위임) ──
            entry_result = await self._entry_planner.plan_entries(
                ctx=ctx,
                regime_result=regime_result,
                index_prices=index_prices,
                stock_universe=stock_universe,
                broker=self._broker,
            )

            # Merge entry results → CycleResult
            result.candidates_found = entry_result.get("candidates_found", 0)
            result.screened = entry_result.get("screened", [])
            result.minute_screened = entry_result.get("minute_screened", [])
            result.combined_screened = entry_result.get("combined_screened", [])
            result.predictions = entry_result.get("predictions", [])
            result.deferred_entries = entry_result.get("deferred_entries", [])
            result.buys_executed = entry_result.get("buys_executed", 0)
            result.entries_deferred = entry_result.get("entries_deferred", 0)
            result.errors.extend(entry_result.get("errors", []))
            result.preset_change = entry_result.get("preset_change")

        # ── Step 7: Exit pipeline (ExitManager 위임) — 장중에만 실행 ──
        if market_open:
            exit_result = await self._exit_manager.check_exits(
                ctx=ctx,
                broker=self._broker,
                stock_universe=stock_universe,
            )
        else:
            exit_result = {}
        result.sells_executed = exit_result.get("sells_executed", 0)
        result.errors.extend(exit_result.get("errors", []))

        # ── Confirm pending buy trades (via TradeRecorder) ──
        await self._trade_recorder.confirm_pending_buys(self._fetch_positions)

        # ── Build summary ──
        regime_str = (
            "레짐=스킵(장종료)"
            if result.regime_skipped
            else f"레짐={regime_result.regime.value}({regime_result.score:.0f}점)"
        ) if regime_result else "레짐=없음"
        result.reason = (
            f"사이클 #{self._cycle_count} 완료: "
            f"모드={self._current_mode}, "
            f"{regime_str}, "
            f"후보={result.candidates_found}개, "
            f"매수={result.buys_executed}건(보류 {result.entries_deferred}건), "
            f"매도={result.sells_executed}건"
        )
        if result.errors:
            result.reason += f", 오류={len(result.errors)}건"

        # Save diagnosis for dashboard
        self._last_diagnosis = {
            "timestamp": result.timestamp.isoformat(),
            "cycle": self._cycle_count,
            "regime": result.regime.value,
            "regime_score": getattr(self._last_regime, 'score', 0),
            "mode": result.mode,
            "mode_decision": result.mode_decision,
            "candidates_found": result.candidates_found,
            "buys": result.buys_executed,
            "sells": result.sells_executed,
            "deferred": result.entries_deferred,
            "screened": result.screened,
            "predictions": result.predictions,
            "deferred_entries": result.deferred_entries,
            "minute_screened": result.minute_screened,
            "combined_screened": result.combined_screened,
            "summary": result.reason,
            "minute_ml_status": (
                "정상(분봉ML)" if self._inference.has_minute_model
                else "미설정(일봉폴백)" if self._current_mode == "short_term"
                else "비활성(스윙모드)"
            ),
        }
        return result

    async def _fetch_positions(self) -> list[Position]:
        """Fetch current positions from broker."""
        try:
            positions = await self._broker.get_positions()
            self._last_held_codes = {p.code for p in positions}
            return positions
        except Exception as e:
            logger.error(f"포지션 조회 실패: {e}")
            return []

    async def force_sell(self, code: str, name: str = "") -> ExecutionResult:
        """Force sell a position — operator override."""
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

    async def get_account_summary(self) -> AccountSummary:
        """Public accessor for account summary (bot/UI용)."""
        return await self._broker.get_account_summary()

    async def close(self):
        """Clean up database engine and resources."""
        if hasattr(self, '_db_engine') and self._db_engine:
            try:
                self._db_engine.dispose()
            except Exception as e:
                logger.warning(f"DB 엔진 종료 중 오류: {e}")

    async def cancel_unfilled_orders(self, max_age_seconds: int = 30) -> int:
        """미체결 주문 중 일정 시간 경과한 주문을 취소."""
        now = now_kst()
        cancelled = 0
        try:
            unfilled = await self._broker.get_unfilled_orders()
        except Exception as e:
            logger.warning(f"미체결 주문 조회 실패: {e}")
            return 0
        for order in unfilled:
            try:
                ot = order.order_time
                if len(ot) >= 6:
                    order_dt = now.replace(
                        hour=int(ot[:2]), minute=int(ot[2:4]),
                        second=int(ot[4:6]), microsecond=0,
                    )
                    age = (now - order_dt).total_seconds()
                    if age < 0:
                        age += 86400
                    if age >= max_age_seconds:
                        ok = await self._broker.cancel_order(order.order_id)
                        if ok:
                            cancelled += 1
                            logger.info(f"미체결 취소: {order.name}({order.code}) {order.side} {order.quantity}주 (경과 {age:.0f}초)")
            except Exception as e:
                logger.warning(f"주문 취소 중 오류: {order.order_id}: {e}")
        if cancelled > 0:
            logger.info(f"미체결 주문 {cancelled}건 취소 완료")
        return cancelled
