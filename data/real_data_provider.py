"""NGSAT 실시간 시장 데이터 제공자 — KIS API 실데이터 연결.

main.py의 합성 데이터를 대체하여 실제 KIS 데이터를
오케스트레이터에 공급한다.

사용법:
    from data.real_data_provider import RealDataProvider
    provider = RealDataProvider()
    universe, index_prices = await provider.load()

주의:
    - KIS 토큰 발급 필요 (1회/분 제한)
    - 일봉 데이터는 하루 1회만 새로고침 (KIS rate limit)
    - KOSPI 지수는 동일한 daily_chart endpoint 사용 (FID_COND_MRKT_DIV_CODE=U)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from core.config import load_config
from core.logger import logger
from core.types import Market, PriceData, StockInfo

KST = timezone(timedelta(hours=9))

# 기본 수집 종목 (KOSPI 30)
DEFAULT_UNIVERSE_CODES: list[str] = [
    "005930", "000660", "373220", "207940", "005380",
    "000270", "068270", "105560", "055550", "035420",
    "000810", "012330", "006400", "028260", "032830",
    "086790", "003550", "066570", "015760", "017670",
    "329180", "138040", "096770", "018260", "034730",
    "323410", "259960", "352820", "247540", "196170",
]


class RealDataProvider:
    """KIS API에서 실제 시장 데이터를 로드하는 제공자.

    캐싱 전략:
    - 일봉 데이터: 최초 로드 후 세션 동안 메모리 캐싱
    - 분봉 데이터: 호출 시마다 KIS에서 실시간 조회
    - 지수 데이터: KOSPI 일봉 (FID_COND_MRKT_DIV_CODE=U)
    """

    def __init__(self, codes: list[str] | None = None):
        self._codes = codes or DEFAULT_UNIVERSE_CODES
        self._adapter: Any = None
        self._universe_cache: list[tuple[StockInfo, list[PriceData]]] | None = None
        self._index_cache: list[PriceData] | None = None
        self._cache_date: str = ""

    async def _get_adapter(self):
        """Lazy-create KIS adapter with .env loaded."""
        if self._adapter is None:
            load_config()  # .env 로드
            from data.adapters.kis.adapter import KisAdapter
            self._adapter = KisAdapter.from_env()
        return self._adapter

    async def load(self) -> tuple[list[tuple[StockInfo, list[PriceData]]], list[PriceData]]:
        """전체 시장 데이터 로드 (캐시 갱신).

        Returns:
            (universe, index_prices)
            universe: [(StockInfo, daily_price_list), ...]
            index_prices: KOSPI 지수 일봉 리스트
        """
        adapter = await self._get_adapter()
        today = datetime.now(KST).strftime("%Y-%m-%d")

        # 하루 1회만 캐시 갱신
        if self._universe_cache is not None and self._cache_date == today:
            logger.debug(f"데이터 캐시 사용 (날짜: {today})")
            return self._universe_cache, self._index_cache

        logger.info(f"KIS 실데이터 로드 시작: 종목 {len(self._codes)}개")
        end = datetime.now(KST)
        start = end - timedelta(days=250)  # 약 1년

        # 1. 종목별 일봉 데이터
        universe: list[tuple[StockInfo, list[PriceData]]] = []
        for i, code in enumerate(self._codes):
            try:
                prices = await adapter.get_price_history(code, start, end)
                if prices:
                    market = Market.KOSPI  # 기본값
                    info = StockInfo(code=code, name=_code_to_name(code), market=market)
                    universe.append((info, prices))
            except Exception as e:
                logger.warning(f"[{code}] 데이터 로드 실패: {type(e).__name__}")

            if (i + 1) % 10 == 0:
                logger.info(f"  진행: {i + 1}/{len(self._codes)} 종목")

            # KIS rate limit: 50ms 간격
            import asyncio
            await asyncio.sleep(0.05)

        if not universe:
            logger.error("KIS 실데이터 로드 실패 — 모든 종목 조회 실패")
            return [], []

        # 2. KOSPI 지수 데이터
        index_prices = await self._fetch_index(adapter)

        self._universe_cache = universe
        self._index_cache = index_prices
        self._cache_date = today

        logger.info(f"KIS 실데이터 로드 완료: {len(universe)}종목, 지수 {len(index_prices)}일")
        return universe, index_prices

    async def _fetch_index(self, adapter) -> list[PriceData]:
        """KOSPI 지수 일봉 데이터 조회.

        inquire-daily-indexchartprice (FHPUP02110000) 사용.
        KOSPI 지수 코드는 0001, 시장구분코드 U(업종).
        """
        end = datetime.now(KST)
        start = end - timedelta(days=250)

        try:
            from data.adapters.kis.endpoints import get_endpoint
            from data.adapters.kis.mapper import parse_index_history

            params = {
                "FID_COND_MRKT_DIV_CODE": "U",
                "FID_INPUT_ISCD": "0001",
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
            }
            resp = await adapter._http.get("inquire_index_daily", params=params)
            if resp.success:
                prices = parse_index_history(resp.raw, code="KOSPI")
                logger.info(f"KOSPI 지수 조회: {len(prices)}일")
                return prices
            else:
                logger.warning(f"KOSPI 지수 조회 실패: {resp.msg_cd} {resp.msg1}")
        except Exception as e:
            logger.warning(f"KOSPI 지수 조회 실패: {type(e).__name__}")

        # 폴백: 합성 지수
        return self._synthetic_index()

    def _synthetic_index(self) -> list[PriceData]:
        """KOSPI 지수 조회 실패 시 합성 지수 반환."""
        logger.warning("KOSPI 지수 폴백: 합성 지수 사용")
        from backtest.data_loader import generate_synthetic_index
        return generate_synthetic_index(n_days=250, start_value=2600, seed=100)

    async def close(self):
        """리소스 정리."""
        if self._adapter:
            await self._adapter.close()

    @property
    async def is_available(self) -> bool:
        """KIS API 연결 가능 여부."""
        try:
            adapter = await self._get_adapter()
            from data.adapters.kis.endpoints import get_endpoint
            ep = get_endpoint("inquire_price")
            params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": "005930"}
            resp = await adapter._http.get("inquire_price", params=params)
            return resp.success
        except Exception:
            return False


def _code_to_name(code: str) -> str:
    """종목코드 → 종목명 (간단 매핑, 추후 KIS stock_info로 대체)."""
    names = {
        "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션",
        "207940": "삼성바이오로직스", "005380": "현대차", "000270": "기아",
        "068270": "셀트리온", "105560": "KB금융", "055550": "신한지주",
        "035420": "NAVER", "000810": "삼성화재", "012330": "현대모비스",
        "006400": "삼성SDI", "028260": "삼성물산", "032830": "삼성생명",
        "086790": "하나금융지주", "003550": "LG", "066570": "LG전자",
        "015760": "한국전력", "017670": "SK텔레콤", "329180": "HD현대중공업",
        "138040": "메리츠금융지주", "096770": "SK이노베이션", "018260": "삼성에스디에스",
        "034730": "SK", "323410": "카카오뱅크", "259960": "크래프톤",
        "352820": "하이브", "247540": "에코프로비엠", "196170": "알테오젠",
    }
    return names.get(code, f"종목{code}")
