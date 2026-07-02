"""NGSAT preset router — automatically selects trading preset based on regime + volatility.

PresetRouter maps market conditions (regime + volatility) to strategy presets
defined in config/presets.json. The orchestrator calls select_preset() each
cycle to determine the optimal preset for current market conditions.

Key features:
- 6-way mapping: regime (bull/neutral/bear) × volatility (low/high)
- Hysteresis: score must differ by at least HYSTERESIS_POINTS to switch
- User override: manual preset selection disables auto until market close
- Cooldown: minimum time between auto-switches

Usage:
    router = PresetRouter()
    preset_name = router.select_preset(regime_result, atr_pct)
    if preset_name != router.current_preset:
        # apply the new preset
"""

from __future__ import annotations

import time
from datetime import datetime

from core.logger import logger
from core.types import MarketRegime
from strategy.regime import RegimeResult


class PresetRouter:
    """시장 상황 기반 프리셋 자동 선택기.

    Attributes:
        current_preset: 현재 활성 프리셋 이름.
        auto_enabled: 자동 전환 활성화 여부.
        _override_until: 사용자 수동 선택 시 오버라이드 만료 시간 (timestamp).
        _last_switch: 마지막 자동 전환 시간 (timestamp).
    """

    # 기본 매핑: (regime, is_high_volatility) → preset_name
    DEFAULT_MAPPING: dict[tuple[str, bool], str] = {
        (MarketRegime.BULL.value, False):     "스윙형",      # 강세+저변동
        (MarketRegime.BULL.value, True):      "공격형",      # 강세+고변동
        (MarketRegime.NEUTRAL.value, False):  "균형형",     # 중립+저변동
        (MarketRegime.NEUTRAL.value, True):   "단타형",     # 중립+고변동
        (MarketRegime.BEAR.value, False):     "안정형",     # 약세+저변동
        (MarketRegime.BEAR.value, True):      "AI집중형",   # 약세+고변동
    }

    HYSTERESIS_POINTS = 5     # 점수 차이가 이 값 이상일 때만 전환
    COOLDOWN_SECONDS = 1800   # 자동 전환 간 최소 간격 (30분)
    HIGH_VOL_THRESHOLD = 1.5  # ATR 기준 고변동성 임계값 (%)

    def __init__(self) -> None:
        self.current_preset: str | None = None
        self.auto_enabled: bool = True
        self._override_until: float = 0.0  # timestamp
        self._last_switch: float = 0.0
        self._last_score: float = 50.0  # hysteresis 비교용 직전 레짐 점수

    def select_preset(
        self,
        regime: RegimeResult,
        atr_pct: float | None = None,
    ) -> str | None:
        """현재 시장 상황에 맞는 프리셋을 선택.

        Args:
            regime: 레짐 평가 결과.
            atr_pct: 현재 ATR (%). None이면 저변동성 가정.

        Returns:
            선택된 프리셋 이름. 변경이 없으면 None.
        """
        if not self.auto_enabled:
            return None

        # 사용자 오버라이드 체크 (당일 장 종료 전까지)
        if time.time() < self._override_until:
            return None

        # 쿨다운 체크
        if time.time() - self._last_switch < self.COOLDOWN_SECONDS:
            return None

        vol = atr_pct or 0.5
        is_high_vol = vol >= self.HIGH_VOL_THRESHOLD
        key = (regime.regime.value, is_high_vol)

        target = self.DEFAULT_MAPPING.get(key)
        if target is None or target == self.current_preset:
            return None

        # 히스테리시스: 점수 차이가 HYSTERESIS_POINTS 이상일 때만 전환
        if self.current_preset is not None:
            if abs(regime.score - self._last_score) < self.HYSTERESIS_POINTS:
                return None

        logger.info(
            f"PresetRouter: {regime.regime.value} "
            f"{'고변동' if is_high_vol else '저변동'} "
            f"→ {target} (기존: {self.current_preset}, "
            f"점수: {self._last_score:.0f}→{regime.score:.0f})"
        )
        self.current_preset = target
        self._last_score = regime.score
        self._last_switch = time.time()
        return target

    def set_user_override(self) -> None:
        """사용자가 수동으로 프리셋 선택 → 당일 장 종료까지 오버라이드."""
        now = datetime.now()
        # 당일 15:30 KST = 06:30 UTC
        market_close = now.replace(hour=6, minute=30, second=0, microsecond=0)
        if now >= market_close:
            market_close = market_close.replace(day=market_close.day + 1)
        self._override_until = market_close.timestamp()
        logger.info(
            f"PresetRouter: 사용자 오버라이드 활성화 "
            f"— {market_close.strftime('%H:%M')}까지 자동 전환 중단"
        )

    def reset_override(self) -> None:
        """오버라이드 초기화 (새 거래일)."""
        self._override_until = 0.0
        self.auto_enabled = True
        logger.info("PresetRouter: 오버라이드 초기화 — 자동 전환 재개")

    def set_auto_enabled(self, enabled: bool) -> None:
        """자동 전환 ON/OFF."""
        self.auto_enabled = enabled
        logger.info(f"PresetRouter: 자동 전환 {'활성화' if enabled else '비활성화'}")
