"""NGSAT 청산 타이밍 정밀화 — 분봉 기반 청산 판단.

하이브리드 매매의 1단계(진입 정밀화의 대칭): 보유 종목에 대해 분봉을 보고
'분봉이 청산을 권고하는가', '얼마나 급하게(시장가/지정가)', '어떤 가격에'
파는 게 유리한지 정밀화한다.

두 가지 분봉 신호:
- 급락: 최근 N분봉 급락 → 즉시 시장가 청산(빠른 손절)
- 수익+과열: 수익 중 + 분봉 RSI 과열 → 익절(지정가)

원칙:
- 정밀화는 보조 신호. 분봉이 부족하면 기존 일봉 청산 로직에 맡긴다(생략).
- 모든 결정에 근거(reason)를 남긴다 (NGSAT 핵심 원칙).
- 순수 함수: 분봉 PriceData 리스트 + 현재 수익률만 받아 판단.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from core.types import PriceData
from strategy.indicators import current_rsi


class ExitUrgency(str, Enum):
    """청산 긴급도."""
    IMMEDIATE = "immediate"  # 시장가 즉시 청산 (급락)
    NORMAL = "normal"        # 지정가 청산
    NONE = "none"            # 분봉상 청산 불필요(정밀화 생략 포함)


@dataclass(frozen=True)
class ExitDecision:
    """청산 정밀화 결정.

    Attributes:
        should_exit: 분봉이 자체적으로 청산을 권고하는지(선제 트리거).
        urgency: IMMEDIATE(시장가) / NORMAL(지정가) / NONE.
        limit_price: 정상 매도 시 권장 지정가(분봉 현재가). None=시장가/미가용.
        reason: 근거(한글).
        evidence: 정량 근거.
    """
    should_exit: bool
    urgency: ExitUrgency
    limit_price: float | None
    reason: str
    evidence: dict[str, float] = field(default_factory=dict)


# ── 기본 파라미터 (보수적; 추후 리서치로 세밀 조정) ──
DEFAULT_PLUNGE_LOOKBACK = 5          # 최근 N분봉
DEFAULT_PLUNGE_THRESHOLD_PCT = 3.0   # 최근 N분봉 -이 값% 이하 → 급락
DEFAULT_TAKE_PROFIT_MIN_PCT = 5.0    # 수익 이 값% 이상 + 과열 → 익절
DEFAULT_OVERHEAT_RSI = 75.0          # 분봉 RSI 이 값 이상 → 과열
DEFAULT_MIN_BARS = 20


def refine_exit(
    minute_prices: list[PriceData],
    current_profit_pct: float,
    *,
    plunge_lookback: int = DEFAULT_PLUNGE_LOOKBACK,
    plunge_threshold_pct: float = DEFAULT_PLUNGE_THRESHOLD_PCT,
    take_profit_min_pct: float = DEFAULT_TAKE_PROFIT_MIN_PCT,
    overheat_rsi: float = DEFAULT_OVERHEAT_RSI,
    min_bars: int = DEFAULT_MIN_BARS,
) -> ExitDecision:
    """보유 종목에 대해 분봉으로 청산을 정밀화한다.

    Args:
        minute_prices: 분봉 PriceData 리스트. 마지막 원소를 '현재 분봉'으로 간주.
        current_profit_pct: 현재 평가 손익률(%).
        plunge_lookback: 급락 판정에 쓸 최근 분봉 개수.
        plunge_threshold_pct: 급락 판정 하락률(%).
        take_profit_min_pct: 익절 검토 최소 수익률(%).
        overheat_rsi: 과열 판정 RSI 임계값.
        min_bars: 정밀화에 필요한 최소 분봉 개수.

    Returns:
        ExitDecision (항상 근거 포함).
    """
    n = len(minute_prices)

    # 데이터 부족 → 정밀화 생략 (기존 일봉 청산 로직에 위임)
    if n < min_bars:
        return ExitDecision(
            should_exit=False,
            urgency=ExitUrgency.NONE,
            limit_price=None,
            reason=f"분봉 데이터 부족({n}개<{min_bars}) — 청산 정밀화 생략",
            evidence={"bars": float(n)},
        )

    closes = np.array([p.close for p in minute_prices], dtype=float)
    current_price = float(closes[-1])

    # P-81: volume=0 bar(전일종가/장전데이터)를 현재가로 사용하지 않음
    if minute_prices[-1].volume == 0:
        return ExitDecision(
            should_exit=False,
            urgency=ExitUrgency.NONE,
            limit_price=None,
            reason=f"마지막 분봉 거래량 0 (volume={minute_prices[-1].volume}) — limit_price 신뢰 불가, 시장가 fallback",
            evidence={"bars": float(n), "volume": 0.0},
        )

    lookback = min(plunge_lookback, n - 1)
    past = float(closes[-1 - lookback]) if lookback >= 1 else current_price
    change_pct = ((current_price - past) / past * 100.0) if past > 0 else 0.0

    rsi_val = current_rsi(closes, 14)
    rsi_safe = rsi_val if not np.isnan(rsi_val) else 50.0

    evidence = {
        "rsi": round(rsi_safe, 2),
        "change_pct": round(change_pct, 2),
        "current_price": current_price,
        "profit_pct": round(current_profit_pct, 2),
        "plunge_threshold_pct": plunge_threshold_pct,
        "take_profit_min_pct": take_profit_min_pct,
        "overheat_rsi": overheat_rsi,
        "bars": float(n),
    }

    # 1) 급락 → 즉시 시장가 청산
    if change_pct <= -plunge_threshold_pct:
        return ExitDecision(
            should_exit=True,
            urgency=ExitUrgency.IMMEDIATE,
            limit_price=None,
            reason=(
                f"최근 {lookback}분봉 {change_pct:.1f}% 급락 "
                f"(<= -{plunge_threshold_pct:.0f}%) — 즉시 시장가 청산"
            ),
            evidence=evidence,
        )

    # 2) 수익 + 분봉 과열 → 익절 (지정가)
    if current_profit_pct >= take_profit_min_pct and rsi_safe >= overheat_rsi:
        return ExitDecision(
            should_exit=True,
            urgency=ExitUrgency.NORMAL,
            limit_price=current_price,
            reason=(
                f"수익 {current_profit_pct:.1f}% + 분봉 RSI {rsi_safe:.1f} 과열 "
                f"— 익절(현재가 {current_price:,.0f}원 지정가)"
            ),
            evidence=evidence,
        )

    # 3) 분봉 청산 신호 없음 — 보유 유지(정상 매도 시 현재가 지정가 제공)
    return ExitDecision(
        should_exit=False,
        urgency=ExitUrgency.NORMAL,
        limit_price=current_price,
        reason=(
            f"분봉 청산 신호 없음(최근 {lookback}분 {change_pct:+.1f}%, "
            f"RSI {rsi_safe:.1f}) — 보유 유지, 정상 매도 시 현재가 {current_price:,.0f}원 지정가"
        ),
        evidence=evidence,
    )
