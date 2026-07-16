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

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.config import load_config
from core.logger import logger
from core.types import Market, PriceData, StockInfo
from data.minute_bar_builder import MinuteBarBuilder

KST = ZoneInfo("Asia/Seoul")

# ── KOSPI / KOSDAQ 종목코드 (명시적 매핑) ──
# 설계: KOSPI 70% · KOSDAQ 30%
# KOSPI 28 + KOSDAQ 12 = 40종목 (28/40 = 70%)

KOSPI_CODES: list[str] = [
    "005930", "000660", "373220", "207940", "005380",  # 삼전, 하이닉스, LG엔솔, 삼바, 현대차
    "000270", "068270", "105560", "055550", "035420",  # 기아, 셀트리온, KB금융, 신한지주, NAVER
    "000810", "012330", "006400", "028260", "032830",  # 삼성화재, 현대모비스, 삼성SDI, 삼성물산, 삼성생명
    "086790", "003550", "066570", "015760", "017670",  # 하나금융, LG, LG전자, 한국전력, SKT
    "329180", "138040", "096770", "018260", "034730",  # HD현대중공업, 메리츠금융, SK이노, SDS, SK
    "323410", "259960", "352820",                       # 카카오뱅크, 크래프톤, 하이브
]

KOSDAQ_CODES: list[str] = [
    "247540", "196170",  # 에코프로비엠, 알테오젠 (기존 유지)
    "028300", "086520", "058470", "214150", "035900",  # HLB, 에코프로, 리노공업, 클래시스, JYP
    "403870", "068760", "263750", "257720", "240810",  # HPSP, 셀트리온제약, 펄어비스, 실리콘투, 원익IPS
]

DEFAULT_UNIVERSE_CODES: list[str] = KOSPI_CODES + KOSDAQ_CODES


def _infer_market(code: str) -> Market:
    """종목코드로 KOSPI/KOSDAQ 구분 (명시적 리스트 기반)."""
    code = code.strip()
    if code in KOSPI_CODES:
        return Market.KOSPI
    if code in KOSDAQ_CODES:
        return Market.KOSDAQ
    # 미등록 코드 → 첫자리 휴리스틱 (mapper.py _infer_market 동일 로직)
    if code and len(code) >= 6 and code[0] in ("0", "1"):
        return Market.KOSPI
    return Market.KOSDAQ


