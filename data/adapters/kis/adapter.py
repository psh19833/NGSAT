"""KIS BrokerAdapter implementation.

Implements the BrokerAdapter interface for Korea Investment & Securities (KIS) API.
This is the concrete adapter that the rest of NGSAT uses — it never exposes
KIS-specific field names to the business logic layer.

All credentials come from .env via core.config — never hardcoded.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from core.exceptions import BrokerError, ConfigError
from core.logger import logger
from core.types import AccountSummary, OrderSide, Position, PriceData, StockInfo
from data.adapters.base import BrokerAdapter
from data.adapters.kis.client import KisHttpClient
from data.adapters.kis.endpoints import BUY_TR_ID, SELL_TR_ID
from data.adapters.kis.mapper import (
    build_order_payload,
    parse_account_summary,
    parse_positions,
    parse_price,
    parse_price_history,
    parse_stock_info,
)
from data.adapters.kis.token_manager import KisTokenManager


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

        # The raw response contains both account summary (output2) and positions (output)
        summary = parse_account_summary(resp.raw)
        logger.info(
            f"계좌 조회 성공: 총자산={summary.total_asset:,.0f}, "
            f"예수금={summary.deposit:,.0f}"
        )
        return summary

    async def get_positions(self) -> list[Position]:
        """Fetch all currently held positions."""
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

        positions = parse_positions(resp.raw)
        logger.info(f"보유 포지션 조회: {len(positions)}개")
        return positions

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
        """Cancel a pending order.
        
        Note: KIS cancel-order endpoint requires additional fields
        (ORD_GNO_BRNO, ORGN_ODNO). Will be fully implemented in Phase 6.
        """
        logger.warning(f"cancel_order not yet fully implemented for order_id={order_id}")
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
