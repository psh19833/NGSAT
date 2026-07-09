"""NGSAT 진입 타이밍 정밀화 — 분봉 기반 진입 판단.

하이브리드 매매의 1단계: 스윙 ML이 '매수'로 판단한 종목에 대해,
분봉(1~5분 캔들)을 보고 '지금 살지(타이밍)'와 '얼마에 살지(가격)'를
정밀화한다.

원칙:
- 정밀화는 '개선'이지 '차단'이 아니다. 분봉이 부족하면 시장가 진입으로 폴백.
- 모든 결정에 근거(reason)를 남긴다 (NGSAT 핵심 원칙).
- 순수 함수: KIS 호출 없이 분봉 PriceData 리스트만 받아 판단.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from core.types import PriceData
from core.logger import logger
from strategy.indicators import current_rsi


class EntryTiming(str, Enum):
    """진입 타이밍 판정."""
    ENTER_NOW = "enter_now"   # 지금 진입
    WAIT = "wait"             # 이번 틱 보류 — 다음 틱 재평가


@dataclass(frozen=True)
class EntryDecision:
    """진입 정밀화 결정.

    Attributes:
        timing: ENTER_NOW / WAIT
        should_enter: 이번에 진입할지 여부
        limit_price: 권장 지정가 (None = 시장가)
        reason: 근거 (한글)
        evidence: 정량 근거
    """
    timing: EntryTiming
    should_enter: bool
    limit_price: float | None
    reason: str
    evidence: dict[str, float] = field(default_factory=dict)


# ── 기본 파라미터 (보수적) ──
DEFAULT_OVERHEAT_RSI = 75.0        # 분봉 RSI 이 값 초과 → 과열, 추격 보류
DEFAULT_SURGE_LOOKBACK = 5         # 최근 N분봉
DEFAULT_SURGE_THRESHOLD_PCT = 3.0  # 최근 N분봉 등락률 이 값 초과 → 급등, 추격 보류
DEFAULT_MIN_BARS = 20              # 분봉 최소 개수 (미만이면 정밀화 생략)


def refine_entry(
    minute_prices: list[PriceData],
    *,
    overheat_rsi: float = DEFAULT_OVERHEAT_RSI,
    surge_lookback: int = DEFAULT_SURGE_LOOKBACK,
    surge_threshold_pct: float = DEFAULT_SURGE_THRESHOLD_PCT,
    min_bars: int = DEFAULT_MIN_BARS,
) -> EntryDecision:
    """분봉으로 진입 타이밍/가격을 정밀화한다.

    스윙 ML이 이미 '매수'로 판단한 종목에 대해 호출한다.
    분봉이 과열(RSI 높음)이거나 단기 급등 중이면 추격매수를 보류하고,
    그 외에는 현재가 지정가로 진입을 제안한다.

    Args:
        minute_prices: 분봉 PriceData 리스트. 마지막 원소를 '현재 분봉'으로 간주.
        overheat_rsi: 과열 판정 RSI 임계값.
        surge_lookback: 단기 급등 판정에 쓸 최근 분봉 개수.
        surge_threshold_pct: 급등 판정 등락률(%).
        min_bars: 정밀화에 필요한 최소 분봉 개수.

    Returns:
        EntryDecision (항상 근거 포함).
    """
    n = len(minute_prices)

    # 1) 데이터 부족 → 정밀화 생략, 시장가 진입 폴백
    if n < min_bars:
        return EntryDecision(
            timing=EntryTiming.ENTER_NOW,
            should_enter=True,
            limit_price=None,
            reason=f"분봉 데이터 부족({n}개<{min_bars}) — 정밀화 생략(시장가 진입)",
            evidence={"bars": float(n)},
        )

    closes = np.array([p.close for p in minute_prices], dtype=float)
    current_price = float(closes[-1])

    # 2) 단기 급등 가드 (최근 N분봉 등락률)
    lookback = min(surge_lookback, n - 1)
    past = float(closes[-1 - lookback]) if lookback >= 1 else current_price
    surge_pct = ((current_price - past) / past * 100.0) if past > 0 else 0.0

    # 3) 분봉 RSI
    rsi_val = current_rsi(closes, 14)
    rsi_safe = rsi_val if not np.isnan(rsi_val) else 50.0

    evidence = {
        "rsi": round(rsi_safe, 2),
        "surge_pct": round(surge_pct, 2),
        "current_price": current_price,
        "overheat_rsi": overheat_rsi,
        "surge_threshold_pct": surge_threshold_pct,
        "bars": float(n),
    }

    # 급등 추격 보류
    if surge_pct > surge_threshold_pct:
        reason = (
            f"최근 {lookback}분봉 +{surge_pct:.1f}% 급등 "
            f"(>{surge_threshold_pct:.0f}%) — 추격매수 보류, 눌림 대기"
        )
        logger.info(f"매수 보류(급등): 코드=?, RSI={rsi_safe:.1f}, surge={surge_pct:+.1f}%, 사유={reason}")
        return EntryDecision(
            timing=EntryTiming.WAIT,
            should_enter=False,
            limit_price=None,
            reason=reason,
            evidence=evidence,
        )

    # RSI 과열 보류
    if rsi_safe > overheat_rsi:
        reason = (
            f"분봉 RSI {rsi_safe:.1f} 과열(>{overheat_rsi:.0f}) "
            f"— 추격매수 보류, 다음 기회 대기"
        )
        logger.info(f"매수 보류(과열): 코드=?, RSI={rsi_safe:.1f}, surge={surge_pct:+.1f}%, 사유={reason}")
        return EntryDecision(
            timing=EntryTiming.WAIT,
            should_enter=False,
            limit_price=None,
            reason=reason,
            evidence=evidence,
        )

    # 4) 진입 적합 → 시장가 즉시 체결
    # 지정가 미체결 무한반복 방지: surge/RSI/VI 3중 가드 통과 후에는
    # 시장가로 즉시 체결. 슬리피지는 소량(2~5주) 기준 미미함.
    return EntryDecision(
        timing=EntryTiming.ENTER_NOW,
        should_enter=True,
        limit_price=None,
        reason=(
            f"분봉 RSI {rsi_safe:.1f} 정상·급등 위험 없음(최근 {lookback}분 "
            f"{surge_pct:+.1f}%) — 시장가 진입"
        ),
        evidence=evidence,
    )