class RealDataProvider:
    """KIS API에서 실제 시장 데이터를 로드하는 제공자.

    캐싱 전략:
    - 일봉 데이터: 최초 로드 후 세션 동안 메모리 캐싱
    - 분봉 데이터: 호출 시마다 KIS에서 실시간 조회
    - 지수 데이터: KOSPI 일봉 (FID_COND_MRKT_DIV_CODE=U)
    """

    def __init__(
        self,
        codes: list[str] | None = None,
        training_days: int = 250,
        start_date: str | None = None,   # YYYY-MM-DD, 설정 시 training_days 무시
        end_date: str | None = None,      # YYYY-MM-DD, 설정 시 training_days 무시
    ):
        self._codes = codes or DEFAULT_UNIVERSE_CODES
        self._training_days = training_days
        self._start_date_str = start_date
        self._end_date_str = end_date
        self._adapter: Any = None
        self._universe_cache: list[tuple[StockInfo, list[PriceData]]] | None = None
        self._index_cache: list[PriceData] | None = None
        self._cache_date: str = ""
        # WebSocket 실시간 시세
        self._ws: Any = None
        self._ws_task: Any = None
        self._minute_builder: MinuteBarBuilder = MinuteBarBuilder()
        # P-88: 차등 갱신 (reserve 종목 30분 주기)
        self._reserve_codes: set[str] = set()
        self._reserve_refresh_counter: int = 0

    def update_date_range(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        training_days: int | None = None,
    ):
        """날짜 설정 변경 및 캐시 무효화. start/end 설정 시 training_days 무시."""
        if start_date is not None:
            self._start_date_str = start_date
        if end_date is not None:
            self._end_date_str = end_date
        if training_days is not None:
            self._training_days = training_days
        # 캐시 무효화 → 다음 load()에서 새 날짜로 fresh 로드
        self._universe_cache = None
        self._index_cache = None
        self._cache_date = ""
        logger.info(
            f"데이터 날짜 범위 변경: "
            f"start={self._start_date_str or '(N/A)'}, "
            f"end={self._end_date_str or '(N/A)'}, "
            f"training_days={self._training_days}"
        )

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

        # 학습 기간 결정: start_date/end_date 우선, 없으면 training_days
        end = datetime.now(KST)
        if self._start_date_str and self._end_date_str:
            try:
                start = datetime.strptime(self._start_date_str, "%Y-%m-%d").replace(tzinfo=KST)
                end = datetime.strptime(self._end_date_str, "%Y-%m-%d").replace(tzinfo=KST)
                if (end - start).days < 20:
                    logger.warning(f"학습 기간 부족 ({ (end-start).days }일) — 최소 20일 필요, training_days 폴백")
                    start = end - timedelta(days=self._training_days)
                    end = datetime.now(KST)
            except ValueError:
                logger.warning(f"날짜 파싱 오류: start={self._start_date_str}, end={self._end_date_str} — training_days 폴백")
                start = end - timedelta(days=self._training_days)
                end = datetime.now(KST)
        else:
            start = end - timedelta(days=self._training_days)

        period_label = f"{start.strftime('%Y-%m-%d')}~{end.strftime('%Y-%m-%d')}"
        logger.info(f"KIS 실데이터 로드 시작: 종목 {len(self._codes)}개, 기간 {period_label}")

        # 1. 먼저 KOSPI 지수 조회 (Rate Limit 버킷이 가득 찬 상태에서 1번째 호출)
        index_prices = await self._fetch_index(adapter)
        if len(index_prices) < 20:
            logger.warning(f"KOSPI 지수 부족 ({len(index_prices)}일) — 종목 로드 후 재계산")

        # 2. 종목별 일봉 데이터 (KOSPI 후 0.05s 간격으로 안전)
        universe: list[tuple[StockInfo, list[PriceData]]] = []
        for i, code in enumerate(self._codes):
            try:
                prices = await adapter.get_price_history(code, start, end)
                if prices:
                    market = _infer_market(code)
                    info = StockInfo(code=code, name=await _code_to_name(code, adapter), market=market,
                                    product_type=await _get_stock_type(code, adapter))
                    universe.append((info, prices))
            except Exception as e:
                logger.warning(f"[{code}] 데이터 로드 실패: {type(e).__name__}")

            if (i + 1) % 10 == 0:
                logger.info(f"  진행: {i + 1}/{len(self._codes)} 종목")

            # KIS rate limit: 50ms 간격 — client.py KisRateLimiter가 중앙 관리

        if not universe:
            logger.error("KIS 실데이터 로드 실패 — 모든 종목 조회 실패")
            return [], []

        # KOSPI 지수 API가 부족하면 종목군 평균으로 시장 지수 계산
        if len(index_prices) < 20:
            if universe:
                logger.warning(
                    f"KOSPI 지수 부족 ({len(index_prices)}일) — "
                    f"종목 {len(universe)}개 평균으로 시장 지수 계산"
                )
                index_prices = self._compute_market_index(universe)
            else:
                index_prices = self._synthetic_index()

        self._universe_cache = universe
        self._index_cache = index_prices
        self._cache_date = today

        logger.info(f"KIS 실데이터 로드 완료: {len(universe)}종목, 지수 {len(index_prices)}일")

        # 지수 데이터가 부족하면 시장 지수로 보강 (백테스트/모델학습 호환)
        # 단, KOSPI 지수가 20일 이상이면 실제 지수 유지 (stock avg로 대체하지 않음)
        max_stock_days = max((len(p) for _, p in universe), default=0)
        if len(index_prices) < 20 and max_stock_days > len(index_prices) and self._universe_cache:
            computed = self._compute_market_index(self._universe_cache)
            if len(computed) > len(index_prices):
                logger.info(f"KOSPI 지수 보강: {len(index_prices)}일 → {len(computed)}일 (시장 지수 계산)")
                index_prices = computed

        # Start WebSocket real-time price feed (non-blocking)
        if universe:
            self._ws_task = asyncio.create_task(self._start_websocket(universe))

        return universe, index_prices

    async def _fetch_index(self, adapter) -> list[PriceData]:
        """KOSPI 지수 일봉 데이터 조회.

        inquire-daily-indexchartprice (FHPUP02110000) 사용.
        KOSPI 지수 코드는 0001, 시장구분코드 U(업종).
        """
        # load()와 동일한 날짜 로직
        end = datetime.now(KST)
        if self._start_date_str and self._end_date_str:
            try:
                start = datetime.strptime(self._start_date_str, "%Y-%m-%d").replace(tzinfo=KST)
                end = datetime.strptime(self._end_date_str, "%Y-%m-%d").replace(tzinfo=KST)
                if (end - start).days < 20:
                    start = end - timedelta(days=self._training_days)
                    end = datetime.now(KST)
            except ValueError:
                start = end - timedelta(days=self._training_days)
        else:
            start = end - timedelta(days=self._training_days)

        try:
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
                if len(prices) >= 20:
                    return prices
                logger.warning(f"KOSPI 지수 부족 ({len(prices)}일) — 시장 지수 계산으로 대체")
            else:
                logger.warning(f"KOSPI 지수 조회 실패: {resp.msg_cd} {resp.msg1}")
        except Exception as e:
            logger.warning(f"KOSPI 지수 조회 실패: {type(e).__name__}")

        # return empty — caller decides fallback (시장 지수 계산 or 합성지수)
        return []

    def _synthetic_index(self) -> list[PriceData]:
        """KOSPI 지수 조회 실패 시 합성 지수 반환 (backtest import 없이 자체 생성)."""
        logger.warning(f"KOSPI 지수 폴백: 합성 지수 사용 ({self._training_days}일)")
        import numpy as np
        from datetime import datetime, timedelta
        from core.types import PriceData
        rng = np.random.default_rng(100)
        prices: list[PriceData] = []
        current = 2600.0
        start = datetime.now() - timedelta(days=self._training_days)
        for i in range(self._training_days):
            daily_return = rng.normal(2.0 / 2600, 0.01)
            current = current * (1 + daily_return)
            intraday_vol = current * 0.01 * 0.5
            open_p = current + rng.normal(0, intraday_vol * 0.3)
            prices.append(PriceData(
                code="INDEX", timestamp=start + timedelta(days=i),
                open=max(open_p, 1), high=current * 1.005, low=current * 0.995,
                close=current, volume=int(max(rng.normal(100000, 20000), 0)),
            ))
        return prices

    def _compute_market_index(
        self, universe: list[tuple[Any, list[PriceData]]]
    ) -> list[PriceData]:
        """종목군 평균 종가로 시장 지수 계산.

        KOSPI 지수 API가 부족할 때 종목 37개 평균으로 대체.
        실제 시장 상황을 반영하며, refresh_prices()로 업데이트 시 변동.
        """
        from collections import defaultdict

        daily_closes: dict[str, list[float]] = defaultdict(list)
        daily_volumes: dict[str, list[float]] = defaultdict(list)

        for _, prices in universe:
            for p in prices:
                k = p.timestamp.strftime("%Y%m%d")
                daily_closes[k].append(p.close)
                daily_volumes[k].append(p.volume)

        if not daily_closes:
            return self._synthetic_index()

        result: list[PriceData] = []
        for day_key in sorted(daily_closes.keys()):
            closes = daily_closes[day_key]
            avg_close = sum(closes) / len(closes)
            avg_volume = sum(daily_volumes[day_key]) / len(daily_volumes[day_key])
            dt = datetime.strptime(day_key, "%Y%m%d").replace(tzinfo=KST)
            result.append(PriceData(
                code="MARKET_INDEX",
                timestamp=dt,
                open=avg_close,
                high=avg_close,
                low=avg_close,
                close=avg_close,
                volume=int(avg_volume),
            ))

        logger.info(
            f"시장 지수 계산 완료: {len(result)}일 "
            f"({len(universe)}종목 평균)"
        )
        return result

    async def close(self):
        """Clean up adapter + WebSocket task."""
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.disconnect()
        if self._adapter:
            await self._adapter.close()

    async def _start_websocket(self, universe):
        """Start WebSocket real-time price feed (best-effort, non-critical)."""
        try:
            config = load_config()
            from data.adapters.kis.websocket_client import KisWebSocketClient

            ws = KisWebSocketClient(
                app_key=config.kis.app_key,
                app_secret=config.kis.app_secret,
                base_url=config.kis.base_url,
            )

            # Map WebSocket prices back to cache + build minute bars
            def on_price(code: str, price: float, high: float, low: float,
                         open_price: float, volume: int, ts: str):
                self._minute_builder.feed(code, price, high, low, open_price, volume, ts)
                for i, (info, prices) in enumerate(self._universe_cache or []):
                    if info.code == code and prices:
                        updated = PriceData(
                            timestamp=prices[-1].timestamp,
                            open=prices[-1].open,
                            high=max(prices[-1].high, price),
                            low=min(prices[-1].low, price),
                            close=price,
                            volume=volume,
                        )
                        prices[-1] = updated
                        break

            ws.on_price = on_price

            connected = await ws.connect()
            if not connected:
                logger.warning("WebSocket 실시간 시세 사용 불가 — REST polling 유지")
                return

            # Subscribe to all universe stock codes
            for info, _ in universe:
                await ws.subscribe(info.code)

            logger.info(f"WebSocket 실시간 시세 시작: {len(universe)}종목")
            self._ws = ws
            await ws.listen()  # runs until disconnect

        except Exception as e:
            logger.warning(f"WebSocket 실시간 시세 중단: {e} — REST polling fallback")

    async def swap_universe(self, new_codes: list[str],
                            held_codes: set[str] | None = None) -> None:
        """실시간 유니버스 교체 — WebSocket 구독 변경 + 캐시 갱신.

        Args:
            new_codes: 새 유니버스 종목코드 리스트 (최대 40).
            held_codes: 보유 포지션 코드. 이 종목들은 절대 제외되지 않음.
        """
        held = held_codes or set()
        old_codes = set(self._codes) if isinstance(self._codes, list) else set()
        new_set = set(new_codes)
        add_codes = list(new_set - old_codes)
        remove_codes = [c for c in (old_codes - new_set) if c not in held]

        if not add_codes and not remove_codes:
            logger.info("유니버스 교체 불필요 (변동 없음)")
            return

        # 1. WebSocket 구독 교체
        if self._ws:
            await self._ws.swap_universe(add_codes, remove_codes)

        # 2. 새 종목 일봉 데이터 로드
        if add_codes and self._universe_cache:
            adapter = await self._get_adapter()
            for code in add_codes:
                try:
                    prices = await adapter.get_daily_chart(code)
                    if prices:
                        market = _infer_market(code)
                        self._universe_cache.append(
                            (StockInfo(code=code, name="", market=market), prices)
                        )
                except Exception as e:
                    logger.warning(f"[{code}] 신규 편입 일봉 로드 실패: {e}")

        # 3. 제거 종목 캐시 정리
        if remove_codes and self._universe_cache:
            self._universe_cache = [
                (info, prices) for info, prices in self._universe_cache
                if info.code not in remove_codes
            ]

        # 4. MinuteBarBuilder 정리
        for code in remove_codes:
            self._minute_builder.clear(code)

        self._codes = list(new_set)
        logger.info(f"유니버스 교체: {len(old_codes)}→{len(new_set)}종목 "
                    f"(+{len(add_codes)}, -{len(remove_codes)})")

    async def refresh_prices(self):
        """실시간 시세 갱신 — 최근 5일치만 조회해 캐시된 데이터 업데이트.

        load()로 전체 데이터를 로드한 후, 매 사이클마다 이 메서드를 호출하면
        최신 일봉 데이터로 캐시가 갱신된다.

        Returns:
            Updated (universe, index_prices).
        """
        if self._universe_cache is None:
            return await self.load()

        adapter = await self._get_adapter()
        now = datetime.now(KST)
        # Refresh with full date range to get enough KOSPI bars (≥20)
        start = now - timedelta(days=max(self._training_days, 30))

        # Refresh index first (Rate Limit 버킷 Full 상태에서 1번째 호출)
        new_index = await self._fetch_index(adapter)
        if not new_index or len(new_index) < 20:
            if self._universe_cache:
                new_index = self._compute_market_index(self._universe_cache)
            else:
                new_index = self._synthetic_index()

        # Refresh each stock's latest bar (P-88: 차등 갱신)
        reserve_skip = False
        if self._reserve_codes:
            self._reserve_refresh_counter += 1
            reserve_skip = self._reserve_refresh_counter % 3 != 0  # 3번 중 2번 skip
        for i, (info, prices) in enumerate(self._universe_cache):
            # P-88: reserve 종목은 30분마다만 갱신
            if reserve_skip and info.code in self._reserve_codes:
                continue
            try:
                new_bars = await adapter.get_price_history(info.code, start, now)
                if new_bars:
                    latest = new_bars[-1]
                    if prices and prices[-1].timestamp.date() == latest.timestamp.date():
                        # 같은 거래일 — 마지막 bar 업데이트
                        prices[-1] = latest
                    elif not prices or latest.timestamp > prices[-1].timestamp:
                        # 새 거래일 — 추가
                        prices.append(latest)
            except Exception as e:
                logger.debug(f"[{info.code}] 시세 갱신 실패: {type(e).__name__}")

            if (i + 1) % 10 == 0:
                logger.debug(f"  시세 갱신 진행: {i + 1}/{len(self._universe_cache)}")

        if new_index:
            # Update last bar or append
            if self._index_cache and self._index_cache[-1].timestamp.date() == new_index[-1].timestamp.date():
                self._index_cache[-1] = new_index[-1]
            else:
                if self._index_cache is None:
                    self._index_cache = list(new_index)
                elif new_index[-1].timestamp > self._index_cache[-1].timestamp:
                    self._index_cache.append(new_index[-1])

        logger.debug(f"시세 갱신 완료: {len(self._universe_cache)}종목")
        return self._universe_cache, self._index_cache

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


