"""NGSAT live trading — Entry Planner (Phase 2 분할).

Responsible for:
- Stock screening (Step 5, 5b)
- ML predictions & buy execution (Step 6)
- Position sizing, sector concentration, correlation checks
- Entry refinement (minute-based timing)

Orchestrator creates one EntryPlanner and calls `plan_entries()` each cycle.
"""
from __future__ import annotations

from dataclasses import replace as dc_replace
import dataclasses
from typing import Any

import numpy as np

from core.config import StrategyConfig
from core.logger import logger
from core.types import (
    DecisionAction,
    Position,
    PriceData,
    StrategyMode,
)
from live.executor import OrderExecutor
from live.models import CycleContext
from live.risk import RiskManager
from live.trade_recorder import TradeRecorder
from ml.inference import MLInference
from strategy.entry_timing import EntryDecision, EntryTiming, refine_entry
from strategy.indicators import sma
from strategy.mode_selector import estimate_volatility_from_prices
from strategy.screener import screen_stocks


class EntryPlanner:
    """주식 종목 선정 → 진입 실행까지의 파이프라인.

    한 사이클에서 이 planner는 orchestrator로부터 CycleContext를 받아:
    1. 스크리닝 실행 (일봉 + 분봉)
    2. 후보별 ML 평가
    3. 리스크 체크 (섹터, 상관관계, 노출한도)
    4. 포지션 사이징
    5. 실제 매수 실행 (executor 위임)
    """

    def __init__(
        self,
        executor: OrderExecutor,
        inference: MLInference,
        risk: RiskManager,
        trade_recorder: TradeRecorder,
        strategy: StrategyConfig | None = None,
    ) -> None:
        self._executor = executor
        self._inference = inference
        self._risk = risk
        self._trade_recorder = trade_recorder
        self._strategy = strategy or StrategyConfig()
        from strategy.preset_router import PresetRouter
        self._preset_router = PresetRouter()

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    async def plan_entries(
        self,
        ctx: CycleContext,
        regime_result,  # RegimeResult
        index_prices: list[PriceData],
        stock_universe: list[tuple[Any, list[PriceData]]],
        broker,  # BrokerAdapter (for index_price / minute calls)
    ) -> dict:
        """Execute the full entry pipeline.

        Returns:
            dict with keys used by orchestrator to build CycleResult:
                candidates_found, screened, minute_screened,
                predictions, deferred_entries, buys_executed,
                entries_deferred, errors
        """
        result: dict = {
            "candidates_found": 0,
            "screened": [],
            "minute_screened": [],
            "mtf_filtered": [],  # P-83: 다중 TF 추세 필터 통과 실패 목록
            "predictions": [],
            "deferred_entries": [],
            "buys_executed": 0,
            "entries_deferred": 0,
            "errors": [],
            "preset_change": None,
        }

        if not ctx.market_open:
            return result

        # ── Step 4A: Mode selection ──
        vol = estimate_volatility_from_prices(
            [p.close for p in index_prices],
            [p.high for p in index_prices],
            [p.low for p in index_prices],
        )
        ctx.atr_vol_pct = vol

        # ── Step 4b: Auto preset ──
        preset_change = await self._select_preset(regime_result, vol)
        if preset_change:
            result["preset_change"] = preset_change

        # ── P-86: 급등일 모드 감지 ──
        intraday_correction = abs(
            getattr(regime_result, 'evidence', {}).get("intraday_correction", 0)
        )
        is_surge_day = intraday_correction >= self._strategy.surge_day_min_correction
        if is_surge_day:
            logger.info(
                f"급등일 모드 활성: 장중보정 {intraday_correction:.0f}점, "
                f"MTF={self._strategy.surge_day_mtf_threshold:.2f}, "
                f"ML buy≥{self._strategy.surge_day_buy_threshold:.0%}"
            )

        # ── Step 5: Screen stocks ──
        screen_result = screen_stocks(
            stock_universe, regime_result,
            config=self._strategy, index_prices=index_prices,
        )
        # ETN/ETF 종목 제외
        if screen_result.candidates:
            filtered = [c for c in screen_result.candidates if c.product_type == "stock"]
            screen_result = dc_replace(screen_result, candidates=filtered)

        # ── P-85: 외인/기관 수급 데이터 조회 (후보 종목만) ──
        investor_scores: dict[str, float] = {}
        for c in screen_result.candidates[:10]:
            try:
                from strategy.scorer import score_investor_flow
                data = await broker.get_investor_data(c.code)
                if data:
                    investor_scores[c.code] = score_investor_flow(data)
            except Exception:
                pass
        if investor_scores:
            updated_candidates = []
            from dataclasses import replace as dc_replace_cand
            for c in screen_result.candidates:
                inv_score = investor_scores.get(c.code)
                if inv_score is not None:
                    # 수급 점수 가중치 반영 (bear 10, total ~100 → 약 10% 반영)
                    bonus = (inv_score - 50.0) * 0.1  # ±5점 범위
                    new_score = max(0, min(100, c.score + bonus))
                    c = dc_replace_cand(c, score=new_score,
                        indicators={**c.indicators, "investor_score": round(inv_score, 1)})
                updated_candidates.append(c)
            screen_result = dc_replace(screen_result, candidates=updated_candidates)

        result["candidates_found"] = len(screen_result.candidates)
        result["screened"] = [
            {"code": c.code, "name": c.name, "score": round(c.score, 1),
             "reason": c.reason, "indicators": c.indicators}
            for c in screen_result.candidates
        ]

        # ── Step 5b: Minute screening ──
        await self._minute_screen(ctx, screen_result, result, broker)

        # ── Step 6: ML predictions & buy execution ──
        await self._evaluate_and_execute(ctx, screen_result, result, regime_result, stock_universe, broker,
                                         is_surge_day=is_surge_day)

        return result

    # ──────────────────────────────────────────
    # Internal methods
    # ──────────────────────────────────────────

    def _apply_intraday_correction(self, regime_result, index_price, index_prices) -> None:
        """TR-16: 장중 KOSPI 등락률 보정 (regime_result를 직접 수정)."""
        intraday_change_pct = index_price.change_pct if index_price.change_pct is not None else 0.0
        multiplier = getattr(self._strategy, 'regime_intraday_multiplier', 4.0)
        cap = getattr(self._strategy, 'regime_intraday_cap', 20.0)
        correction = intraday_change_pct * multiplier
        correction = max(-cap, min(cap, correction))
        if abs(correction) >= 0.5:
            from core.types import MarketRegime
            bull_t = getattr(self._strategy, 'regime_bull_threshold', 65)
            bear_t = getattr(self._strategy, 'regime_bear_threshold', 35)
            old_score = regime_result.score
            new_score = max(0.0, min(100.0, old_score + correction))
            if new_score <= bear_t:
                new_regime = MarketRegime.BEAR
            elif new_score >= bull_t:
                new_regime = MarketRegime.BULL
            else:
                new_regime = MarketRegime.NEUTRAL
            # Note: this modifies regime_result in-place-ish; caller reads .regime after
            regime_result.regime = new_regime
            regime_result.score = new_score
            regime_result.reason += (
                f" | 장중보정: KOSPI {intraday_change_pct:+.1f}% "
                f"({correction:+.0f}점, {old_score:.0f}→{new_score:.0f})"
            )
            regime_result.evidence["intraday_correction"] = correction
            logger.info(f"장중 KOSPI {intraday_change_pct:+.1f}% — 레짐 보정 {correction:+.0f}점")

    async def _select_preset(self, regime_result, vol: float) -> str | None:
        """자동 프리셋 선택 — PresetRouter 인스턴스 재사용 (P-68)."""
        try:
            if not hasattr(self, '_preset_router'):
                from strategy.preset_router import PresetRouter
                self._preset_router = PresetRouter()
            return self._preset_router.select_preset(regime_result, atr_pct=vol)
        except Exception:
            return None

    async def _minute_screen(self, ctx: CycleContext, screen_result, result: dict, broker) -> None:
        """Step 5b: 분봉 기반 실시간 스크리닝 (보조 점수)."""
        if not screen_result.candidates:
            return
        try:
            from strategy.minute_screener import screen_by_minute
            from strategy.combined_scorer import (
                compute_combined_score,
                compute_minute_confidence,
            )
            minute_data: dict[str, list[PriceData]] = {}
            names: dict[str, str] = {}
            ctx.minute_cache.clear()
            for c in screen_result.candidates:
                mp = await self._fetch_minute_prices(broker, c.code, ctx)
                if mp and len(mp) >= 20:
                    minute_data[c.code] = mp
                    ctx.minute_cache[c.code] = mp
                    names[c.code] = c.name
            if minute_data:
                minute_scores = screen_by_minute(minute_data)
                for ms in minute_scores:
                    if ms.code in names and not ms.name:
                        ms.name = names[ms.code]
                result["minute_screened"] = [
                    {"code": s.code, "name": s.name, "score": s.score,
                     "rsi": s.minute_rsi, "momentum": s.momentum_5m,
                     "volume": s.volume_spike, "volatility": s.volatility_pct}
                    for s in minute_scores[:10]
                ]
                logger.info(f"분봉 스크리닝: {len(minute_scores)}개 점수 산출")

                # ── Combined score: 일봉 + 분봉 통합 점수 ──
                regime_str = ctx.regime.value if hasattr(ctx.regime, 'value') else str(ctx.regime)
                minute_score_map = {ms.code: ms.score for ms in minute_scores}
                has_ws = bool(getattr(broker, '_minute_builder', None))
                combined_list = []
                for s in result.get("screened", []):
                    ms = minute_score_map.get(s["code"])
                    if ms is not None:
                        n_bars = len(ctx.minute_cache.get(s["code"], []))
                        confidence = compute_minute_confidence(n_bars, has_websocket=has_ws)
                        combined = compute_combined_score(
                            daily_score=s["score"],
                            minute_score=ms,
                            regime=regime_str,
                            minute_confidence=confidence,
                        )
                        combined_list.append({
                            "code": s["code"],
                            "name": s["name"],
                            "combined_score": combined,
                            "daily_score": s["score"],
                            "minute_score": ms,
                            "confidence": round(confidence, 2),
                        })
                if combined_list:
                    combined_list.sort(key=lambda x: x["combined_score"], reverse=True)
                    result["combined_screened"] = combined_list
                    logger.info(f"통합 점수: {len(combined_list)}개 산출 (레짐={regime_str})")
        except Exception as e:
            logger.warning(f"분봉 스크리닝 실패 (skip): {e}")

    async def _evaluate_and_execute(
        self,
        ctx: CycleContext,
        screen_result,
        result: dict,
        regime_result,
        stock_universe: list[tuple[Any, list[PriceData]]],
        broker,
        is_surge_day: bool = False,
    ) -> None:
        """Step 6: 각 후보에 대해 ML 평가 → 리스크 체크 → 매수 실행."""
        for candidate in screen_result.candidates if screen_result else []:
            if not ctx.market_open:
                break
            if candidate.code in ctx.held_codes and ctx.held_quantities.get(candidate.code, 0) > 0:
                continue

            # 포지션 리스크: 최대 보유 종목 수
            if self._strategy.max_holdings > 0 and len(ctx.held_codes) >= self._strategy.max_holdings:
                logger.info(f"최대 보유 종목({self._strategy.max_holdings}개) 도달 — 신규 진입 생략")
                break

            # 섹터 집중도 체크 (TR-5)
            candidate_sector = ctx.sector_lookup.get(candidate.code, "")
            max_sec = self._strategy.max_sector_concentration
            if candidate_sector and max_sec > 0:
                current_sector_count = ctx.held_sector_counts.get(candidate_sector, 0)
                if current_sector_count >= max_sec:
                    logger.info(f"섹터 집중도 제한: {candidate.name}({candidate.code}) 업종={candidate_sector} — 진입 생략")
                    result["entries_deferred"] += 1
                    continue

            # ML predictions
            prices = self._find_prices(stock_universe, candidate.code)
            if prices is None or len(prices) < 60:
                continue

            # ── P-83: 다중 타임프레임 추세 필터 ──
            # HOLD 모드에서는 MTF 필터 skip (ML 예측 결과는 기록, HOLD가 매수 차단)
            if ctx.mode.value != "hold":
                regime_str = regime_result.regime.value if hasattr(regime_result, 'regime') else "neutral"
                mtf_threshold_override = self._strategy.surge_day_mtf_threshold if is_surge_day else None
                mtf_result = await self._check_multitf_alignment(
                    candidate.code, candidate.name, prices, broker, ctx, regime=regime_str,
                    threshold_override=mtf_threshold_override,
                )
                if not mtf_result["aligned"]:
                    result["mtf_filtered"].append(mtf_result)
                    continue

            if ctx.is_short_term:
                minute_prices = ctx.minute_cache.get(candidate.code) or await self._fetch_minute_prices(broker, candidate.code, ctx)
                if minute_prices and len(minute_prices) >= 30:
                    if self._inference.has_minute_model:
                        pred = self._inference.predict_minute_entry(candidate, minute_prices)
                        if pred is None:
                            logger.error(f"분봉ML 예측 실패: {candidate.code}({candidate.name}) — predict_minute_entry가 None 반환. 분봉모델 상태를 확인하세요.")
                            continue
                    else:
                        logger.error(f"분봉ML 모델 없음: {candidate.code}({candidate.name}) — 단타모드이나 분봉ML이 로드되지 않음. models/trained/minute_model.pkl 확인 필요.")
                        continue
                else:
                    logger.error(f"분봉 데이터 부족: {candidate.code}({candidate.name}) — {len(minute_prices) if minute_prices else 0}개 (30개 필요). WebSocket 데이터 수집 상태 확인.")
                    continue
            else:
                pred = self._inference.predict_entry(candidate, prices)

            if pred:
                pred_entry = {
                    "code": pred.code, "name": pred.name,
                    "action": pred.action.value,
                    "probability": round(pred.rise_probability, 3),
                    "reason": pred.reason,
                    "evidence": pred.evidence,
                }
                # P-86: 급등일 모드 — buy threshold 완화
                if is_surge_day and pred.rise_probability >= self._strategy.surge_day_buy_threshold:
                    if pred.action != DecisionAction.BUY:
                        pred_entry["action"] = DecisionAction.BUY.value
                        pred_entry["reason"] += f" | 급등일 모드({pred.rise_probability:.0%}≥{self._strategy.surge_day_buy_threshold:.0%})"
                        pred = dataclasses.replace(pred,
                            action=DecisionAction.BUY,
                            reason=pred_entry["reason"],
                        )

            # HOLD 모드: 평가만 기록, 매수 차단
            if ctx.mode == StrategyMode.HOLD:
                if pred:
                    result["predictions"].append(pred_entry)
                continue

            if pred and pred.action == DecisionAction.BUY:
                await self._attempt_buy(ctx, candidate, pred, prices, result, regime_result, stock_universe, broker)
                # Check if this buy was deferred — update prediction entry
                if result.get("deferred_entries"):
                    for de in reversed(result["deferred_entries"]):
                        if de["code"] == pred.code:
                            pred_entry["deferred_reason"] = de["reason"]
                            break
                result["predictions"].append(pred_entry)
            elif pred:
                result["predictions"].append(pred_entry)

    async def _attempt_buy(
        self,
        ctx: CycleContext,
        candidate,
        pred,
        prices: list[PriceData],
        result: dict,
        regime_result,
        stock_universe: list[tuple[Any, list[PriceData]]],
        broker,
    ) -> None:
        """진입 정밀화 → 포지션 사이징 → 리스크 체크 → 매수 실행."""
        entry = await self._refine_entry(broker, pred.code, ctx)
        if not entry.should_enter:
            result["entries_deferred"] += 1
            result["deferred_entries"].append(
                {"code": pred.code, "name": pred.name,
                 "reason": entry.reason, "probability": round(pred.rise_probability, 3)}
            )
            logger.info(f"매수 보류: {pred.name}({pred.code}) 확률={pred.rise_probability:.1%} — {entry.reason}")
            return

        ref_price = entry.limit_price or prices[-1].close
        kelly_stats = self._trade_recorder.get_kelly_stats()
        base_budget_pct = self._risk.position_size_pct(kelly_stats=kelly_stats)

        # ATR-based dynamic position sizing
        target_vol_pct = self._strategy.target_vol_pct
        min_pct = base_budget_pct * 0.3
        max_pct = base_budget_pct * 2.0
        vol_pct = max(ctx.atr_vol_pct, 0.5)
        adjusted_pct = base_budget_pct * (target_vol_pct / vol_pct)
        adjusted_pct = max(min_pct, min(adjusted_pct, max_pct))

        if not ctx.account:
            return
        budget = ctx.account.deposit * adjusted_pct
        quantity = int(budget / ref_price) if ref_price > 0 else 0
        if quantity <= 0:
            logger.info(f"매수 불가: {pred.name}({pred.code}) — 예산 부족 "
                        f"(deposit={ctx.account.deposit:.0f}원, "
                        f"adj_pct={adjusted_pct:.1%}, budget={budget:.0f}원, "
                        f"ref_price={ref_price:.0f}원, qty={quantity})")
            return

        # TR-13: 일일 거래 횟수 제한
        if self._strategy.daily_trade_limit > 0 and ctx.daily_trade_count >= self._strategy.daily_trade_limit:
            logger.info(f"일일 거래 횟수 제한 ({self._strategy.daily_trade_limit}회) 도달 — 진입 생략")
            result["entries_deferred"] += 1
            return

        # TR-14: 총 노출 한도 체크
        max_exposure = ctx.account.total_asset * (self._strategy.max_total_exposure_pct / 100.0)
        current_exposure = sum(
            p.eval_amount or (p.current_price * p.quantity)
            for p in ctx.current_positions
        )
        new_exposure = ref_price * quantity
        if current_exposure + new_exposure > max_exposure:
            logger.info(f"총 노출 한도 초과 — 진입 생략")
            result["entries_deferred"] += 1
            return

        # TR-7: 포트폴리오 상관관계 검사
        corr_safe, corr_reason = await self._check_correlation_risk(
            candidate_code=candidate.code,
            candidate_prices=prices,
            current_positions=ctx.current_positions,
            stock_universe=stock_universe,
            sector_lookup=ctx.sector_lookup,
        )
        if not corr_safe:
            result["entries_deferred"] += 1
            result["deferred_entries"].append(
                {"code": candidate.code, "name": candidate.name,
                 "reason": corr_reason, "probability": 0}
            )
            return

        # Execute buy
        buy_reason = f"{pred.reason} || 진입정밀화: {entry.reason}"
        import time
        exec_result = await self._executor.execute_buy(
            code=pred.code,
            name=pred.name,
            quantity=quantity,
            price=entry.limit_price,
            action=pred.action,
            reason=buy_reason,
        )

        if exec_result.success:
            result["buys_executed"] += 1
            ctx.daily_trade_count += 1
            self._trade_recorder.record_pending_buy({
                "code": pred.code,
                "name": pred.name,
                "quantity": quantity,
                "price": exec_result.price or ref_price,
                "fill_price": exec_result.fill_price or 0,
                "amount": exec_result.amount or (quantity * (exec_result.price or ref_price)),
                "action": pred.action,
                "reason": buy_reason,
                "timestamp": time.time(),
            })
            ctx.held_codes.add(pred.code)
            ctx.held_quantities[pred.code] = ctx.held_quantities.get(pred.code, 0) + quantity
            if candidate_sector := ctx.sector_lookup.get(candidate.code, ""):
                ctx.held_sector_counts[candidate_sector] = ctx.held_sector_counts.get(candidate_sector, 0) + 1
        else:
            result["errors"].append(f"매수 실패 {pred.code}: {exec_result.error}")

    async def _check_correlation_risk(
        self,
        candidate_code: str,
        candidate_prices: list[PriceData],
        current_positions: list[Position],
        stock_universe: list[tuple[Any, list[PriceData]]],
        sector_lookup: dict[str, str] | None = None,
    ) -> tuple[bool, str]:
        """TR-7: 포트폴리오 상관관계 검사 (PC-2: 가중 + 업종 보정)."""
        if len(current_positions) < 1:
            return True, ""

        closes_map: dict[str, list[float]] = {candidate_code: [p.close for p in candidate_prices]}
        for info, prices in stock_universe:
            if info.code in {p.code for p in current_positions}:
                closes_map[info.code] = [p.close for p in prices]

        cand_returns = np.diff(np.log(closes_map[candidate_code][-60:]))
        n = len(cand_returns)

        # 가중치: 최근 20봉 1.5배, 이전 40봉 1.0배
        weights = np.ones(n)
        weights[-20:] = 1.5
        weights[:n-20] = 1.0

        cand_sector = (sector_lookup or {}).get(candidate_code, "")

        weighted_corr_sum = 0.0
        weight_sum = 0.0
        threshold = 0.75

        for pos in current_positions:
            p_prices = closes_map.get(pos.code)
            if p_prices is None or len(p_prices) < 60:
                continue
            pos_returns = np.diff(np.log(p_prices[-60:]))
            if len(pos_returns) != n:
                continue

            # Pearson correlation using weighted returns
            w = weights[:len(pos_returns)]
            w_mean_cand = np.average(cand_returns, weights=w)
            w_mean_pos = np.average(pos_returns, weights=w)
            w_cov = np.sum(w * (cand_returns - w_mean_cand) * (pos_returns - w_mean_pos)) / np.sum(w)
            w_std_cand = np.sqrt(np.sum(w * (cand_returns - w_mean_cand)**2) / np.sum(w))
            w_std_pos = np.sqrt(np.sum(w * (pos_returns - w_mean_pos)**2) / np.sum(w))
            corr = w_cov / (w_std_cand * w_std_pos) if w_std_cand > 0 and w_std_pos > 0 else 0

            if np.isnan(corr):
                continue

            # 동일 업종 보정: 같은 업종이면 +0.1
            pos_sector = (sector_lookup or {}).get(pos.code, "")
            sector_bonus = 0.1 if (cand_sector and pos_sector and cand_sector == pos_sector) else 0.0

            effective_corr = abs(corr) + sector_bonus
            weight = np.sum(w)
            weighted_corr_sum += effective_corr * weight
            weight_sum += weight

        if weight_sum == 0:
            return True, ""

        avg_weighted_corr = weighted_corr_sum / weight_sum
        if avg_weighted_corr > threshold:
            return False, (
                f"포트폴리오 상관관계 주의: 가중치 상관 {avg_weighted_corr:.2f} > {threshold:.0%}"
            )
        return True, ""

    async def _refine_entry(self, broker, code: str, ctx: CycleContext) -> EntryDecision:
        """분봉으로 진입 타이밍/가격 정밀화."""
        try:
            minute_prices = ctx.minute_cache.get(code)
            if minute_prices is None:
                minute_prices = await self._fetch_minute_prices(broker, code, ctx)
            if minute_prices is None:
                return EntryDecision(
                    timing=EntryTiming.ENTER_NOW, should_enter=True, limit_price=None,
                    reason="분봉 미지원 — 정밀화 생략(시장가 진입)", evidence={},
                )
        except NotImplementedError:
            return EntryDecision(
                timing=EntryTiming.ENTER_NOW, should_enter=True, limit_price=None,
                reason="분봉 미지원 — 정밀화 생략(시장가 진입)", evidence={},
            )
        except Exception as e:
            logger.warning(f"분봉 조회 실패({code}) — 정밀화 생략: {type(e).__name__}")
            return EntryDecision(
                timing=EntryTiming.ENTER_NOW, should_enter=True, limit_price=None,
                reason="분봉 조회 실패 — 정밀화 생략(시장가 진입)", evidence={},
            )
        return refine_entry(minute_prices)

    async def _check_multitf_alignment(
        self, code: str, name: str, daily_prices: list[PriceData],
        broker, ctx: CycleContext, regime: str = "neutral",
        threshold_override: float | None = None,
    ) -> dict:
        """P-83: 다중 타임프레임 추세 방향성 일치도 검사.

        주봉(장기) + 일봉(중기) + 분봉(단기) 방향이 모두 일치할 때만
        aligned=True. 분봉 데이터가 없으면 주봉+일봉 2개로 판단.

        레짐별 임계값은 self._strategy에서 동적으로 조회 (DB config 가능).

        Returns:
            dict: {aligned: bool, score: float, reason: str, code, name}
        """
        daily_closes = np.array([p.close for p in daily_prices], dtype=float)

        # 1) 주봉 추세 (장기): MA5 > MA20 정렬
        weekly_aligned = False
        weekly_reason = ""
        try:
            from datetime import datetime, timedelta, timezone
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=365)
            weekly = await broker.get_weekly_history(code, start, end)
            if weekly and len(weekly) >= 25:
                w_closes = np.array([p.close for p in weekly], dtype=float)
                w_ma5 = float(sma(w_closes, 5)[-1])
                w_ma20 = float(sma(w_closes, 20)[-1])
                w_last = float(w_closes[-1])
                weekly_aligned = w_last > w_ma5 > w_ma20
                weekly_reason = (
                    f"주봉 MA5={w_ma5:.0f}/MA20={w_ma20:.0f} {'상승' if weekly_aligned else '하락'}"
                )
            else:
                weekly_reason = f"주봉 데이터 부족({len(weekly) if weekly else 0}개)"
        except Exception as e:
            weekly_reason = f"주봉 조회 실패"
            logger.debug(f"주봉 조회 실패({code}): {type(e).__name__}")

        # 2) 일봉 추세 (중기): 최근 5일 > 20일 MA 정렬
        if len(daily_closes) >= 20:
            d_ma5 = float(sma(daily_closes, 5)[-1])
            d_ma20 = float(sma(daily_closes, 20)[-1])
            d_last = float(daily_closes[-1])
            daily_aligned = d_last > d_ma5 > d_ma20
            daily_reason = f"일봉 MA5={d_ma5:.0f}/MA20={d_ma20:.0f} {'상승' if daily_aligned else '하락'}"
        else:
            daily_aligned = False
            daily_reason = "일봉 데이터 부족"

        # 3) 분봉 추세 (단기): 최근 5분봉 상승 여부
        minute_aligned = False
        minute_reason = ""
        minute_prices = ctx.minute_cache.get(code)
        if minute_prices and len(minute_prices) >= 5:
            m_prev = minute_prices[-5].close
            m_last = minute_prices[-1].close
            minute_aligned = m_last > m_prev
            change_pct = (m_last - m_prev) / m_prev * 100 if m_prev > 0 else 0
            minute_reason = f"분봉 5개 {change_pct:+.1f}% {'상승' if minute_aligned else '하락'}"
        else:
            minute_reason = "분봉 데이터 없음(생략)"

        # 4) 통합 점수: 레짐별 임계값 (self._strategy → DB config 동적)
        checks = [weekly_aligned, daily_aligned]
        if minute_prices and len(minute_prices) >= 5:
            checks.append(minute_aligned)
        aligned_count = sum(checks)
        total_checks = len(checks)
        score = aligned_count / total_checks if total_checks > 0 else 0.0
        if threshold_override is not None:
            mtf_threshold = threshold_override
        else:
            threshold_map = {
                "bear": getattr(self._strategy, 'mtf_bear_threshold', 0.50),
                "neutral": getattr(self._strategy, 'mtf_neutral_threshold', 0.67),
                "bull": getattr(self._strategy, 'mtf_bull_threshold', 0.67),
            }
            mtf_threshold = threshold_map.get(regime, 0.50)
        aligned = score >= mtf_threshold

        reasons = [r for r in [weekly_reason, daily_reason, minute_reason] if r]
        reason = " | ".join(reasons) if reasons else "TF 데이터 없음"

        return {
            "code": code,
            "name": name,
            "aligned": aligned,
            "score": round(score, 2),
            "aligned_count": aligned_count,
            "total_checks": total_checks,
            "reason": reason,
        }

    async def _fetch_minute_prices(self, broker, code: str, ctx: CycleContext) -> list[PriceData] | None:
        """Fetch minute data — cache-first, REST fallback."""
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
