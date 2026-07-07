"""NGSAT stock screener — 2nd stage of the 3-stage pipeline.

Takes the market regime (from stage 1) and a list of stocks with price data,
then screens for candidates using technical indicators and chart patterns.

Output: ScreenResult with scored candidates, each with a reason and evidence.

This module does NOT make buy/sell decisions — it only identifies
promising candidates for the ML stage (stage 3) to evaluate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from core.logger import logger

import numpy as np

from core.types import Market, MarketRegime, PriceData, StockInfo
from strategy.indicators import (
    current_macd,
    current_rsi,
    sma,
    stochastic,
    adx,
    adx_with_di,
    volume_ratio,
    mfi,
    obv,
    obv_slope,
    relative_strength,
    detect_hammer,
    detect_engulfing,
)
from strategy.patterns import (
    PatternResult,
    detect_breakout,
    detect_pullback,
    detect_rebound,
    detect_bollinger_squeeze,
    detect_ma_cross,
)
from strategy.scorer import (
    compute_total_score,
    score_rsi,
    score_mfi,
    score_stochastic,
    score_macd,
    score_ma_alignment,
    score_adx_di,
    score_volume,
    score_obv_slope,
    score_relative_strength,
    score_candlestick,
)
from strategy.regime import RegimeResult


@dataclass(frozen=True)
class ScreenCandidate:
    """A stock that passed the screening stage.

    Attributes:
        code: Stock code (6-digit).
        name: Stock name.
        market: KOSPI / KOSDAQ.
        score: Screening score (0-100, higher = better).
        patterns: List of detected patterns with reasons.
        indicators: Technical indicator values at screening time.
        reason: Human-readable screening summary (Korean).
        kospi_bonus: Whether this stock received KOSPI weighting bonus.
    """
    code: str
    name: str
    market: Market
    score: float
    patterns: list[PatternResult] = field(default_factory=list)
    indicators: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    kospi_bonus: bool = False
    product_type: str = "stock"  # stock / etf / etn


@dataclass(frozen=True)
class ScreenResult:
    """Result of screening all stocks.

    Attributes:
        regime: Market regime at screening time.
        candidates: Screened candidates sorted by score (descending).
        total_scanned: Total number of stocks evaluated.
        total_passed: Number of stocks that passed screening.
        screened_at: When screening occurred.
        reason: Summary reason for the screening result.
    """
    regime: MarketRegime
    candidates: list[ScreenCandidate]
    total_scanned: int
    total_passed: int
    screened_at: datetime = field(default_factory=datetime.now)
    reason: str = ""


# ── Strategy config injection ──
from core.config import StrategyConfig as _StrategyConfig

# ── Screening thresholds by regime (configurable via StrategyConfig) ──
# Defaults match the original hardcoded values.
def _build_regime_thresholds(cfg: _StrategyConfig) -> dict:
    return {
        MarketRegime.BULL: {
            "min_score": cfg.screener_bull_min_score,
            "max_candidates": cfg.screener_bull_max_candidates,
            "pattern_weight": 1.2,
        },
        MarketRegime.NEUTRAL: {
            "min_score": cfg.screener_neutral_min_score,
            "max_candidates": cfg.screener_neutral_max_candidates,
            "pattern_weight": 1.0,
        },
        MarketRegime.BEAR: {
            "min_score": cfg.screener_bear_min_score,
            "max_candidates": cfg.screener_bear_max_candidates,
            "pattern_weight": 0.8,
        },
    }


def screen_stocks(
    stocks: list[tuple[StockInfo, list[PriceData]]],
    regime_result: RegimeResult,
    config: _StrategyConfig | None = None,
    index_prices: list[PriceData] | None = None,
) -> ScreenResult:
    """Screen stocks for trading candidates.

    2nd stage of the NGSAT pipeline:
    1. For each stock, calculate technical indicators
    2. Detect chart patterns
    3. Score each stock based on indicators + patterns
    4. Apply regime-based filtering (BULL=relaxed, BEAR=strict)
    5. Apply KOSPI weighting bonus
    6. Return top candidates sorted by score

    Args:
        stocks: List of (StockInfo, price history) tuples.
        regime_result: Market regime evaluation from stage 1.
        config: Strategy configuration (optional).
        index_prices: Index price data for RS calculation (optional).

    Returns:
        ScreenResult with ranked candidates.
    """
    thresholds = _build_regime_thresholds(config or _StrategyConfig()).get(
        regime_result.regime,
    )
    if thresholds is None:
        thresholds = {"min_score": 70.0, "max_candidates": 15, "pattern_weight": 1.0}

    # P-60: 지수 데이터를 thresholds에 포함 (RS 계산용)
    if index_prices:
        thresholds["index_closes"] = [p.close for p in index_prices]
    thresholds["regime"] = regime_result.regime.value

    candidates: list[ScreenCandidate] = []

    for stock_info, price_history in stocks:
        if len(price_history) < 60:
            continue  # 데이터 부족 (ML 예측도 60일 필요)

        candidate = _evaluate_single_stock(stock_info, price_history, thresholds, config)

        if candidate and candidate.score >= thresholds["min_score"]:
            candidates.append(candidate)
        elif candidate:
            logger.info(f"스크리닝 저점수: {stock_info.code} {candidate.score:.1f}점 (기준 {thresholds['min_score']})")

    # Sort by score descending
    candidates.sort(key=lambda c: c.score, reverse=True)

    # Limit to max candidates
    max_cands = thresholds["max_candidates"]
    candidates = candidates[:max_cands]

    reason = (
        f"스크리닝 완료: {regime_result.regime.value}장, "
        f"{len(stocks)}개 스캔 → {len(candidates)}개 통과 "
        f"(기준: {thresholds['min_score']:.0f}점, 최대: {max_cands}개)"
    )

    return ScreenResult(
        regime=regime_result.regime,
        candidates=candidates,
        total_scanned=len(stocks),
        total_passed=len(candidates),
        reason=reason,
    )


def _evaluate_single_stock(
    stock: StockInfo,
    prices: list[PriceData],
    thresholds: dict,
    config: _StrategyConfig | None = None,
) -> ScreenCandidate | None:
    """Evaluate a single stock for screening.

    Calculates indicators, detects patterns, computes composite score via scorer.py.
    """
    closes = np.array([p.close for p in prices], dtype=float)
    highs = np.array([p.high for p in prices], dtype=float)
    lows = np.array([p.low for p in prices], dtype=float)
    volumes = np.array([p.volume for p in prices], dtype=float)
    opens = np.array([p.open for p in prices], dtype=float)

    regime = thresholds.get("regime", "neutral")
    index_closes = thresholds.get("index_closes")

    # ── Pre-filtering (P-60): 변동성/거래량 부족 종목 제외 ──
    atr_pct = float(np.std(closes[-20:]) / (np.mean(closes[-20:]) or 1) * 100) if len(closes) >= 20 else 0
    if atr_pct < 0.2:
        logger.info(f"스크리닝 필터: {stock.code} ATR {atr_pct:.2f}% < 0.2%")
        return None
    avg_vol = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else 0
    if avg_vol < 500:
        logger.info(f"스크리닝 필터: {stock.code} 거래량 {avg_vol:.0f} < 500")
        return None

    # ── Technical indicators ──
    rsi_val = current_rsi(closes, 14)
    macd_line, signal_line, hist = current_macd(closes)

    ma5 = float(sma(closes, 5)[-1]) if not np.isnan(sma(closes, 5)[-1]) else 0.0
    ma20 = float(sma(closes, 20)[-1]) if not np.isnan(sma(closes, 20)[-1]) else 0.0

    vol_ratio = float(volume_ratio(volumes, 20)[-1]) if not np.isnan(volume_ratio(volumes, 20)[-1]) else 1.0

    # ── Additional indicators ──
    k_val, d_val = float('nan'), float('nan')
    if len(closes) >= 14:
        k_arr, d_arr = stochastic(highs, lows, closes, 14, 3)
        k_val = float(k_arr[-1]) if not np.isnan(k_arr[-1]) else float('nan')
        d_val = float(d_arr[-1]) if not np.isnan(d_arr[-1]) else float('nan')

    adx_val = float('nan')
    di_plus_val = float('nan')
    di_minus_val = float('nan')
    if len(closes) >= 30:
        adx_arr, di_plus_arr, di_minus_arr = adx_with_di(highs, lows, closes, 14)
        adx_val = float(adx_arr[-1]) if not np.isnan(adx_arr[-1]) else float('nan')
        di_plus_val = float(di_plus_arr[-1]) if not np.isnan(di_plus_arr[-1]) else float('nan')
        di_minus_val = float(di_minus_arr[-1]) if not np.isnan(di_minus_arr[-1]) else float('nan')

    vol_ma5_val, vol_ma20_val = float('nan'), float('nan')
    if len(volumes) >= 20:
        vol_ma5_arr = sma(volumes, 5)
        vol_ma20_arr = sma(volumes, 20)
        vol_ma5_val = float(vol_ma5_arr[-1]) if not np.isnan(vol_ma5_arr[-1]) else float('nan')
        vol_ma20_val = float(vol_ma20_arr[-1]) if not np.isnan(vol_ma20_arr[-1]) else float('nan')

    # ── Advanced indicators (P-60) ──
    mfi_val = float('nan')
    if len(closes) >= 30:
        try:
            mfi_arr = mfi(highs, lows, closes, volumes, 14)
            mfi_val = float(mfi_arr[-1]) if not np.isnan(mfi_arr[-1]) else float('nan')
        except Exception:
            pass

    obv_slope_val = 0.0
    if len(closes) >= 20:
        try:
            obv_arr = obv(closes, volumes)
            obv_slope_val = obv_slope(obv_arr, 20)
        except Exception:
            pass

    rs_val = 1.0
    if index_closes and len(index_closes) >= 21 and len(closes) >= 21:
        try:
            rs_val = relative_strength(
                closes, np.array(index_closes[-len(closes):], dtype=float), 20)
        except Exception:
            pass

    candle_bullish = False
    if len(prices) >= 2:
        try:
            if detect_engulfing(opens[-2], closes[-2], opens[-1], closes[-1]):
                candle_bullish = True
            if detect_hammer(opens[-1], highs[-1], lows[-1], closes[-1]):
                candle_bullish = True
        except Exception:
            pass

    # ── indicators dict (backward compat + extended) ──
    indicators = {
        "rsi": rsi_val, "macd_line": macd_line, "macd_signal": signal_line,
        "macd_histogram": hist, "ma5": ma5, "ma20": ma20,
        "volume_ratio": vol_ratio, "current_price": float(closes[-1]),
        "stochastic_k": k_val, "stochastic_d": d_val, "adx": adx_val,
        "vol_ma5": vol_ma5_val, "vol_ma20": vol_ma20_val,
        "mfi": mfi_val, "obv_slope": obv_slope_val,
        "di_plus": di_plus_val, "di_minus": di_minus_val,
        "rs": rs_val, "atr_pct": atr_pct,
    }

    # ── Pattern detection ──
    patterns: list[PatternResult] = []
    for detector in [
        lambda: detect_breakout(closes, highs, volumes),
        lambda: detect_pullback(closes, highs),
        lambda: detect_rebound(closes, lows, volumes),
        lambda: detect_bollinger_squeeze(closes),
        lambda: detect_ma_cross(closes),
    ]:
        try:
            result = detector()
            if result.detected:
                patterns.append(result)
        except Exception as e:
            logger.warning(f"패턴 탐지 실패: {type(e).__name__}")
            continue

    # ── Scoring via scorer.py (P-60) ──
    indicator_scores = {"pattern": 0.0}

    if not np.isnan(rsi_val):
        indicator_scores["rsi"] = score_rsi(rsi_val)
    if not np.isnan(mfi_val):
        indicator_scores["mfi"] = score_mfi(mfi_val)
    if not np.isnan(adx_val) and not np.isnan(di_plus_val) and not np.isnan(di_minus_val):
        indicator_scores["adx_di"] = score_adx_di(adx_val, di_plus_val, di_minus_val)
    indicator_scores["obv"] = score_obv_slope(obv_slope_val)
    if ma5 > 0 and ma20 > 0:
        indicator_scores["ma"] = score_ma_alignment(float(closes[-1]), ma5, ma20)
    if not np.isnan(vol_ma5_val) and not np.isnan(vol_ma20_val) and not np.isnan(vol_ratio):
        indicator_scores["volume"] = score_volume(vol_ma5_val, vol_ma20_val, vol_ratio)
    indicator_scores["candle"] = score_candlestick(candle_bullish, False)
    if index_closes:
        indicator_scores["rs"] = score_relative_strength(rs_val)

    # Pattern score
    pattern_type_weights = {
        "breakout": 1.5, "bollinger_squeeze": 1.3, "ma_cross": 1.2,
        "pullback": 0.8, "rebound": 0.7,
    }
    pattern_score = sum(
        3 * pattern_type_weights.get(p.pattern_name, 1.0)
        for p in patterns
    ) if patterns else 0
    indicator_scores["pattern"] = min(100.0, pattern_score * 10)

    total_score = compute_total_score(indicator_scores, regime)

    # ── KOSPI bonus ──
    kospi_bonus = False
    cfg = config or _StrategyConfig()
    if stock.market == Market.KOSPI:
        total_score += cfg.kospi_bonus_score
        kospi_bonus = True
    elif stock.market == Market.KOSDAQ:
        total_score += cfg.kosdaq_bonus_score

    score = max(0, min(100, total_score))

    # ── Build reason ──
    pattern_names = [p.pattern_name_kr for p in patterns]
    reason_parts = [
        f"점수 {score:.1f}/100",
        f"RSI {rsi_val:.1f}" if not np.isnan(rsi_val) else "",
        f"MFI {mfi_val:.1f}" if not np.isnan(mfi_val) else "",
        f"MACD {'+' if hist > 0 else ''}{hist:.0f}",
        f"ADX {adx_val:.0f} DI+{di_plus_val:.0f}/DI-{di_minus_val:.0f}" if not np.isnan(adx_val) else "",
        f"OBV {obv_slope_val:+.2f}",
        f"RS {rs_val:.2f}" if index_closes else "",
        f"스토캐스틱 K={k_val:.0f}" if not np.isnan(k_val) else "",
        f"패턴: {', '.join(pattern_names)}" if pattern_names else "",
        f"코스피 가산점" if kospi_bonus else "",
    ]
    reason = " | ".join(r for r in reason_parts if r)

    return ScreenCandidate(
        code=stock.code,
        name=stock.name,
        market=stock.market,
        score=score,
        patterns=patterns,
        indicators=indicators,
        reason=reason,
        product_type=getattr(stock, "product_type", "stock"),
        kospi_bonus=kospi_bonus,
    )
