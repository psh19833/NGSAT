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

from core.config import RiskConfig, StrategyConfig
from core.logger import logger
from pathlib import Path
from core.types import (
    DecisionAction,
    MarketRegime,
    OrderSide,
    Position,
    PriceData,
    StrategyMode,
    is_market_hours,
)
from data.adapters.base import BrokerAdapter
from live.controller import TradingController
from live.executor import ExecutionResult, OrderExecutor
from live.risk import RiskManager
from ml.inference import MLInference
from strategy.regime import RegimeResult, evaluate_regime
from strategy.screener import screen_stocks
from strategy.entry_timing import EntryDecision, EntryTiming, refine_entry
from strategy.exit_timing import ExitDecision, ExitUrgency, refine_exit
from strategy.mode_selector import select_mode, estimate_volatility_from_prices


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
    timestamp: datetime = field(default_factory=datetime.now)
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
        minute_model=None,
        strategy_config=None,
        db_url: str | None = None,
        db_pool_size: int = 10,
        db_max_overflow: int = 20,
    ):
        self._broker = broker
        self._risk = RiskManager(risk_config or RiskConfig(), strategy_config=strategy_config)
        self._strategy = strategy_config or StrategyConfig()
        self._controller = TradingController()
        self._executor = OrderExecutor(broker, self._risk, self._controller)
        self._inference = MLInference(model, buy_threshold, sell_threshold, minute_model=minute_model)
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
            # Fallback: SQLite (localhost / dev)
            db_path = str(Path(__file__).resolve().parent.parent / "data" / "ngsat.db")
            self._db_engine = create_engine(f"sqlite:///{db_path}", echo=False)

        Session = sessionmaker(bind=self._db_engine)
        self._Session = Session  # sessionmaker factory (thread-safe, for per-cycle writes)
        # Separate read-only session + repo for dashboard queries
        self._db_session = Session()
        self._TradeRepo = TradeRepository  # class ref for per-cycle instantiation
        self._trade_repo = TradeRepository(self._db_session)
        self._PositionRepo = PositionRepository

        # State
        self._last_regime: RegimeResult | None = None
        self._current_mode: str = "swing"
        self._last_diagnosis: dict | None = None  # 진단 현황
        self._cycle_count: int = 0
        # TR-13: 일일 거래 횟수 제한
        self._daily_trade_date: str = ""
        self._daily_trade_count: int = 0

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

        # TR-13: 일일 거래 횟수 리셋 (날짜 변경 시)
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_trade_date != today:
            self._daily_trade_date = today
            self._daily_trade_count = 0
            logger.info(f"일일 거래 횟수 리셋 ({today})")

        # ── Step 0: Universe sanity check (synthetic data guard) ──
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

        # ── Step 3: Regime evaluation ──
        if len(index_prices) < 20:
            result.reason = "인덱스 데이터 부족 — 레짐 평가 불가"
            return result

        regime_result = evaluate_regime(
            [p.close for p in index_prices],
            [p.volume for p in index_prices],
            [p.high for p in index_prices],
            [p.low for p in index_prices],
            config=self._strategy,
        )
        self._last_regime = regime_result
        result.regime = regime_result.regime

        logger.info(f"레짐 평가: {regime_result.regime.value} ({regime_result.score:.1f}점)")

        # ── Step 4A: Mode selection (하이브리드 2단계) ──
        vol = estimate_volatility_from_prices(
            [p.close for p in index_prices],
            [p.high for p in index_prices],
            [p.low for p in index_prices],
        )
        mode_decision = select_mode(regime_result, atr_pct=vol, config=self._strategy)
        self._current_mode = mode_decision.mode.value
        result.mode_decision = {
            "mode": mode_decision.mode.value,
            "confidence": mode_decision.confidence,
            "reason": mode_decision.reason,
            "forward_days": mode_decision.forward_days,
        }
        self._risk.set_mode(self._current_mode)
        result.mode = self._current_mode

        logger.info(f"모드 선택: {mode_decision.mode.value} (신뢰도 {mode_decision.confidence:.0%}) — {mode_decision.reason}")

        # HOLD 모드: 신규 진입 금지, 청산만 실행
        if mode_decision.mode == StrategyMode.HOLD:
            logger.info("HOLD 모드 — 신규 진입 없이 기존 포지션 청산만 실행")

        # ── Step 5: Screen stocks ──
        screen_result = screen_stocks(stock_universe, regime_result, config=self._strategy)
        result.candidates_found = len(screen_result.candidates)
        result.screened = [
            {"code": c.code, "name": c.name, "score": round(c.score, 1),
             "reason": c.reason}
            for c in screen_result.candidates
        ]
        logger.info(f"스크리닝: {screen_result.total_scanned}개 → {result.candidates_found}개 후보")

        # ── Step 6: ML predictions & buy execution (모드별 라우팅) ──
        current_positions = await self._fetch_positions()
        held_codes = {p.code for p in current_positions}
        market_open = is_market_hours()
        if not market_open:
            logger.info("장 운영 시간 아님 — 신규 진입 생략 (매도만 실행)")

        is_short_term = self._current_mode == "short_term"

        # ── TR-5: 섹터 집중도 체크를 위한 섹터 룩업 ──
        sector_lookup: dict[str, str] = {}
        for info, _ in stock_universe:
            sector_lookup[info.code] = info.sector

        held_sector_counts: dict[str, int] = {}
        for p in current_positions:
            sec = p.sector or sector_lookup.get(p.code, "")
            if sec:
                held_sector_counts[sec] = held_sector_counts.get(sec, 0) + 1

        for candidate in screen_result.candidates:
            if not market_open:
                break
            if candidate.code in held_codes:
                continue  # Already holding

            # 포지션 리스크: 최대 보유 종목 수 체크 (break = 루프 종료, 청산 루프는 별도)
            if self._strategy.max_holdings > 0 and len(held_codes) >= self._strategy.max_holdings:
                logger.info(
                    f"최대 보유 종목({self._strategy.max_holdings}개) 도달 — 신규 진입 생략"
                )
                break

            # 섹터 집중도 체크 (TR-5): 동일 업종 N개 초과 시 진입 불가
            candidate_sector = sector_lookup.get(candidate.code, "")
            max_sec = self._strategy.max_sector_concentration
            if candidate_sector and max_sec > 0:
                current_sector_count = held_sector_counts.get(candidate_sector, 0)
                if current_sector_count >= max_sec:
                    logger.info(
                        f"섹터 집중도 제한: {candidate.name}({candidate.code}) "
                        f"업종={candidate_sector} {current_sector_count}/{max_sec} — 진입 생략"
                    )
                    result.entries_deferred += 1
                    continue

            # HOLD 모드: 신규 진입 금지
            if self._current_mode == "hold":
                continue

            # Find price data for this candidate
            prices = self._find_prices(stock_universe, candidate.code)
            if prices is None or len(prices) < 60:
                continue

            if is_short_term:
                # 단타 모드: 분봉 ML로 진입 예측
                minute_prices = await self._fetch_minute_prices(candidate.code)
                if minute_prices and len(minute_prices) >= 60:
                    pred = self._inference.predict_minute_entry(candidate, minute_prices)
                else:
                    pred = self._inference.predict_entry(candidate, prices)
            else:
                # 스윙 모드: 일봉 ML로 진입 예측 (기존)
                pred = self._inference.predict_entry(candidate, prices)

            # Record prediction for diagnosis
            if pred:
                result.predictions.append({
                    "code": pred.code, "name": pred.name,
                    "action": pred.action.value,
                    "probability": round(pred.rise_probability, 3),
                    "reason": pred.reason,
                })

            if pred and pred.action == DecisionAction.BUY:
                # 진입 정밀화: 분봉으로 타이밍/가격 판단 (하이브리드 1단계, 양 모드 공통)
                entry = await self._refine_entry(pred.code, use_minute=is_short_term)
                if not entry.should_enter:
                    result.entries_deferred += 1
                    result.deferred_entries.append(
                        {"code": pred.code, "name": pred.name,
                         "reason": entry.reason, "probability": round(pred.rise_probability, 3)}
                    )
                    logger.info(f"진입 보류: {pred.name}({pred.code}) — {entry.reason}")
                    continue

                ref_price = entry.limit_price or prices[-1].close
                base_budget_pct = self._position_budget_pct
                # ATR-based dynamic position sizing
                # High volatility → reduce position, Low volatility → increase (within limits)
                target_vol_pct = 1.5  # 기준 변동성(%): 이 값에서 base_pct = full position
                min_pct = base_budget_pct * 0.3
                max_pct = base_budget_pct * 2.0
                vol_pct = max(vol, 0.5)  # vol은 이미 백분율 (std/mean*100)
                adjusted_pct = base_budget_pct * (target_vol_pct / vol_pct)
                adjusted_pct = max(min_pct, min(adjusted_pct, max_pct))
                budget = account.deposit * adjusted_pct
                quantity = int(budget / ref_price) if ref_price > 0 else 0

                if quantity <= 0:
                    continue

                # TR-13: 일일 거래 횟수 제한
                if self._strategy.daily_trade_limit > 0 and self._daily_trade_count >= self._strategy.daily_trade_limit:
                    logger.info(f"일일 거래 횟수 제한 ({self._strategy.daily_trade_limit}회) 도달 — 진입 생략")
                    result.entries_deferred += 1
                    continue

                # TR-14: 총 노출 한도 체크
                max_exposure = account.total_asset * (self._strategy.max_total_exposure_pct / 100.0)
                current_exposure = sum(
                    p.eval_amount or (p.current_price * p.quantity)
                    for p in current_positions
                )
                new_exposure = ref_price * quantity
                if current_exposure + new_exposure > max_exposure:
                    logger.info(f"총 노출 한도 초과: {current_exposure:,.0f}+{new_exposure:,.0f} > {max_exposure:,.0f} — 진입 생략")
                    result.entries_deferred += 1
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
                    self._daily_trade_count += 1
                    # Record to database (per-cycle session for isolation)
                    try:
                        with self._Session() as session:
                            trade_repo = self._TradeRepo(session)
                            trade_repo.save_trade(
                                code=pred.code, name=pred.name,
                                side=OrderSide.BUY, quantity=quantity,
                                price=exec_result.price or ref_price,
                                amount=exec_result.amount or (quantity * (exec_result.price or ref_price)),
                                action=pred.action, reason=buy_reason,
                            )
                            session.commit()
                    except Exception as e:
                        logger.error(f"거래 기록 저장 실패: {e}")
                    held_codes.add(pred.code)
                else:
                    result.errors.append(f"매수 실패 {pred.code}: {exec_result.error}")

        # ── Step 7: Exit check for existing positions (모드별 라우팅) ──
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
            effective_stop = self._risk.effective_stop_loss_pct or position.stop_loss_pct
            if loss_pct >= effective_stop:
                exec_result = await self._executor.execute_sell(
                    code=position.code,
                    name=position.name,
                    quantity=position.quantity,
                    price=sell_price,
                    action=DecisionAction.STOP_LOSS,
                    reason=(
                        f"손절: {position.name}({position.code}) "
                        f"손실 {loss_pct:.1f}% >= 손절선 {effective_stop:.1f}% "
                        f"|| 청산정밀화: {exit_ref.reason}"
                    ),
                )
                if exec_result.success:
                    result.sells_executed += 1
                    try:
                        with self._Session() as session:
                            self._TradeRepo(session).save_trade(
                                code=position.code, name=position.name,
                                side=OrderSide.SELL, quantity=position.quantity,
                                price=exec_result.price or sell_price or 0,
                                amount=exec_result.amount or (position.quantity * (exec_result.price or sell_price or 0)),
                                action=DecisionAction.STOP_LOSS,
                                reason=f"손절: {position.name}({position.code}) 손실 {loss_pct:.1f}%",
                            )
                            self._PositionRepo(session).close_position(
                                code=position.code, final_profit_loss=position.profit_loss_pct
                            )
                            session.commit()
                    except Exception as e:
                        logger.error(f"손절 기록 저장 실패: {e}")
                else:
                    result.errors.append(f"손절 실패 {position.code}: {exec_result.error}")
                continue

            # 2) 분봉 선제 청산 (일봉 ML보다 빠른 급락/과열익절 신호) — 양 모드 공통
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
                    try:
                        with self._Session() as session:
                            self._TradeRepo(session).save_trade(
                                code=position.code, name=position.name,
                                side=OrderSide.SELL, quantity=position.quantity,
                                price=exec_result.price or sell_price or 0,
                                amount=exec_result.amount or (position.quantity * (exec_result.price or sell_price or 0)),
                                action=DecisionAction.SELL,
                                reason=f"분봉 청산: {exit_ref.reason}",
                            )
                            self._PositionRepo(session).close_position(
                                code=position.code, final_profit_loss=position.profit_loss_pct
                            )
                            session.commit()
                    except Exception as e:
                        logger.error(f"매도 기록 저장 실패: {e}")
                else:
                    result.errors.append(f"매도 실패 {position.code}: {exec_result.error}")
                continue

            # 3) ML 청산 예측 (모드별 라우팅)
            if is_short_term:
                # 단타 모드: 분봉 ML로 청산 예측
                minute_prices = await self._fetch_minute_prices(position.code)
                if minute_prices and len(minute_prices) >= 60:
                    exit_pred = self._inference.predict_minute_exit(
                        position.code, position.name, minute_prices, position.profit_loss_pct
                    )
                else:
                    exit_pred = self._inference.predict_exit(
                        position.code, position.name, prices, position.profit_loss_pct
                    )
            else:
                # 스윙 모드: 일봉 ML로 청산 예측 (기존)
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
                    try:
                        with self._Session() as session:
                            self._TradeRepo(session).save_trade(
                                code=position.code, name=position.name,
                                side=OrderSide.SELL, quantity=position.quantity,
                                price=exec_result.price or sell_price or 0,
                                amount=exec_result.amount or (position.quantity * (exec_result.price or sell_price or 0)),
                                action=DecisionAction.SELL,
                                reason=f"{exit_pred.reason} || 청산정밀화: {exit_ref.reason}",
                            )
                            self._PositionRepo(session).close_position(
                                code=position.code, final_profit_loss=position.profit_loss_pct
                            )
                            session.commit()
                    except Exception as e:
                        logger.error(f"매도 기록 저장 실패: {e}")
                else:
                    result.errors.append(f"매도 실패 {position.code}: {exec_result.error}")

        # ── Build summary ──
        result.reason = (
            f"사이클 #{self._cycle_count} 완료: "
            f"모드={self._current_mode}, "
            f"레짐={regime_result.regime.value}({regime_result.score:.0f}점), "
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
            "summary": result.reason,
        }
        return result

    async def _fetch_positions(self) -> list[Position]:
        """Fetch current positions from broker."""
        try:
            return await self._broker.get_positions()
        except Exception as e:
            logger.error(f"포지션 조회 실패: {e}")
            return []

    async def _fetch_minute_prices(self, code: str) -> list[PriceData] | None:
        """Fetch minute-candle data for a stock.

        Returns None if adapter doesn't support minute data.
        """
        try:
            return await self._broker.get_minute_history(code)
        except Exception as e:
            logger.warning(f"분봉 조회 실패({code}): {type(e).__name__}")
            return None

    async def _refine_entry(self, code: str, use_minute: bool = True) -> EntryDecision:
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

    async def close(self):
        """Clean up database engine and resources."""
        if hasattr(self, '_db_engine') and self._db_engine:
            try:
                self._db_engine.dispose()
            except Exception as e:
                logger.warning(f"DB 엔진 종료 중 오류: {e}")
