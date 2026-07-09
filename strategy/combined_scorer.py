"""NGSAT combined scorer — 일봉 점수 + 분봉 점수 통합.

두 개의 병렬 점수 체계(일봉 스크리너 + 분봉 스크리너)를
레짐별 가중치로 통합한 Combined Score를 계산한다.

Usage:
    from strategy.combined_scorer import compute_combined_score

    combined = compute_combined_score(
        daily_score=71.6,
        minute_score=83.0,
        regime="neutral",
        minute_confidence=0.8,
    )
"""

from __future__ import annotations

from core.types import MarketRegime

# 레짐별 기본 가중치 (일봉 % / 분봉 %)
_REGIME_WEIGHTS: dict[str, tuple[float, float]] = {
    "bull":     (0.70, 0.30),  # 강세장: 일봉 70% + 분봉 30%
    "neutral":  (0.50, 0.50),  # 중립장: 일봉 50% + 분봉 50% (단타 중요)
    "bear":     (0.80, 0.20),  # 약세장: 일봉 80% + 분봉 20% (보수적)
}


def compute_combined_score(
    daily_score: float,
    minute_score: float | None = None,
    regime: MarketRegime | str = "neutral",
    minute_confidence: float = 1.0,
) -> float:
    """Compute combined score from daily and minute scores.

    Args:
        daily_score: 일봉 스크리너 점수 (0~100).
        minute_score: 분봉 스크리너 점수 (0~100, None이면 daily만 사용).
        regime: 현재 시장 레짐 (bull/neutral/bear).
        minute_confidence: 분봉 데이터 신뢰도 (0~1).
            - 1.0: 완전 신뢰 (WebSocket 실시간 데이터 충분)
            - 0.5: 부분 신뢰 (REST 폴링, 데이터 지연)
            - 0.0: 불신 (분봉 데이터 없음)

    Returns:
        Combined score (0~100, 소수 첫째 자리 반올림).
    """
    # 분봉 점수가 없으면 일봉 점수만 반환
    if minute_score is None:
        return round(daily_score, 1)

    # 레짐별 가중치
    regime_key = regime.value if isinstance(regime, MarketRegime) else str(regime)
    w_daily, w_minute = _REGIME_WEIGHTS.get(regime_key, (0.50, 0.50))

    # 분봉 confidence로 가중치 보정 (데이터 부족 시 분봉 영향력 감소)
    w_minute *= max(0.0, min(1.0, minute_confidence))

    total_weight = w_daily + w_minute
    if total_weight <= 0:
        return round(daily_score, 1)

    combined = (daily_score * w_daily + minute_score * w_minute) / total_weight
    return round(combined, 1)


def compute_minute_confidence(
    minute_bars_count: int,
    has_websocket: bool = False,
    required_bars: int = 20,
) -> float:
    """분봉 데이터 충분도 평가 (0~1).

    WebSocket 실시간 연결 시 confidence 높음.
    REST 폴링만 가능하면 데이터 지연 고려.

    Args:
        minute_bars_count: 현재 확보된 분봉 캔들 수.
        has_websocket: WebSocket 실시간 연결 여부.
        required_bars: 필요 최소 분봉 수 (기본 20개).

    Returns:
        0.0 (데이터 없음) ~ 1.0 (완전 신뢰).
    """
    if minute_bars_count <= 0:
        return 0.0

    # WebSocket 연결: 실시간 = high confidence
    if has_websocket:
        if minute_bars_count >= required_bars:
            return 1.0
        # 데이터가 덜 쌓여도 WebSocket이면 빠르게 채워짐
        return min(1.0, minute_bars_count / required_bars * 1.2)

    # REST 폴링: 지연 고려, 최대 0.8
    if minute_bars_count >= required_bars:
        return 0.8
    return min(0.8, minute_bars_count / required_bars * 0.6)
