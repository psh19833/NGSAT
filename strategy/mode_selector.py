"""NGSAT 모드 선택기 — 시장 레짐 + 변동성 → 매매 모드 자동 전환.

하이브리드 매매 2단계 핵심:
일봉 레짐 평가 + 장중 변동성 → 스윙/단타/관망 모드 선택

모드 매핑 (초안 — 2단계 정밀화 후 확정):
  강세장(BULL)     → 스윙 (추세 따라가기)
  중립장+고변동성  → 단타 (방향성 약할 때 짧게)
  중립장+저변동성  → 스윙 or 단타 (점수 기반)
  약세장(BEAR)     → 관망/보수적 스윙

변동성 판단 기준:
  ATR(%)로 측정. 종목 ATR이 일정 threshold 이상이면 '고변동성'.
  기준: ATR% > median(ATR%) × 1.5 → 고변동성
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from core.types import MarketRegime, StrategyMode
from strategy.regime import RegimeResult


@dataclass(frozen=True)
class ModeDecision:
    """모드 선택 결과.

    Attributes:
        mode: 선택된 매매 모드.
        confidence: 선택 신뢰도 (0~1).
        reason: 선택 근거 (한글).
        evidence: 정량 근거.
    """
    mode: StrategyMode
    confidence: float
    reason: str
    evidence: dict[str, float] = field(default_factory=dict)
    forward_days: int = 3  # ML 예측 기간 (스윙 기본 3일)
    forward_minutes: int | None = None  # 단타 모드 시 분봉 예측 기간 (분)


# ── Strategy config injection ──
from core.config import StrategyConfig as _StrategyConfig


def select_mode(
    regime: RegimeResult,
    atr_pct: float | None = None,
    volatility_pct: float | None = None,
    config: _StrategyConfig | None = None,
) -> ModeDecision:
    """시장 레짐 + 변동성 → 매매 모드 선택.

    Args:
        regime: 레짐 평가 결과.
        atr_pct: 현재 시장/종목 ATR(%) - None이면 중립 가정.
        volatility_pct: 최근 변동성(%) - None이면 중립 가정.

    Returns:
        ModeDecision with mode and reason.
    """
    vol = volatility_pct or atr_pct or 0.5
    regime_score = regime.score
    cfg = config or _StrategyConfig()

    evidence: dict[str, float] = {
        "regime_score": regime_score,
        "regime": {"bull": 0, "neutral": 1, "bear": 2}.get(regime.regime.value, 1),
        "atr_pct": vol,
        "high_volatility": 1.0 if vol >= cfg.mode_high_volatility_atr_pct else 0.0,
        "low_volatility": 1.0 if vol <= cfg.mode_low_volatility_atr_pct else 0.0,
        "strong_trend": 1.0 if regime_score >= cfg.regime_bull_threshold - 5 else 0.0,
    }

    if regime.regime == MarketRegime.BULL:
        # 강세장 → 스윙 (추세를 따라간다)
        if vol >= cfg.mode_high_volatility_atr_pct:
            # 고변동성 강세 = 일부 단타도 가능하지만 기본은 스윙
            return ModeDecision(
                mode=StrategyMode.SWING,
                confidence=0.7,
                reason=(
                    f"강세장 · 고변동성: 레짐 점수 {regime_score:.0f}/100, "
                    f"ATR {vol:.1f}%. 기본 스윙 모드. "
                    f"고변동 구간이므로 단타 부분 활용 가능."
                ),
                forward_days=cfg.ml_swing_forward_days,
                evidence=evidence,
            )
        return ModeDecision(
            mode=StrategyMode.SWING,
            confidence=0.9,
            reason=(
                f"강세장 · 안정적: 레짐 점수 {regime_score:.0f}/100. "
                f"추세 추종 스윙 모드."
            ),
            forward_days=cfg.ml_swing_forward_days,
            evidence=evidence,
        )

    elif regime.regime == MarketRegime.BEAR:
        # 약세장 → 관망 (신규 진입 최소화)
        return ModeDecision(
            mode=StrategyMode.HOLD,
            confidence=0.85,
            reason=(
                f"약세장: 레짐 점수 {regime_score:.0f}/100. "
                f"신규 진입 금지, 기존 포지션만 청산."
            ),
            forward_days=cfg.ml_swing_forward_days,
            evidence=evidence,
        )

    else:  # NEUTRAL
        # 중립장 → 변동성에 따라 단타/스윙 결정
        if vol >= cfg.mode_high_volatility_atr_pct:
            return ModeDecision(
                mode=StrategyMode.SHORT_TERM,
                confidence=0.8,
                reason=(
                    f"중립장 · 고변동성: 레짐 점수 {regime_score:.0f}/100, "
                    f"ATR {vol:.1f}%. 방향성 없고 변동 높음 → 단타 모드."
                ),
                forward_minutes=cfg.ml_short_forward_minutes,
                evidence=evidence,
            )
        elif vol <= cfg.mode_low_volatility_atr_pct:
            # 저변동성 중립 = 방향성도 없고 움직임도 없음 → 관망
            return ModeDecision(
                mode=StrategyMode.HOLD,
                confidence=0.6,
                reason=(
                    f"중립장 · 저변동성: 레짐 점수 {regime_score:.0f}/100, "
                    f"ATR {vol:.1f}%. 시장 움직임 미약 → 관망."
                ),
                forward_days=cfg.ml_swing_forward_days,
                evidence=evidence,
            )
        else:
            # 중립 중간 변동 = 스윙
            return ModeDecision(
                mode=StrategyMode.SWING,
                confidence=0.55,
                reason=(
                    f"중립장 · 보통 변동성: 레짐 점수 {regime_score:.0f}/100. "
                    f"스윙 모드 유지, 단타 전환 조건 모니터링."
                ),
                forward_days=cfg.ml_swing_forward_days,
                evidence=evidence,
            )


def estimate_volatility_from_prices(
    closes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
    period: int = 14,
) -> float:
    """ATR%로 변동성 추정.

    True Range 기반 ATR을 계산하여 일관된 변동성 측정 제공.
    high/low가 없으면 표준편차 기반 CV로 폴백.

    Args:
        closes: 종가 리스트.
        highs: 고가 리스트 (True Range 계산용).
        lows: 저가 리스트.
        period: ATR 기간 (기본 14).

    Returns:
        ATR(%). 데이터 부족 시 0.5 반환.
    """
    closes_arr = np.asarray(closes, dtype=float)
    if len(closes_arr) < period + 1:
        return 0.5

    # high/low가 없으면 CV(변동계수)로 폴백
    if highs is None or lows is None or len(highs) < period + 1 or len(lows) < period + 1:
        recent = closes_arr[-period:]
        std = float(np.std(recent))
        mean = float(np.mean(recent))
        return (std / mean * 100) if mean > 0 else 0.5

    highs_arr = np.asarray(highs, dtype=float)
    lows_arr = np.asarray(lows, dtype=float)

    # True Range = max(H-L, |H-prevC|, |L-prevC|)
    prev_close = np.roll(closes_arr, 1)
    prev_close[0] = closes_arr[0]
    tr = np.maximum(
        highs_arr - lows_arr,
        np.maximum(np.abs(highs_arr - prev_close), np.abs(lows_arr - prev_close)),
    )

    # ATR = SMA of True Range over period
    atr = float(np.mean(tr[-period:]))
    current_price = float(closes_arr[-1])
    if current_price <= 0:
        return 0.5
    return atr / current_price * 100