# ── 종목코드→종목명 캐시 (KIS API 기반, TTL 1일) ──
_name_cache: dict[str, tuple[str, float]] = {}
_type_cache: dict[str, tuple[str, float]] = {}  # code → (product_type, expiry)
_NAME_CACHE_TTL = 86400  # 1일


async def _code_to_name(code: str, adapter: Any = None) -> str:
    """종목코드 → 종목명 (KIS API + 캐시 + 하드코딩 fallback).

    캐시 TTL 1일. API 실패 시 하드코딩 딕셔너리로 fallback.
    """
    now = time.time()
    # 1. 캐시 조회
    if code in _name_cache:
        name, expiry = _name_cache[code]
        if now < expiry:
            return name
    # 2. 하드코딩 딕셔너리
    _STATIC_NAMES = {
        "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션",
        "207940": "삼성바이오로직스", "005380": "현대차", "000270": "기아",
        "068270": "셀트리온", "105560": "KB금융", "055550": "신한지주",
        "035420": "NAVER", "000810": "삼성화재", "012330": "현대모비스",
        "006400": "삼성SDI", "028260": "삼성물산", "032830": "삼성생명",
        "086790": "하나금융지주", "003550": "LG", "066570": "LG전자",
        "015760": "한국전력", "017670": "SK텔레콤", "329180": "HD현대중공업",
        "138040": "메리츠금융지주", "096770": "SK이노베이션", "018260": "삼성에스디에스",
        "034730": "SK", "323410": "카카오뱅크", "259960": "크래프톤",
        "352820": "하이브",
        "247540": "에코프로비엠", "196170": "알테오젠",
        "028300": "HLB", "086520": "에코프로", "058470": "리노공업",
        "214150": "클래시스", "035900": "JYP Ent.", "403870": "HPSP",
        "068760": "셀트리온제약", "263750": "펄어비스", "257720": "실리콘투",
        "240810": "원익IPS",
        # 동적 유니버스 자주 편입되는 종목
        "189400": "건설기계", "429010": "클럽디",
    }
    if code in _STATIC_NAMES:
        _name_cache[code] = (_STATIC_NAMES[code], now + _NAME_CACHE_TTL)
        _type_cache[code] = ("stock", now + _NAME_CACHE_TTL)
        return _STATIC_NAMES[code]
    # 3. KIS API 호출 — 이름 + 분류 동시 획득
    if adapter is not None and hasattr(adapter, 'get_stock_info'):
        try:
            from data.adapters.kis.mapper import parse_stock_info
            # 직접 API 호출 (adapter._http를 통해)하여 이름과 분류 동시 획득
            if hasattr(adapter, '_http'):
                resp = await adapter._http.get("inquire_stock_basic", params={"PDNO": code, "PRDT_TYPE_CD": "300"})
                if resp.success and resp.data:
                    info = parse_stock_info(resp.data)
                    if info.name:
                        _name_cache[code] = (info.name, now + _NAME_CACHE_TTL)
                        _type_cache[code] = (info.product_type or "stock", now + _NAME_CACHE_TTL)
                        return info.name
        except Exception:
            pass
    return f"종목{code}"


