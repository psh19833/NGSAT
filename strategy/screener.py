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

import numpy as np

from core.types import Market, MarketRegime, PriceData, StockInfo
from strategy.indicators import (
    current_macd,
    current_rsi,
    sma,
    volume_ratio,
)
from strategy.patterns import (
    PatternResult,
    detect_breakout,
    detect_pullback,
    detect_rebound,
    detect_bollinger_squeeze,
    detect_ma_cross,
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

_KOSPI_BONUS = 5.0


def screen_stocks(
    stocks: list[tuple[StockInfo, list[PriceData]]],
    regime_result: RegimeResult,
    config: _StrategyConfig | None = None,
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

    Returns:
        ScreenResult with ranked candidates.
    """
    thresholds = _build_regime_thresholds(config or _StrategyConfig()).get(
        regime_result.regime,
    )
    if thresholds is None:
        thresholds = {"min_score": 70.0, "max_candidates": 15, "pattern_weight": 1.0}

    candidates: list[ScreenCandidate] = []

    for stock_info, price_history in stocks:
        if len(price_history) < 60:
            continue  # 데이터 부족 (ML 예측도 60일 필요)

        candidate = _evaluate_single_stock(stock_info, price_history, thresholds)

        if candidate and candidate.score >= thresholds["min_score"]:
            candidates.append(candidate)

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
) -> ScreenCandidate | None:
    """Evaluate a single stock for screening.

    Calculates indicators, detects patterns, and computes a composite score.
    """
    closes = np.array([p.close for p in prices], dtype=float)
    highs = np.array([p.high for p in prices], dtype=float)
    lows = np.array([p.low for p in prices], dtype=float)
    volumes = np.array([p.volume for p in prices], dtype=float)

    # ── Technical indicators ──
    rsi_val = current_rsi(closes, 14)
    macd_line, signal_line, hist = current_macd(closes)

    ma5 = float(sma(closes, 5)[-1]) if not np.isnan(sma(closes, 5)[-1]) else 0.0
    ma20 = float(sma(closes, 20)[-1]) if not np.isnan(sma(closes, 20)[-1]) else 0.0

    vol_ratio = float(volume_ratio(volumes, 20)[-1]) if not np.isnan(volume_ratio(volumes, 20)[-1]) else 1.0

    indicators = {
        "rsi": rsi_val,
        "macd_line": macd_line,
        "macd_signal": signal_line,
        "macd_histogram": hist,
        "ma5": ma5,
        "ma20": ma20,
        "volume_ratio": vol_ratio,
        "current_price": float(closes[-1]),
    }

    # ── Pattern detection ──
    patterns: list[PatternResult] = []
    pattern_weight = thresholds.get("pattern_weight", 1.0)

    # Detect all applicable patterns
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
        except Exception:
            continue  # Skip failed pattern detection, don't crash screening

    # ── Scoring ──
    score = 50.0  # Base score

    # RSI scoring
    if not np.isnan(rsi_val):
        if 30 < rsi_val < 50:
            score += 10  # Oversold recovery zone
        elif 50 <= rsi_val < 70:
            score += 15  # Healthy bullish zone
        elif rsi_val >= 70:
            score -= 5   # Overbought — risky
        elif rsi_val <= 30:
            score += 5   # Oversold — potential rebound

    # MACD scoring
    if hist > 0:
        score += 10  # Bullish MACD
    elif hist < 0:
        score -= 10  # Bearish MACD

    # MA alignment scoring
    if ma5 > 0 and ma20 > 0:
        if closes[-1] > ma5 > ma20:
            score += 15  # Perfect bullish alignment
        elif closes[-1] > ma5:
            score += 5   # Above short-term MA
        elif closes[-1] < ma5 < ma20:
            score -= 15  # Bearish alignment

    # Volume confirmation
    if vol_ratio > 1.5:
        score += 5  # Above-average volume

    # Pattern scoring (weighted by regime)
    detected_count = len(patterns)
    score += detected_count * 4 * pattern_weight

    # KOSPI bonus (기획서: 코스피 비중 더 높게)
    kospi_bonus = False
    if stock.market == Market.KOSPI:
        score += _KOSPI_BONUS
        kospi_bonus = True

    score = max(0, min(100, score))

    # Build reason
    pattern_names = [p.pattern_name_kr for p in patterns]
    reason_parts = [
        f"점수 {score:.1f}/100",
        f"RSI {rsi_val:.1f}" if not np.isnan(rsi_val) else "RSI N/A",
        f"MACD {'+' if hist > 0 else ''}{hist:.0f}",
        f"패턴: {', '.join(pattern_names)}" if pattern_names else "패턴 없음",
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
        kospi_bonus=kospi_bonus,
    )
