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
import asyncio
import numpy as np

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
from strategy.indicators import atr as calc_atr


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
    regime_skipped: bool = False                       # 장 종료로 레짐 스킵
    preset_change: str | None = None                   # 자동 프리셋 변경 (preset name)
    minute_screened: list = field(default_factory=list) # 분봉 스크리닝 결과


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
        self._inference = MLInference(model, buy_threshold, sell_threshold, minute_model=minute_model)
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
        self._preset_router: Any = None  # lazy init in run_cycle
        self._minute_cache: dict[str, list[PriceData]] = {}  # per-cycle 분봉 캐시
        self._cycle_count: int = 0
        self._regime_skipped: bool = False         # 장 종료 시 레짐 미평가
        # TR-13: 일일 거래 횟수 제한
        self._daily_trade_date: str = ""
        self._daily_trade_count: int = 0

    def refresh_read_session(self) -> None:
        """BE-10: 읽기 전용 세션 갱신 — dashboard 조회 전 호출하여 데이터 신선도 유지."""
        try:
            if hasattr(self, '_db_session') and self._db_session:
                self._db_session.close()
        except Exception:
            pass
        Session = self._Session
        self._db_session = Session()
        if hasattr(self, '_TradeRepo'):
            self._trade_repo = self._TradeRepo(self._db_session)

    async def _check_correlation_risk(
        self,
        candidate_code: str,
        candidate_prices: list[PriceData],
        current_positions: list[Position],
        stock_universe: list[tuple[Any, list[PriceData]]],
    ) -> tuple[bool, str]:
        """TR-7: 포트폴리오 상관관계 검사.

        후보 종목과 보유 포지션 간 가격 수익률 상관계수가
        임계값(0.85)을 초과하면 진입을 보류.

        Returns:
            (is_safe: bool, reason: str)
        """
        if len(current_positions) < 2:
            return True, ""

        closes_map: dict[str, list[float]] = {candidate_code: [p.close for p in candidate_prices]}
        for info, prices in stock_universe:
            if info.code in {p.code for p in current_positions}:
                closes_map[info.code] = [p.close for p in prices]

        import numpy as np

        cand_returns = np.diff(np.log(closes_map[candidate_code][-60:]))

        high_corr_count = 0
        total_checked = 0
        threshold = 0.85

        for pos in current_positions:
            p_prices = closes_map.get(pos.code)
            if p_prices is None or len(p_prices) < 60:
                continue
            pos_returns = np.diff(np.log(p_prices[-60:]))
            if len(cand_returns) != len(pos_returns):
                continue
            corr = np.corrcoef(cand_returns, pos_returns)[0, 1]
            if not np.isnan(corr) and abs(corr) > threshold:
                high_corr_count += 1
            total_checked += 1

        if total_checked == 0:
            return True, ""

        avg_corr = high_corr_count / total_checked
        if avg_corr > 0.5:
            return False, (
                f"포트폴리오 상관관계 주의: {high_corr_count}/{total_checked}개 포지션 "
                f"상관계수 {threshold:.0%} 초과"
            )
        return True, ""

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

        # ── Step 3-6: Regime → Mode → Screening → Entry (장중만 실행) ──
        market_open = is_market_hours()
        current_positions = await self._fetch_positions()
        held_codes = {p.code for p in current_positions}
        regime_result = None
        is_short_term = False
        screen_result = None
        if not market_open:
            result.regime_skipped = True
            self._regime_skipped = True
            logger.info("장 운영 시간 아님 — 레짐/스크리닝/진입 생략, 청산만 실행")
        else:
            self._regime_skipped = False
            # ── Step 3: Regime evaluation ──
            # 안전장치: 서버 재시작 후 KOSPI 지수 데이터 안정화까지
            # 처음 5사이클은 직전 레짐 유지 (급변 방지)
            if self._cycle_count < 5 and self._last_regime is not None:
                regime_result = self._last_regime
                result.regime = regime_result.regime
                logger.info(
                    f"레짐 평가 보류 (사이클 #{self._cycle_count}/5): "
                    f"직전 레짐 유지 — {regime_result.regime.value} "
                    f"({regime_result.score:.1f}점)"
                )
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

            # ── TR-16: 장중 KOSPI 등락률 보정 (B안) ──
            # intraday: fetch current KOSPI index, compare to yesterday close
            try:
                index_price = await self._broker.get_index_price()
            except Exception:
                index_price = None
            if index_price is not None and len(index_prices) >= 2:
                prev_close = index_prices[-1].close
                intraday_change_pct = ((index_price.close - prev_close) / prev_close) * 100
                # Map to ±5 point correction, capped
                correction = intraday_change_pct * 1.67  # ±3% → ±5점
                correction = max(-5.0, min(5.0, correction))
                if abs(correction) >= 0.5:
                    old_score = regime_result.score
                    new_score = max(0.0, min(100.0, old_score + correction))
                    regime_result = RegimeResult(
                        regime=regime_result.regime,
                        score=new_score,
                        reason=regime_result.reason + (
                            f" | 장중보정: KOSPI {intraday_change_pct:+.1f}% "
                            f"({correction:+.0f}점, {old_score:.0f}→{new_score:.0f})"
                        ),
                        evidence={**regime_result.evidence, "intraday_correction": correction},
                    )
                    self._last_regime = regime_result
                    result.regime = regime_result.regime
                    logger.info(f"장중 KOSPI {intraday_change_pct:+.1f}% — 레짐 보정 {correction:+.0f}점")
            else:
                logger.debug("장중 레짐 보정: KOSPI 현재가 미조회, 생략")

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

            # ── Step 4b: Auto preset selection ──
            if self._preset_router is None:
                from strategy.preset_router import PresetRouter
                self._preset_router = PresetRouter()
            new_preset = self._preset_router.select_preset(regime_result, atr_pct=vol)
            if new_preset:
                logger.info(f"프리셋 자동 전환: {self._preset_router.current_preset} → {new_preset}")
                result.preset_change = new_preset

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

            # ── Step 5b: Minute screening (분봉 기반 실시간 점수) ──
            if market_open and screen_result.candidates:
                try:
                    from strategy.minute_screener import screen_by_minute
                    # Fetch minute data for top candidates + 캐시 저장
                    minute_data = {}
                    names = {}
                    self._minute_cache.clear()
                    for c in screen_result.candidates:  # 전체 후보 분봉 조회 (WebSocket 캐시, API 0회)
                        mp = await self._fetch_minute_prices(c.code)
                        await asyncio.sleep(0.1)  # ★ Rate Limit 보호
                        if mp and len(mp) >= 20:
                            minute_data[c.code] = mp
                            self._minute_cache[c.code] = mp  # 캐시 저장
                            names[c.code] = c.name
                    if minute_data:
                        minute_scores = screen_by_minute(minute_data)
                        # Merge names
                        for ms in minute_scores:
                            if ms.code in names and not ms.name:
                                ms.name = names[ms.code]
                        result.minute_screened = [
                            {"code": s.code, "name": s.name, "score": s.score,
                             "rsi": s.minute_rsi, "momentum": s.momentum_5m,
                             "volume": s.volume_spike, "volatility": s.volatility_pct}
                            for s in minute_scores[:10]
                        ]
                        logger.info(f"분봉 스크리닝: {len(minute_scores)}개 점수 산출")
                except Exception as e:
                    logger.warning(f"분봉 스크리닝 실패 (skip): {e}")

            # ── Step 6: ML predictions & buy execution (모드별 라우팅) ──
            # market_open은 이미 True — 진입 진행
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

            logger.info("장 운영 시간 — 정상 진입 실행")

            # 09:10 이전: 신규 진입 차단 (데이터 수집 모드)
            if not self._trading_allowed:
                logger.info("09:10 이전 — 데이터 수집 모드, 신규 진입 보류")
                # 청산은 실행됨 (별도 청산 루프는 영향 없음)

        for candidate in (screen_result.candidates if screen_result else []):
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

            # ── ML predictions (모든 모드에서 평가 실행) ──
            # Find price data for this candidate
            prices = self._find_prices(stock_universe, candidate.code)
            if prices is None or len(prices) < 60:
                continue

            if is_short_term:
                # 단타 모드: 분봉 ML로 진입 예측 (캐시 우선)
                minute_prices = self._minute_cache.get(candidate.code) or await self._fetch_minute_prices(candidate.code)
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

            # HOLD 모드: ML 평가만 기록하고 매수 실행은 차단
            if self._current_mode == "hold":
                continue

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
                base_budget_pct = self._risk.position_size_pct
                # ATR-based dynamic position sizing (최소 = base 보장)
                target_vol_pct = 1.5
                min_pct = base_budget_pct * 1.0
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

                # TR-7: 포트폴리오 상관관계 검사
                corr_safe, corr_reason = await self._check_correlation_risk(
                    candidate_code=candidate.code,
                    candidate_prices=prices,
                    current_positions=current_positions,
                    stock_universe=stock_universe,
                )
                if not corr_safe:
                    logger.info(f"상관관계 리스크: {candidate.name}({candidate.code}) — {corr_reason}")
                    result.entries_deferred += 1
                    result.deferred_entries.append(
                        {"code": candidate.code, "name": candidate.name,
                         "reason": corr_reason, "probability": 0}
                    )
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

            # 0) 트레일링 스탑 업데이트 + 트리거 검사 (P1-1)
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
                exec_result = await self._executor.execute_sell(
                    code=position.code,
                    name=position.name,
                    quantity=position.quantity,
                    price=sell_price,
                    action=DecisionAction.STOP_LOSS,
                    reason=(
                        f"{trail_result.reason} || 청산정밀화: {exit_ref.reason}"
                    ),
                )
                self._record_sell(
                    result, exec_result, position, sell_price,
                    action=DecisionAction.STOP_LOSS,
                    log_label="트레일링스탑",
                    reason=trail_result.reason,
                )
                continue

            # 0.5) 부분 익절 검사 (P1-2) — 손절 전, 수익중인 경우만
            partial_tp = self._risk.check_partial_take_profit(position)
            if partial_tp["should_sell"] and partial_tp["sell_quantity"] > 0:
                exec_result = await self._executor.execute_sell(
                    code=position.code,
                    name=position.name,
                    quantity=partial_tp["sell_quantity"],
                    price=sell_price,
                    action=DecisionAction.SELL,
                    reason=partial_tp["reason"],
                )
                if exec_result.success:
                    # 부분 매도 DB 기록 (close_position 대신 update_position_quantity)
                    self._record_sell(
                        result, exec_result, position, sell_price,
                        action=DecisionAction.SELL,
                        log_label="부분 익절",
                        reason=partial_tp["reason"],
                        partial_sold_qty=partial_tp["sell_quantity"],
                    )
                    from dataclasses import replace as dc_replace_tp
                    remaining = position.quantity - partial_tp["sell_quantity"]
                    tp1_done = position.partial_tp1_executed or (partial_tp["tp_stage"] == 1)
                    tp2_done = position.partial_tp2_executed or (partial_tp["tp_stage"] == 2)
                    position = dc_replace_tp(
                        position,
                        quantity=remaining,
                        partial_tp1_executed=tp1_done,
                        partial_tp2_executed=tp2_done,
                        original_quantity=position.original_quantity or position.quantity + partial_tp["sell_quantity"],
                    )
                    logger.info(
                        f"부분 익절 완료: {position.name}({position.code}) "
                        f"{partial_tp['sell_quantity']}주 매도, 잔여 {remaining}주"
                    )
                else:
                    result.errors.append(f"부분 익절 실패 {position.code}: {exec_result.error}")
                # 부분 매도 후에도 계속 손절/ML 청산 검사 (잔여 포지션에 대해)
                # 단, 잔량이 0이면 다음 position으로
                if position.quantity <= 0:
                    continue

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
                self._record_sell(
                    result, exec_result, position, sell_price,
                    action=DecisionAction.STOP_LOSS,
                    log_label="손절",
                    reason=f"손절: {position.name}({position.code}) 손실 {loss_pct:.1f}%",
                )
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
                self._record_sell(
                    result, exec_result, position, sell_price,
                    action=DecisionAction.SELL,
                    log_label="매도",
                    reason=f"분봉 청산: {exit_ref.reason}",
                )
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
                self._record_sell(
                    result, exec_result, position, sell_price,
                    action=DecisionAction.SELL,
                    log_label="매도",
                    reason=f"{exit_pred.reason} || 청산정밀화: {exit_ref.reason}",
                )

        # ── Build summary ──
        regime_str = (
            f"레짐=스킵(장종료)"
            if result.regime_skipped
            else f"레짐={regime_result.regime.value}({regime_result.score:.0f}점)"
        )
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

        Checks the local MinuteBarBuilder cache first (WebSocket-fed).
        Falls back to REST API if insufficient local data.

        Returns None if adapter doesn't support minute data.
        """
        # Local WS cache first
        if self._minute_builder is not None:
            bars = self._minute_builder.get_bars(code, 60)
            if len(bars) >= 30:
                return bars

        try:
            return await self._broker.get_minute_history(code)
        except Exception as e:
            logger.warning(f"분봉 조회 실패({code}): {type(e).__name__}")
            return None

    async def _refine_entry(self, code: str, use_minute: bool = True) -> EntryDecision:
        """분봉으로 진입 타이밍/가격을 정밀화. 분봉 미가용 시 시장가 진입 폴백."""
        try:
            # 캐시 우선, 없으면 API 호출
            minute_prices = self._minute_cache.get(code)
            if minute_prices is None:
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

    def _record_sell(
        self, result, exec_result, position, sell_price, action, log_label, reason,
        partial_sold_qty: int | None = None,
    ) -> None:
        """Record a sell trade to DB + update cycle result.

        Args:
            partial_sold_qty: Actual shares sold in a partial sell.
                When set (and < position.quantity), updates position quantity
                instead of closing the position. Default None = full close.
        """
        if not exec_result.success:
            result.errors.append(f"{log_label} 실패 {position.code}: {exec_result.error}")
            return
        result.sells_executed += 1
        try:
            # 실제 매도 수량 (부분 청산 시 partial_sold_qty, 아니면 exec_result)
            sold_qty = partial_sold_qty or exec_result.quantity or position.quantity
            is_partial = partial_sold_qty is not None and partial_sold_qty < position.quantity
            with self._Session() as session:
                self._TradeRepo(session).save_trade(
                    code=position.code, name=position.name,
                    side=OrderSide.SELL, quantity=sold_qty,
                    price=exec_result.price or sell_price or 0,
                    amount=exec_result.amount or (sold_qty * (exec_result.price or sell_price or 0)),
                    action=action, reason=reason,
                )
                if is_partial:
                    self._PositionRepo(session).update_position_quantity(
                        code=position.code, sold_quantity=sold_qty,
                    )
                else:
                    self._PositionRepo(session).close_position(
                        code=position.code, final_profit_loss=position.profit_loss_pct
                    )
                session.commit()
        except Exception as e:
            logger.error(f"{log_label} 기록 저장 실패: {e}")

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

    async def cancel_unfilled_orders(self, max_age_seconds: int = 30) -> int:
        """미체결 주문 중 일정 시간 경과한 주문을 취소.

        매 사이클마다 실행:
        1. KIS 미체결 주문 목록 조회
        2. 주문 시각과 현재 시각 비교
        3. max_age_seconds 초과 시 주문 취소

        Args:
            max_age_seconds: 취소 기준 시간(초). 기본 30초.

        Returns:
            취소한 주문 개수.
        """
        now = datetime.now()
        cancelled = 0

        try:
            unfilled = await self._broker.get_unfilled_orders()
        except Exception as e:
            logger.warning(f"미체결 주문 조회 실패: {e}")
            return 0

        for order in unfilled:
            try:
                # 주문 시각 파싱 (HHMMSS 형식)
                ot = order.order_time
                if len(ot) >= 6:
                    order_dt = now.replace(
                        hour=int(ot[:2]), minute=int(ot[2:4]),
                        second=int(ot[4:6]),
                    )
                    age = (now - order_dt).total_seconds()
                    if age < 0:
                        age += 86400  # 자정 넘김 처리

                    if age >= max_age_seconds:
                        ok = await self._broker.cancel_order(order.order_id)
                        if ok:
                            cancelled += 1
                            logger.info(
                                f"미체결 취소: {order.name}({order.code}) "
                                f"{order.side} {order.quantity}주 "
                                f"{order.price:,.0f}원 (경과 {age:.0f}초)"
                            )
            except Exception as e:
                logger.warning(f"주문 취소 중 오류: {order.order_id}: {e}")

        if cancelled > 0:
            logger.info(f"미체결 주문 {cancelled}건 취소 완료")
        return cancelled
