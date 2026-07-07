"""KIS BrokerAdapter implementation.

Implements the BrokerAdapter interface for Korea Investment & Securities (KIS) API.
This is the concrete adapter that the rest of NGSAT uses — it never exposes
KIS-specific field names to the business logic layer.

All credentials come from .env via core.config — never hardcoded.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime
from typing import Any

from core.exceptions import BrokerError, ConfigError
from core.logger import logger
from core.types import AccountSummary, OrderSide, OrderStatus, Position, PriceData, StockInfo
from data.adapters.base import BrokerAdapter
from data.adapters.kis.client import KisHttpClient
from data.adapters.kis.endpoints import BUY_TR_ID, SELL_TR_ID
from data.adapters.kis.mapper import (
    build_order_payload,
    parse_account_summary,
    parse_minute_history,
    parse_order_status,
    parse_positions,
    parse_price,
    parse_price_history,
)
from data.adapters.kis.token_manager import KisTokenManager


_BALANCE_CACHE_TTL = 10.0  # seconds — prevent duplicate inquire_balance calls (KIS rate limit: ~20 req/s)


class KisAdapter(BrokerAdapter):
    """KIS (Korea Investment & Securities) broker adapter.

    Implements BrokerAdapter for the KIS Open API.
    All secrets are loaded from .env — never logged or hardcoded.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        base_url: str,
        account_no: str,
        account_product_code: str,
    ):
        if not app_key or not app_secret:
            raise ConfigError("KIS app_key and app_secret are required")

        self._account_no = self._normalize_account_no(account_no)
        self._account_product_code = account_product_code or "01"

        self._token_manager = KisTokenManager(
            app_key=app_key,
            app_secret=app_secret,
            base_url=base_url,
        )
        self._http = KisHttpClient(
            app_key=app_key,
            app_secret=app_secret,
            base_url=base_url,
            token_manager=self._token_manager,
        )
        self._balance_cache: dict[str, tuple[float, Any]] = {}
        self._balance_raw_cache: dict[str, tuple[float, dict]] = {}
        self._balance_lock = asyncio.Lock()

    @staticmethod
    def _normalize_account_no(account_no: str) -> str:
        """Extract 8-digit CANO from account number.

        Accepts formats:
        - "12345678" (8 digits)
        - "12345678-01" (with product code)
        """
        raw = (account_no or "").strip()
        if "-" in raw:
            raw = raw.split("-")[0]
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) != 8:
            raise ConfigError(f"KIS account_no must be 8 digits, got: {len(digits)}")
        return digits

    async def _cached_balance(self, key: str, fetcher):
        """TTL 캐시로 inquire_balance 중복 호출 방지. asyncio.Lock 직렬화."""
        async with self._balance_lock:
            now = time.monotonic()
            if key in self._balance_cache:
                ts, data = self._balance_cache[key]
                if now - ts < _BALANCE_CACHE_TTL:
                    return data
            data = await fetcher()
            self._balance_cache[key] = (now, data)
            return data

    @classmethod
    def from_env(cls) -> "KisAdapter":
        """Create adapter from environment variables (.env).

        Required env vars:
        - KIS_BASE_URL
        - KIS_APP_KEY
        - KIS_APP_SECRET
        - KIS_ACCOUNT_NO
        - KIS_ACNT_PRDT_CD (optional, default "01")
        """
        return cls(
            app_key=os.getenv("KIS_APP_KEY", ""),
            app_secret=os.getenv("KIS_APP_SECRET", ""),
            base_url=os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
            account_no=os.getenv("KIS_ACCOUNT_NO", ""),
            account_product_code=os.getenv("KIS_ACNT_PRDT_CD", "01"),
        )

    async def get_account_summary(self) -> AccountSummary:
        """Fetch current account balance and position summary."""
        async def _fetch():
            params = {
                "CANO": self._account_no,
                "ACNT_PRDT_CD": self._account_product_code,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            }

            resp = await self._http.get("inquire_balance", params=params)

            if not resp.success:
                raise BrokerError(f"KIS balance query failed: {resp.msg_cd} {resp.msg1}")

            # Cache raw response for get_positions() to reuse (rate limit 방지)
            self._balance_raw_cache["summary"] = (time.monotonic(), resp.raw)

            # The raw response contains both account summary (output2) and positions (output)
            summary = parse_account_summary(resp.raw)
            logger.info(
                f"계좌 조회 성공: 총자산={summary.total_asset:,.0f}, "
                f"예수금={summary.deposit:,.0f}"
            )
            return summary

        return await self._cached_balance("summary", _fetch)

    async def get_positions(self) -> list[Position]:
        """Fetch all currently held positions.

        Delegates to get_account_summary() for the API call (1회만 호출),
        then parses positions from the cached raw response.
        NEVER makes its own inquire_balance call — KIS rate limit 방지.
        """
        # get_account_summary()를 먼저 호출하여 raw 응답 캐싱 보장
        # (이미 캐시되어 있으면 즉시 반환, 없으면 1회 API 호출)
        await self.get_account_summary()

        # 캐시된 raw 응답에서 포지션 파싱 — API 호출 0회
        now = time.monotonic()
        if "summary" in self._balance_raw_cache:
            ts, raw = self._balance_raw_cache["summary"]
            if now - ts < _BALANCE_CACHE_TTL:
                positions = parse_positions(raw)
                logger.debug(f"보유 포지션 조회(캐시): {len(positions)}개")
                return positions

        # Fallback 제거 — 이 경로에 도달하는 것은 비정상 상태
        # (get_account_summary()가 정상 종료되었다면 _balance_raw_cache는 항상 존재)
        logger.error("get_positions: _balance_raw_cache 누락 — 비정상 상태")
        return []

    async def get_price(self, code: str) -> PriceData:
        """Fetch real-time price for a single stock."""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        }

        resp = await self._http.get("inquire_price", params=params)

        if not resp.success:
            raise BrokerError(f"KIS price query failed for {code}: {resp.msg_cd} {resp.msg1}")

        price = parse_price(resp.data, code)
        logger.debug(f"시세 조회: {code} 현재가={price.close:,.0f}")
        return price

    async def get_index_price(self, code: str = "0001") -> PriceData | None:
        """Fetch current KOSPI/KOSDAQ index price (장중 레짐 보정용).

        Args:
            code: Index code. "0001" = KOSPI, "1001" = KOSDAQ.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": code,
        }
        try:
            resp = await self._http.get("inquire_index_price", params=params)
            if not resp.success:
                logger.warning(f"지수({code}) 현재가 조회 실패: {resp.msg_cd} {resp.msg1}")
                return None
            name = "KOSPI" if code == "0001" else "KOSDAQ"
            price = parse_price(resp.data, name)
            return price
        except Exception as e:
            logger.warning(f"지수({code}) 현재가 조회 예외: {type(e).__name__}: {e}")
            return None

    async def get_price_history(
        self, code: str, start: datetime, end: datetime
    ) -> list[PriceData]:
        """Fetch historical daily price data."""
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "1",
        }

        resp = await self._http.get("inquire_daily_chart", params=params)

        if not resp.success:
            raise BrokerError(
                f"KIS chart query failed for {code}: {resp.msg_cd} {resp.msg1}"
            )

        history = parse_price_history(resp.raw, code)
        logger.info(f"일봉 조회: {code} {len(history)}개")
        return history

    async def get_volume_rank(self) -> list[dict]:
        """KIS 거래량순위 API — 실시간 거래량 상위 종목.

        Returns:
            [{"code": "005930", "name": "삼성전자", "volume": 12345678}, ...]
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0",
            "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "0000001100",  # 7:ETF, 8:ETN 제외
            "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "1000000",
            "FID_VOL_CNT": "100000",
        }
        resp = await self._http.get("volume_rank", params=params)
        if not resp.success:
            logger.warning(f"거래량순위 조회 실패: {resp.msg_cd} {resp.msg1}")
            return []
        output = resp.raw.get("output", [])
        if not isinstance(output, list):
            return []
        result = []
        for item in output:
            code = item.get("mksc_shrn_iscd", "") or item.get("stck_shrn_iscd", "")
            if not code:
                continue
            result.append({
                "code": code,
                "name": item.get("hts_kor_isnm", ""),
                "volume": int(item.get("acml_vol", 0) or 0),
                "price": int(item.get("stck_prpr", 0) or 0),
                "change_pct": float(item.get("prdy_ctrt", 0) or 0),
            })
        return result

    async def get_volume_power(self) -> list[dict]:
        """체결강도 상위 — 실시간 매수압력 순위.

        Returns:
            [{"code": "005930", "name": "삼성전자", "score": 100}, ...]
        """
        params = {
            "FID_TRGT_EXLS_CLS_CODE": "0000001100",  # 7:ETF, 8:ETN 제외
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20168",
            "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "0",
            "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "",
            "FID_TRGT_CLS_CODE": "0",
        }
        resp = await self._http.get("volume_power", params=params)
        if not resp.success:
            return []
        output = resp.raw.get("output", [])
        if not isinstance(output, list):
            return []
        result = []
        for item in output:
            code = item.get("stck_shrn_iscd", "")
            if not code:
                continue
            result.append({
                "code": code,
                "name": item.get("hts_kor_isnm", ""),
                "score": int(item.get("acml_vol", 0) or 0),
            })
        return result

    async def get_fluctuation_rank(self, top_n: int = 100) -> list[dict]:
        """등락률 순위 — 상승률 상위 N종목.

        Args:
            top_n: 조회할 종목 수 (기본 100).

        Returns:
            [{"code": "005930", "name": "삼성전자", "change_pct": 2.5}, ...]
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_COND_SCR_DIV_CODE": "20170",
            "FID_INPUT_ISCD": "0000",
            "FID_RANK_SORT_CLS_CODE": "0000",
            "FID_INPUT_CNT_1": str(top_n),
            "FID_PRC_CLS_CODE": "0",
            "FID_INPUT_PRICE_1": "0",
            "FID_INPUT_PRICE_2": "1000000",
            "FID_VOL_CNT": "100000",
            "FID_TRGT_CLS_CODE": "0",
            "FID_TRGT_EXLS_CLS_CODE": "0000001100",  # 7:ETF, 8:ETN 제외
            "FID_DIV_CLS_CODE": "0",
            "FID_RSFL_RATE1": "0",
            "FID_RSFL_RATE2": "30",
        }
        resp = await self._http.get("fluctuation", params=params)
        if not resp.success:
            return []
        output = resp.raw.get("output", [])
        if not isinstance(output, list):
            return []
        result = []
        for item in output:
            code = item.get("stck_shrn_iscd", "")
            if not code:
                continue
            result.append({
                "code": code,
                "name": item.get("hts_kor_isnm", ""),
                "change_pct": float(item.get("prdy_ctrt", 0) or 0),
            })
        return result

    async def get_minute_history(
        self,
        code: str,
        base_time: datetime | None = None,
        include_past: bool = True,
    ) -> list[PriceData]:
        """Fetch intraday minute-bar price data for the current trading day.

        Args:
            code: 6-digit stock code.
            base_time: Reference time (only HH:MM:SS is used); None = now.
            include_past: Whether to include earlier bars of the same day.

        Returns:
            List of PriceData minute bars. KIS returns up to ~30 bars ending
            at base_time; call again with an earlier base_time to page further
            back within the same trading day.
        """
        hour_str = (base_time or datetime.now()).strftime("%H%M%S")
        params = {
            "FID_ETC_CLS_CODE": "",
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": hour_str,
            "FID_PW_DATA_INCU_YN": "Y" if include_past else "N",
        }

        resp = await self._http.get("inquire_time_chart", params=params)

        if not resp.success:
            raise BrokerError(
                f"KIS minute-chart query failed for {code}: {resp.msg_cd} {resp.msg1}"
            )

        history = parse_minute_history(resp.raw, code)
        logger.info(f"분봉 조회: {code} {len(history)}개")
        return history

    async def get_stock_list(self) -> list[StockInfo]:
        """Fetch all tradeable stocks.

        Note: KIS doesn't have a single "list all stocks" endpoint.
        This method would typically use a cached stock list or
        the volume-rank endpoint for active stocks.
        For now, returns empty list — will be implemented with
        a stock universe cache in Phase 3.
        """
        logger.warning("get_stock_list not yet implemented — will use stock universe cache")
        return []

    async def get_stock_info(self, code: str) -> str:
        """종목코드로 종목명 조회 (inquire_stock_basic CTPF1604R).

        Returns:
            종목명 (실패 시 빈 문자열).
        """
        # KIS search-stock-info expects PDNO + PRDT_TYPE_CD
        params = {
            "PDNO": code,
            "PRDT_TYPE_CD": "300",
        }
        try:
            resp = await self._http.get("inquire_stock_basic", params=params)
            if resp.success and resp.data:
                from data.adapters.kis.mapper import parse_stock_info
                info = parse_stock_info(resp.data)
                if info.name:
                    # API 응답명에서 접미사 제거 (예: 삼성전자보통주 → 삼성전자)
                    name = info.name
                    for suffix in ("보통주", "우선주", "보통주", "주식"):
                        if name.endswith(suffix) and len(name) > len(suffix):
                            name = name[:-len(suffix)]
                            break
                    return name
        except Exception as e:
            logger.debug(f"[{code}] 종목정보 조회 실패: {type(e).__name__}")
        return ""

    async def submit_order(
        self,
        code: str,
        side: OrderSide,
        quantity: int,
        price: float | None = None,
    ) -> str:
        """Submit a buy or sell order.

        Args:
            code: 6-digit stock code
            side: BUY or SELL
            quantity: Number of shares
            price: Limit price (None = market order)

        Returns:
            Order ID from KIS.

        Raises:
            BrokerError: If order submission fails.
        """
        payload = build_order_payload(
            code=code,
            side=side,
            quantity=quantity,
            account_no=self._account_no,
            account_product_code=self._account_product_code,
            price=price,
        )

        # Determine TR_ID based on buy/sell
        tr_id = BUY_TR_ID if side == OrderSide.BUY else SELL_TR_ID
        extra_headers = {"tr_id": tr_id}

        logger.info(f"주문 제출: {side.value} {code} {quantity}주")

        resp = await self._http.post(
            "order_cash",
            json_data=payload,
            extra_headers=extra_headers,
        )

        if not resp.success:
            logger.error(f"주문 거절: {code} {resp.msg_cd} {resp.msg1}")
            raise BrokerError(f"KIS order rejected: {resp.msg_cd} {resp.msg1}")

        # KIS returns order number in output
        order_id = str(resp.data.get("ODNO") or resp.data.get("odno") or "")
        logger.info(f"주문 접수: {code} 주문번호={order_id}")
        return order_id

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order via KIS order-cancel endpoint.

        Args:
            order_id: KIS order number (ODNO) to cancel.

        Returns:
            True if cancellation succeeded.

        Raises:
            BrokerError: If cancellation fails.
        """
        from datetime import datetime

        now = datetime.now()
        payload = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._account_product_code,
            "KRX_FWDG_ORD_ORGNO": "",  # KRX 주문 조직번호 (미입력 시 자동)
            "ORGN_ODNO": order_id,      # 원주문번호
            "ORD_DVSN": "00",           # 00=지정가
            "QTY_ALL_ORD_YN": "Y",      # 잔량전체
        }
        extra_headers = {"tr_id": "TTTC0803U"}

        try:
            resp = await self._http.post(
                "order_cancel",
                json_data=payload,
                extra_headers=extra_headers,
            )
            if resp.success:
                logger.info(f"주문 취소 성공: {order_id}")
                return True
            else:
                logger.warning(f"주문 취소 실패: {order_id} — {resp.msg_cd} {resp.msg1}")
                return False
        except Exception as e:
            logger.error(f"주문 취소 중 오류: {order_id} — {e}")
            return False

    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Check current status of a submitted order via KIS inquire-order.

        Args:
            order_id: KIS order number (ODNO) to check.

        Returns:
            OrderStatus enum.

        Raises:
            BrokerError: If inquiry fails.
        """
        from datetime import datetime

        now = datetime.now()
        params = {
            "CANO": self._account_no,
            "ACNT_PRDT_CD": self._account_product_code,
            "ORD_STR_DT": now.strftime("%Y%m%d"),
            "ORD_GNO_BRNO": "",
            "ODNO": order_id,
            "CCLD_NCCS_DVSN": "00",
            "SLL_BUY_DVSN_CD": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }

        resp = await self._http.get("inquire_order", params=params)

        if not resp.success:
            raise BrokerError(
                f"KIS order status inquiry failed for {order_id}: "
                f"{resp.msg_cd} {resp.msg1}"
            )

        status = parse_order_status(resp.raw, order_id)
        logger.info(f"주문 상태 조회: {order_id} → {status.value}")
        return status

    async def get_fill_price(self, order_id: str) -> float:
        """P-53: 실제 체결가 조회 — KIS inquire_order 응답에서 ccld_prpr 추출.

        Args:
            order_id: KIS order number (ODNO).

        Returns:
            Actual fill price, or 0.0 if not filled / inquiry fails.
        """
        try:
            resp = await self._http.get("inquire_order", params={
                "CANO": self._account_no,
                "ACNT_PRDT_CD": self._account_product_code,
                "ORD_STR_DT": datetime.now().strftime("%Y%m%d"),
                "ORD_GNO_BRNO": "",
                "ODNO": order_id,
                "CCLD_NCCS_DVSN": "00",
                "SLL_BUY_DVSN_CD": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            })
            if not resp.success:
                logger.warning(f"체결가 조회 실패 ({order_id}): {resp.msg_cd}")
                return 0.0
            output = resp.raw.get("output") or resp.raw.get("output2") or {}
            if isinstance(output, list):
                output = output[0] if output else {}
            ccld_prpr = float(output.get("ccld_prpr") or output.get("ftrd_ccld_prpr", 0))
            if ccld_prpr > 0:
                logger.info(f"실제 체결가 조회: {order_id} → {ccld_prpr:,.0f}원")
            return ccld_prpr
        except Exception as e:
            logger.warning(f"체결가 조회 중 오류 ({order_id}): {e}")
            return 0.0

    async def get_vi_status(self, code: str) -> bool:
        """Check VI (Volatility Interruption) status for a stock.

        Uses the 호가(orderbook) endpoint which returns VI_YN field.
        VI means the stock price moved too fast — orders may be restricted.

        Args:
            code: 6-digit stock code.

        Returns:
            True if VI is currently active.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        }
        try:
            resp = await self._http.get("inquire_asking_price", params=params)
            if not resp.success:
                logger.warning(f"VI 조회 실패 ({code}): {resp.msg_cd} — VI 미발동으로 간주")
                return False
            # KIS returns VI_YN = "Y" when VI is active
            vi_yn = (resp.data or {}).get("VI_YN", "N")
            return vi_yn == "Y"
        except Exception as e:
            logger.warning(f"VI 조회 중 오류 ({code}): {e} — VI 미발동으로 간주")
            return False

    async def is_market_open(self) -> bool:
        """Check if the stock market is currently open.

        Uses KIS market-hours endpoint. Falls back to time-based check
        if API is unavailable.
        """
        # Quick time-based check (KST: 09:00-15:30)
        now = datetime.now()
        weekday = now.weekday()

        if weekday >= 5:  # Saturday=5, Sunday=6
            return False

        hour = now.hour
        minute = now.minute

        # 09:00 ~ 15:30
        if hour < 9 or hour > 15:
            return False
        if hour == 15 and minute > 30:
            return False

        # TODO: Query KIS holiday endpoint for accuracy
        return True

    async def close(self) -> None:
        """Clean up resources."""
        await self._http.close()

    async def get_unfilled_orders(self) -> list:
        """Get currently unfilled orders from KIS."""
        try:
            resp = await self._http.get("inquire_unfilled", params={
                "CANO": self._account_no,
                "ACNT_PRDT_CD": self._account_product_code,
                "INQR_STRT_DT": datetime.now().strftime("%Y%m%d"),
                "INQR_END_DT": datetime.now().strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",
                "CCLD_DVSN": "02",
                "INQR_DVSN": "00",
                "INQR_DVSN_3": "00",
            })
            if resp.success:
                from data.adapters.kis.mapper import parse_unfilled_orders
                return parse_unfilled_orders(resp.raw)
        except Exception as e:
            logger.warning(f"미체결 주문 조회 실패: {e}")
        return []

    # ── 외국인/기관/재무 데이터 (P1-3) ──────────────────────────────────

    async def get_investor_data(self, code: str) -> dict[str, Any]:
        """종목별 외국인/기관 투자자 매매동향 조회.

        Args:
            code: 6-digit stock code.

        Returns:
            dict: foreign_net_buy_qty, foreign_net_buy_amt,
                  institution_net_buy_qty, institution_net_buy_amt
                  (API 실패시 0값)
        """
        try:
            resp = await self._http.get("inquire_investor", params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
            })
            await asyncio.sleep(0.1)  # Rate limit
            if resp.success:
                from data.adapters.kis.mapper import parse_investor_data
                return parse_investor_data(resp.raw)
        except Exception as e:
            logger.warning(f"투자자 매매동향 조회 실패 ({code}): {e}")
        return {
            "foreign_net_buy_qty": 0.0,
            "foreign_net_buy_amt": 0.0,
            "institution_net_buy_qty": 0.0,
            "institution_net_buy_amt": 0.0,
        }

    async def get_financial_ratio(self, code: str) -> dict[str, float]:
        """종목별 재무비율 조회 (PER/PBR/EPS).

        Args:
            code: 6-digit stock code.

        Returns:
            dict: per, pbr, eps (API 실패시 0값)
        """
        try:
            resp = await self._http.get("inquire_financial_ratio", params={
                "FID_DIV_CLS_CODE": "1",  # 0: 년, 1: 분기
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
            })
            await asyncio.sleep(0.1)  # Rate limit
            if resp.success:
                from data.adapters.kis.mapper import parse_financial_ratio
                return parse_financial_ratio(resp.raw)
        except Exception as e:
            logger.warning(f"재무비율 조회 실패 ({code}): {e}")
        return {"per": 0.0, "pbr": 0.0, "eps": 0.0}
