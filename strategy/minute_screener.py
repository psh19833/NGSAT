"""NGSAT minute-based real-time screener.

Uses minute-candle data to compute dynamic stock scores that change
intraday, unlike the daily screener which only updates once per day.

Score components (100 total):
  - Minute RSI (30pts): 30-70 neutral = high score
  - 5-min momentum (25pts): +1~3% = high score
  - Volume spike (20pts): 5min/20min ratio
  - Volatility (15pts): Appropriate ATR range
  - Daily bonus (10pts): MA alignment reference
"""

from __future__ import annotations

import numpy as np

from core.logger import logger
from core.types import MarketRegime, MinuteScore, PriceData, StockInfo
from strategy.indicators import current_rsi, sma


def screen_by_minute(
    minute_data: dict[str, list[PriceData]],
) -> list[MinuteScore]:
    """분봉 데이터로 각 종목의 실시간 점수를 계산.

    Args:
        minute_data: {code: [PriceData]} — 각 종목의 분봉 데이터 (최소 20개).

    Returns:
        점수 순 정렬된 MinuteScore 리스트.
    """
    results: list[MinuteScore] = []

    for code, prices in minute_data.items():
        if not prices or len(prices) < 20:
            continue

        try:
            score = _score_minute_stock(code, prices)
            results.append(score)
        except Exception as e:
            logger.debug(f"[{code}] 분봉 스크리닝 오류: {e}")
            continue

    # 점수 내림차순 정렬
    results.sort(key=lambda x: x.score, reverse=True)
    return results


def _score_minute_stock(code: str, prices: list[PriceData]) -> MinuteScore:
    """Compute minute-based score for a single stock."""
    closes = np.array([p.close for p in prices], dtype=float)
    highs = np.array([p.high for p in prices], dtype=float)
    lows = np.array([p.low for p in prices], dtype=float)
    volumes = np.array([p.volume for p in prices], dtype=float)

    n = len(closes)
    reasons: list[str] = []
    total = 50.0  # start neutral

    # ── 1. Minute RSI (30pts) ──
    rsi_val = current_rsi(closes, 14)
    if not np.isnan(rsi_val):
        rsi_rounded = round(float(rsi_val), 1)
        if 30 <= rsi_rounded <= 70:
            total += 15
            reasons.append(f"RSI {rsi_rounded} (중립)")
        elif rsi_rounded > 75:
            total -= 15
            reasons.append(f"RSI {rsi_rounded} (과열-감점)")
        elif rsi_rounded < 25:
            total -= 10
            reasons.append(f"RSI {rsi_rounded} (과매도-감점)")
        else:
            total += 5
            reasons.append(f"RSI {rsi_rounded}")
    else:
        rsi_rounded = 50.0

    # ── 2. 5-min momentum (25pts) ──
    if n >= 5:
        prev = float(closes[-5]) if closes[-5] > 0 else float(closes[0])
        mom = ((float(closes[-1]) - prev) / prev) * 100.0 if prev > 0 else 0.0
        mom = round(mom, 2)
        if 1.0 <= mom <= 3.0:
            total += 20
            reasons.append(f"모멘텀 +{mom:.1f}% (양호)")
        elif 0.0 <= mom < 1.0:
            total += 10
            reasons.append(f"모멘텀 +{mom:.1f}% (미약)")
        elif -2.0 <= mom < 0.0:
            total -= 5
            reasons.append(f"모멘텀 {mom:.1f}% (약세)")
        else:
            total -= 15
            reasons.append(f"모멘텀 {mom:.1f}% (급락)")
    else:
        mom = 0.0

    # ── 3. Volume spike (20pts) ──
    if n >= 20:
        recent_vol = float(np.mean(volumes[-5:])) if len(volumes) >= 5 else 1.0
        prev_vol = float(np.mean(volumes[-20:-5])) if len(volumes) >= 20 else recent_vol
        vol_ratio = (recent_vol / prev_vol) if prev_vol > 0 else 1.0
        vol_ratio = round(vol_ratio, 2)
        if vol_ratio >= 2.0:
            total += 15
            reasons.append(f"거래량 {vol_ratio:.1f}배 (급등)")
        elif vol_ratio >= 1.5:
            total += 10
            reasons.append(f"거래량 {vol_ratio:.1f}배 (증가)")
        elif vol_ratio <= 0.5:
            total -= 5
            reasons.append(f"거래량 {vol_ratio:.1f}배 (감소)")
        else:
            total += 5
            reasons.append(f"거래량 {vol_ratio:.1f}배 (보통)")
    else:
        vol_ratio = 1.0

    # ── 4. Volatility (15pts) ──
    if n >= 20:
        typical_prices = (highs + lows + closes) / 3.0
        atr_values = np.abs(np.diff(typical_prices))
        atr_mean = float(np.mean(atr_values[-14:])) if len(atr_values) >= 14 else 0.0
        avg_price = float(np.mean(closes[-14:])) if len(closes) >= 14 else float(closes[-1])
        vol_pct = (atr_mean / avg_price * 100.0) if avg_price > 0 else 0.0
        vol_pct = round(vol_pct, 2)
        if 0.5 <= vol_pct <= 2.0:
            total += 10
            reasons.append(f"변동성 {vol_pct:.1f}% (적정)")
        elif vol_pct > 3.0:
            total -= 5
            reasons.append(f"변동성 {vol_pct:.1f}% (과다)")
        else:
            total += 5
            reasons.append(f"변동성 {vol_pct:.1f}%")
    else:
        vol_pct = 0.0

    # Clamp score to 0-100
    total = max(0.0, min(100.0, total))

    return MinuteScore(
        code=code,
        name="",  # name will be filled by caller
        score=round(total, 1),
        minute_rsi=rsi_rounded,
        momentum_5m=mom,
        volume_spike=vol_ratio,
        volatility_pct=vol_pct,
        reasons=reasons,
    )