async def _get_stock_type(code: str, adapter: Any = None) -> str:
    """종목코드 → 상품유형 (stock/etf/etn).

    _code_to_name과 동일한 캐시 사용. 이미 _code_to_name에서
    API를 호출했다면 _type_cache가 채워져 있음.
    """
    now = time.time()
    if code in _type_cache:
        ptype, expiry = _type_cache[code]
        if now < expiry:
            return ptype
    # _name_cache에 있으면 _type_cache도 있어야 함 (동시 저장)
    if code in _name_cache:
        return "stock"  # fallback: 기본값
    # API 호출 (이름도 같이 캐싱)
    if adapter is not None and hasattr(adapter, 'get_stock_info'):
        try:
            if hasattr(adapter, '_http'):
                resp = await adapter._http.get("inquire_stock_basic", params={"PDNO": code, "PRDT_TYPE_CD": "300"})
                if resp.success and resp.data:
                    from data.adapters.kis.mapper import parse_stock_info
                    info = parse_stock_info(resp.data)
                    ptype = info.product_type or "stock"
                    _type_cache[code] = (ptype, now + _NAME_CACHE_TTL)
                    if info.name and code not in _name_cache:
                        _name_cache[code] = (info.name, now + _NAME_CACHE_TTL)
                    return ptype
        except Exception:
            pass
    return "stock"  # 기본값: 일반 주식으로 간주
