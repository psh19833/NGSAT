"""KIS API endpoint catalog.

All KIS API paths and TR IDs in one place.
Prevents hardcoded URL strings scattered across the codebase.

Reference: https://api.koreainvestment.com (KIS Open API docs)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class KisCategory(str, Enum):
    """KIS API category."""
    OAUTH = "oauth"
    TRADING = "trading"          # 주문/계좌
    QUOTATION = "quotation"      # 시세
    STOCK_INFO = "stock_info"    # 종목정보
    MARKET_SCHEDULE = "market_schedule"  # 장운영


@dataclass(frozen=True)
class KisEndpoint:
    """KIS API endpoint definition."""
    name: str
    category: KisCategory
    path: str
    method: Literal["GET", "POST"]
    tr_id: str | None = None
    is_order: bool = False       # 주문 endpoint (실행 시 별도 가드 필요)
    description: str = ""


# ── OAuth ──
_TOKEN_ISSUE = KisEndpoint(
    name="token_issue",
    category=KisCategory.OAUTH,
    path="/oauth2/tokenP",
    method="POST",
    tr_id=None,
    description="Access Token 발급",
)

# ── 주문/계좌 (Trading) ──
_BALANCE = KisEndpoint(
    name="inquire_balance",
    category=KisCategory.TRADING,
    path="/uapi/domestic-stock/v1/trading/inquire-balance",
    method="GET",
    tr_id="TTTC8434R",
    description="주식 잔고 조회",
)

_DAILY_FILLS = KisEndpoint(
    name="inquire_daily_ccld",
    category=KisCategory.TRADING,
    path="/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
    method="GET",
    tr_id="TTTC8001R",
    description="당일 체결 조회",
)

_ORDER_CASH = KisEndpoint(
    name="order_cash",
    category=KisCategory.TRADING,
    path="/uapi/domestic-stock/v1/trading/order-cash",
    method="POST",
    tr_id=None,  # BUY/SELL에 따라 동적 할당
    is_order=True,
    description="현금 주문 (매수/매도)",
)

_ORDER_INQUIRY = KisEndpoint(
    name="inquire_order",
    category=KisCategory.TRADING,
    path="/uapi/domestic-stock/v1/trading/inquire-order",
    method="GET",
    tr_id="TTTC8036R",
    description="주식 주문 조회 (ODNO로 단일 주문 상태 확인)",
)

# ── 시세 (Quotation) ──
_CURRENT_PRICE = KisEndpoint(
    name="inquire_price",
    category=KisCategory.QUOTATION,
    path="/uapi/domestic-stock/v1/quotations/inquire-price",
    method="GET",
    tr_id="FHKST01010100",
    description="주식 현재가 조회",
)

_DAILY_CHART = KisEndpoint(
    name="inquire_daily_chart",
    category=KisCategory.QUOTATION,
    path="/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
    method="GET",
    tr_id="FHKST03010100",
    description="주식 일봉 차트 조회",
)

_MINUTE_CHART = KisEndpoint(
    name="inquire_time_chart",
    category=KisCategory.QUOTATION,
    path="/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
    method="GET",
    tr_id="FHKST03010200",
    description="주식 당일 분봉 차트 조회",
)

_ORDERBOOK = KisEndpoint(
    name="inquire_asking_price",
    category=KisCategory.QUOTATION,
    path="/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
    method="GET",
    tr_id="FHKST01010200",
    description="주식 호가/10호가 조회",
)

# ── 지수 (Index) ──
_INDEX_DAILY = KisEndpoint(
    name="inquire_index_daily",
    category=KisCategory.QUOTATION,
    path="/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
    method="GET",
    tr_id="FHPUP02110000",
    description="업종 일자별 지수 (KOSPI/KOSDAQ 일봉)",
)

# ── 종목정보 (Stock Info) ──
_STOCK_BASIC = KisEndpoint(
    name="inquire_stock_basic",
    category=KisCategory.STOCK_INFO,
    path="/uapi/domestic-stock/v1/quotations/search-stock-info",
    method="GET",
    tr_id="CTPF1604R",
    description="주식 기본 정보 조회",
)

# ── 장운영 (Market Schedule) ──
_MARKET_HOLIDAY = KisEndpoint(
    name="inquire_holiday",
    category=KisCategory.MARKET_SCHEDULE,
    path="/uapi/domestic-stock/v1/trading/inquire-holiday",
    method="GET",
    tr_id="CTCA0903R",
    description="휴장일 조회",
)

_MARKET_HOURS = KisEndpoint(
    name="inquire_market_hours",
    category=KisCategory.MARKET_SCHEDULE,
    path="/uapi/domestic-stock/v1/quotations/inquire-market-hours",
    method="GET",
    tr_id="FHKST01010900",
    description="장운영 시간 조회",
)

# ── 수급 (외국인/기관) ──
_FOREIGN_INVESTOR = KisEndpoint(
    name="inquire_foreign_investor",
    category=KisCategory.QUOTATION,
    path="/uapi/domestic-stock/v1/quotations/inquire-foreign-investor",
    method="GET",
    tr_id="FHKST01011000",
    description="외국인 순매수 조회",
)

# ── Order TR IDs (매수/매도 구분) ──
BUY_TR_ID = "TTTC0802U"
SELL_TR_ID = "TTTC0801U"

# ── Endpoint registry ──
_ENDPOINTS: dict[str, KisEndpoint] = {
    ep.name: ep
    for ep in [
        _TOKEN_ISSUE,
        _BALANCE, _DAILY_FILLS, _ORDER_CASH, _ORDER_INQUIRY,
        _CURRENT_PRICE, _DAILY_CHART, _MINUTE_CHART, _ORDERBOOK,
        _INDEX_DAILY,
        _STOCK_BASIC,
        _MARKET_HOLIDAY, _MARKET_HOURS,
        _FOREIGN_INVESTOR,
    ]
}


def get_endpoint(name: str) -> KisEndpoint:
    """Look up an endpoint by name.

    Raises:
        KeyError: If endpoint name is not found.
    """
    if name not in _ENDPOINTS:
        raise KeyError(f"KIS endpoint not found: {name!r}. Available: {list(_ENDPOINTS.keys())}")
    return _ENDPOINTS[name]


def is_order_endpoint(name: str) -> bool:
    """Check if an endpoint is an order (trading) endpoint."""
    ep = _ENDPOINTS.get(name)
    return ep is not None and ep.is_order
