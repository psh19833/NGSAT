"""NGSAT 동적 유니버스 관리자 — 5분 간격 4축 점수 기반 교체.

UniverseManager는 40종목 active(매매 대상) + 60종목 reserve(예비)를 관리하며,
5분마다 4개 축(거래량/체결강도/등락률/스크리너) 점수로 하위 20종목을 교체한다.

Usage:
    um = UniverseManager()
    await um.initialize(broker, provider)
    await um.swap(broker, provider)  # 5분마다
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from core.logger import logger
from core.types import Market, StockInfo

KST = timedelta(hours=9)


@dataclass
class ScoredStock:
    """4축 점수가 계산된 종목."""
    code: str
    name: str = ""
    market: Market = Market.KOSPI
    # 개별 축 점수 (0~100)
    volume_score: float = 0.0      # 거래량순위 점수
    power_score: float = 0.0       # 체결강도순위 점수
    fluct_score: float = 0.0       # 등락률순위 점수
    screener_score: float = 0.0    # 스크리너 기술점수
    # 가중치 적용 합산 점수
    composite_score: float = 0.0

    # 가중치 (클래스 변수)
    W_VOLUME: float = 0.25
    W_POWER: float = 0.20
    W_FLUCT: float = 0.25
    W_SCREENER: float = 0.30

    def compute_score(self) -> None:
        """4개 축 점수를 가중치로 합산."""
        self.composite_score = (
            self.volume_score * self.W_VOLUME
            + self.power_score * self.W_POWER
            + self.fluct_score * self.W_FLUCT
            + self.screener_score * self.W_SCREENER
        )


class UniverseManager:
    """동적 유니버스 — 5분마다 하위 50% 교체."""

    def __init__(self):
        self.active: dict[str, ScoredStock] = {}     # 현재 매매 중 40종목
        self.reserve: dict[str, ScoredStock] = {}    # 예비 60종목
        self.held_codes: set[str] = set()             # 보유 포지션 (제외 불가)
        self.last_swap: Optional[datetime] = None
        self.initialized = False
        self._initial_rank_codes: set[str] = set()    # 09:00에 로드한 100종목

    # ── Public API ──

    async def initialize(self, broker: Any, provider: Any) -> None:
        """09:00 초기화 — 3개 순위API + 4축 점수 → TOP 40 + 예비 60."""
        logger.info("유니버스 초기화 시작 — 3개 순위 API 호출")

        # 1. 3개 순위 API 동시 호출
        volume_rank, volume_power, fluctuation = await asyncio.gather(
            broker.get_volume_rank(),
            broker.get_volume_power(),
            broker.get_fluctuation_rank(100),
            return_exceptions=True,
        )

        if isinstance(volume_rank, Exception) or not volume_rank:
            logger.warning(f"거래량순위 API 실패: {volume_rank}")
            volume_rank = []
        if isinstance(volume_power, Exception) or not volume_power:
            logger.warning(f"체결강도 API 실패: {volume_power}")
            volume_power = []
        if isinstance(fluctuation, Exception) or not fluctuation:
            logger.warning(f"등락률 API 실패: {fluctuation}")
            fluctuation = []

        # 2. 통합 코드 리스트
        all_codes = self._merge_rankings(volume_rank, volume_power, fluctuation)
        if not all_codes:
            logger.error("유니버스 초기화 실패 — 모든 순위 API 실패")
            return

        self._initial_rank_codes = set(all_codes)

        # 3. 4축 점수 계산
        scored = await self._score_candidates(all_codes, provider,
                                              volume_rank, volume_power, fluctuation)

        # 4. TOP 40 = active, 41~100 = reserve
        scored.sort(key=lambda x: x.composite_score, reverse=True)
        self.active = {s.code: s for s in scored[:40]}
        self.reserve = {s.code: s for s in scored[40:100]}

        # 5. 100종목 일봉 데이터 로드 (Rate Limit 보호)
        await self._load_daily_data(all_codes[:100], provider)

        # 6. WebSocket 구독
        if provider._ws:
            ws_codes = list(self.active.keys())
            await provider._ws.swap_universe(ws_codes, [])
            logger.info(f"WebSocket 구독: {len(ws_codes)}종목")

        self.initialized = True
        logger.info(f"유니버스 초기화 완료: active={len(self.active)}, reserve={len(self.reserve)}")

    async def swap(self, broker: Any, provider: Any) -> None:
        """5분 교체 — 하위 20 제외 + 상위 20 편입."""
        if len(self.held_codes) >= 20:
            logger.info(f"보유 포지션 {len(self.held_codes)}개 — 교체 스킵")
            return

        # 1. 3개 순위 API 동시 호출
        volume_rank, volume_power, fluctuation = await asyncio.gather(
            broker.get_volume_rank(),
            broker.get_volume_power(),
            broker.get_fluctuation_rank(100),
            return_exceptions=True,
        )
        if isinstance(volume_rank, Exception) or not volume_rank:
            volume_rank = []

        # 2. 현재 active 40종목 재평가
        active_list = list(self.active.keys())
        active_scored = await self._score_candidates(
            active_list, provider, volume_rank, volume_power, fluctuation
        )
        active_scored.sort(key=lambda x: x.composite_score)

        # 3. 하위 20 선정 (보유포지션 제외)
        to_remove = [s for s in active_scored if s.code not in self.held_codes][:20]
        remove_codes = {s.code for s in to_remove}
        if not remove_codes:
            logger.info("교체할 종목 없음")
            return

        # 4. 편입 후보: 예비 60 + 신규 (rank에 있지만 active/reserve에 없는 종목)
        new_codes = set()
        if volume_rank:
            new_codes = {item["code"] for item in volume_rank[:100]}
            new_codes -= set(self.active.keys())
            new_codes -= set(self.reserve.keys())
            new_codes -= self.held_codes

        candidate_codes = list(self.reserve.keys()) + list(new_codes)
        if not candidate_codes:
            logger.warning("편입 후보 없음")
            return

        candidates = await self._score_candidates(
            candidate_codes, provider, volume_rank, volume_power, fluctuation
        )
        candidates.sort(key=lambda x: x.composite_score, reverse=True)
        to_add = candidates[:min(20, len(candidates))]

        # 5. 교체 실행
        for s in to_add:
            self.active[s.code] = s
        for s in to_remove:
            self.active.pop(s.code, None)
            self.reserve[s.code] = s

        # 6. 예비 리스트 갱신 (최대 60, active 제외)
        reserve_codes = list(self.reserve.keys()) + [s.code for s in to_remove]
        reserve_codes = [c for c in reserve_codes if c not in self.active]
        new_reserve = {}
        for code in reserve_codes[:60]:
            if code in self.reserve:
                new_reserve[code] = self.reserve[code]
        self.reserve = new_reserve

        # 7. 신규 편입 종목 일봉 로드
        new_entries = [s.code for s in to_add if s.code not in self._initial_rank_codes]
        if new_entries:
            await self._load_daily_data(new_entries, provider)
            self._initial_rank_codes.update(new_entries)

        # 8. WebSocket 구독 교체
        if provider._ws:
            await provider._ws.swap_universe(
                [s.code for s in to_add],
                [s.code for s in to_remove],
            )

        logger.info(f"유니버스 교체: -{len(to_remove)}(하위) +{len(to_add)}(상위) = {len(self.active)}종목")

    def should_swap(self, now: Optional[datetime] = None) -> bool:
        """5분 경과 여부 확인."""
        if not self.initialized or not self.last_swap:
            return False
        now = now or (datetime.utcnow() + KST)
        return (now - self.last_swap).total_seconds() >= 300

    def get_active_codes(self) -> list[str]:
        return list(self.active.keys())

    def get_active_stocks(self) -> list[ScoredStock]:
        return list(self.active.values())

    # ── Internal ──

    def _merge_rankings(self, vr: list[dict], vp: list[dict], fl: list[dict]) -> list[str]:
        """3개 순위 데이터 → 통합 코드 리스트 (중복 제거)."""
        codes: dict[str, int] = {}
        for lst in [vr, vp, fl]:
            for item in lst[:100]:
                code = item.get("code", "")
                if code:
                    codes[code] = codes.get(code, 0) + 1
        return sorted(codes.keys(), key=lambda c: codes[c], reverse=True)

    async def _score_candidates(
        self,
        codes: list[str],
        provider: Any,
        volume_rank: Optional[list[dict]] = None,
        volume_power: Optional[list[dict]] = None,
        fluctuation: Optional[list[dict]] = None,
    ) -> list[ScoredStock]:
        """4축 점수 계산.

        Args:
            codes: 평가할 종목코드 리스트.
            provider: RealDataProvider (스크리너 점수용).
            volume_rank: 거래량순위 데이터 (없으면 0점 처리).
            volume_power: 체결강도순위 데이터.
            fluctuation: 등락률순위 데이터.
        """
        # 순위 lookup
        vol_rank_map = {item["code"]: i for i, item in enumerate(volume_rank or [])}
        power_map = {item["code"]: i for i, item in enumerate(volume_power or [])}
        fluct_map = {item["code"]: i for i, item in enumerate(fluctuation or [])}

        # 스크리너: 일봉 데이터로 기술점수 계산
        from strategy.screener import screen_stocks
        from core.config import load_config

        config = load_config()
        universe_data = provider._universe_cache or []

        result = []
        for code in codes:
            # 순위 점수 (normalize: 1등=100, 100등=0)
            vr = max(0, 100 - vol_rank_map.get(code, 999))
            vp = max(0, 100 - power_map.get(code, 999))
            fl = max(0, 100 - fluct_map.get(code, 999))

            # 스크리너 점수 (일봉 데이터 기반)
            screener_score = 50.0  # 기본 중립
            for info, prices in universe_data:
                if info.code == code:
                    try:
                        from core.types import MarketRegime
                        dummy_regime = MarketRegime(
                            regime="neutral", score=50, mode="hold",
                            reason="universe scoring"
                        )
                        # 단순 스크리너: stock 객체를 임시로 구성해야 함
                        # 실제 구현 시 screen_stocks는 복잡하므로 단순화
                        screener_score = 50.0  # 기본값, 필요시 개선
                    except Exception:
                        pass
                    break

            stock = ScoredStock(
                code=code,
                volume_score=vr,
                power_score=vp,
                fluct_score=fl,
                screener_score=50.0,  # 기술점수는 일봉 데이터 필요시 추가 구현
            )
            stock.compute_score()
            result.append(stock)

        return result

    async def _load_daily_data(self, codes: list[str], provider: Any) -> None:
        """종목들의 일봉 데이터 로드 (Rate Limit 100ms 간격)."""
        adapter = None
        loaded = 0
        for code in codes:
            try:
                # 이미 로드된 종목은 스킵
                if provider._universe_cache and any(
                    info.code == code for info, _ in provider._universe_cache
                ):
                    loaded += 1
                    continue
                if adapter is None:
                    from data.adapters.kis.adapter import KisAdapter
                    adapter = provider._adapter
                if adapter:
                    prices = await adapter.get_daily_chart(code)
                    if prices:
                        from data.real_data_provider import _infer_market
                        market = _infer_market(code)
                        if provider._universe_cache is None:
                            provider._universe_cache = []
                        provider._universe_cache.append(
                            (StockInfo(code=code, name="", market=market), prices)
                        )
                        loaded += 1
                    await asyncio.sleep(0.1)  # Rate Limit
            except Exception as e:
                logger.warning(f"[{code}] 일봉 로드 실패: {e}")
        logger.info(f"일봉 로드: {loaded}/{len(codes)}종목")
