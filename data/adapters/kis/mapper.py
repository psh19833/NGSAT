"""KIS response mapper — transforms raw KIS API responses to NGSAT internal models.

This is the translation layer between KIS-specific field names (stck_prpr, hldg_qty, etc.)
and NGSAT's clean domain types (PriceData, Position, AccountSummary).

Isolating the mapping here means:
- If KIS changes field names, only this file needs updating
- Other adapters (future brokers) have their own mappers
- Business logic never sees KIS-specific field names
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.types import AccountSummary, Market, OrderSide, OrderStatus, Position, PriceData, StockInfo


def _int(value: Any, default: int = 0) -> int:
    """Safely convert KIS field to int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    """Safely convert KIS field to float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_account_summary(raw: dict[str, Any]) -> AccountSummary:
    """Parse KIS balance response → AccountSummary.

    KIS balance endpoint returns:
    - output2: account overview (total asset, deposit, etc.)
    - output: list of positions
    """
    # output2 contains account-level summary
    summary_data = raw.get("output2") or raw.get("output1") or raw

    # If output2 is a list, take first element
    if isinstance(summary_data, list):
        summary_data = summary_data[0] if summary_data else {}

    if not isinstance(summary_data, dict):
        summary_data = {}

    total_asset = _float(summary_data.get("tot_evlu_amt") or summary_data.get("dnca_tot_amt"))
    deposit = _float(summary_data.get("prvs_rcdl_excc_amt") or summary_data.get("dnca_tot_amt"))
    total_eval = _float(summary_data.get("evlu_tot_amt"))
    total_pl = _float(summary_data.get("evlu_tot_pl"))
    total_pl_pct = _float(summary_data.get("evlu_tot_pl_pct") or summary_data.get("tot_evlu_pl_pct"))

    return AccountSummary(
        total_asset=total_asset,
        deposit=deposit,
        total_eval=total_eval,
        total_profit_loss=total_pl,
        total_profit_loss_pct=total_pl_pct,
    )


def parse_positions(raw: dict[str, Any]) -> list[Position]:
    """Parse KIS balance response → list of Position.

    KIS balance endpoint returns:
    - output: list of held stocks with pdno (code), hldg_qty, pchs_avg_pric, prpr, etc.
    """
    positions_data = raw.get("output") or raw.get("output1") or []
    if not isinstance(positions_data, list):
        positions_data = [positions_data] if positions_data else []

    positions: list[Position] = []

    for item in positions_data:
        if not isinstance(item, dict):
            continue

        qty = _int(item.get("hldg_qty") or item.get("ord_qty"))
        if qty <= 0:
            continue

        code = str(item.get("pdno") or item.get("stock_code") or "")
        name = str(item.get("prdt_name") or item.get("stock_name") or "")
        buy_price = _float(item.get("pchs_avg_pric") or item.get("avg_buy_price"))
        current_price = _float(item.get("prpr") or item.get("current_price"))
        buy_amount = _float(item.get("pchs_amt") or item.get("buy_amt") or (buy_price * qty))
        eval_amount = _float(item.get("evlu_amt") or (current_price * qty))
        profit_loss = _float(item.get("evlu_pl") or (eval_amount - buy_amount))
        profit_loss_pct = _float(item.get("evlu_pl_pct"))
        # KIS가 evlu_pl_pct를 제공하지 않으면 직접 계산
        if profit_loss_pct == 0.0 and buy_amount > 0:
            profit_loss_pct = (profit_loss / buy_amount) * 100

        # Determine market from code pattern
        market = _infer_market(code)

        positions.append(Position(
            code=code,
            name=name,
            quantity=qty,
            buy_price=buy_price,
            current_price=current_price,
            market=market,
            buy_amount=buy_amount,
            eval_amount=eval_amount,
            profit_loss=profit_loss,
            profit_loss_pct=profit_loss_pct,
            stop_loss_pct=3.0,  # default, will be updated by risk manager
        ))

    return positions


def parse_price(raw: dict[str, Any], code: str = "") -> PriceData:
    """Parse KIS current-price response → PriceData.

    KIS inquire-price endpoint returns output with stck_prpr, stck_oprc, etc.
    """
    now = datetime.now()
    return PriceData(
        code=code or str(raw.get("stck_shrn_iscd") or raw.get("pdno") or ""),
        timestamp=now,
        open=_float(raw.get("stck_oprc") or raw.get("oprc") or raw.get("bstp_nmix_oprc")),
        high=_float(raw.get("stck_hgpr") or raw.get("hgpr") or raw.get("bstp_nmix_hgpr")),
        low=_float(raw.get("stck_lwpr") or raw.get("lwpr") or raw.get("bstp_nmix_lwpr")),
        close=_float(raw.get("stck_prpr") or raw.get("prpr") or raw.get("bstp_nmix_prpr") or raw.get("current_price")),
        volume=_int(raw.get("acml_vol") or raw.get("accumulated_volume")),
        change_pct=_float(raw.get("prdy_ctrt") or raw.get("bstp_nmix_prdy_ctrt") or raw.get("change_rate") or raw.get("chg_rate")),
    )


def parse_price_history(raw: dict[str, Any], code: str = "") -> list[PriceData]:
    """Parse KIS daily-chart response → list of PriceData.

    KIS inquire-daily-chart returns output2 as list of daily OHLCV.
    """
    items = raw.get("output2") or raw.get("output") or []
    if not isinstance(items, list):
        items = [items] if items else []

    result: list[PriceData] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        date_str = str(item.get("stck_bsop_date") or item.get("date") or "")

        try:
            ts = datetime.strptime(date_str, "%Y%m%d") if len(date_str) == 8 else datetime.now()
        except ValueError:
            ts = datetime.now()

        result.append(PriceData(
            code=code,
            timestamp=ts,
            open=_float(item.get("stck_oprc") or item.get("open_price")),
            high=_float(item.get("stck_hgpr") or item.get("high_price")),
            low=_float(item.get("stck_lwpr") or item.get("low_price")),
            close=_float(item.get("stck_clpr") or item.get("close_price")),
            volume=_int(item.get("acml_vol") or item.get("volume")),
            change_pct=_float(item.get("prdy_ctrt") or item.get("change_pct")),
        ))

    return result


def _parse_minute_timestamp(date_str: str, time_str: str) -> datetime:
    """Combine KIS date (YYYYMMDD) + time (HHMMSS) into a datetime.

    Falls back to date-only, then to now(), on malformed input.
    """
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()

    if len(date_str) == 8:
        if time_str:
            t = time_str.zfill(6)
            try:
                return datetime.strptime(date_str + t, "%Y%m%d%H%M%S")
            except ValueError:
                pass
        try:
            return datetime.strptime(date_str, "%Y%m%d")
        except ValueError:
            pass
    return datetime.now()


def parse_minute_history(raw: dict[str, Any], code: str = "") -> list[PriceData]:
    """Parse KIS intraday minute-chart response → list of PriceData.

    KIS inquire-time-itemchartprice returns output2 as a list of minute bars,
    each with stck_bsop_date / stck_cntg_hour and OHLCV fields.
    Order is preserved as returned by KIS (typically most-recent first).
    """
    items = raw.get("output2") or raw.get("output") or []
    if not isinstance(items, list):
        items = [items] if items else []

    result: list[PriceData] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        date_str = str(item.get("stck_bsop_date") or item.get("date") or "")
        time_str = str(item.get("stck_cntg_hour") or item.get("time") or "")
        ts = _parse_minute_timestamp(date_str, time_str)

        result.append(PriceData(
            code=code,
            timestamp=ts,
            open=_float(item.get("stck_oprc") or item.get("open_price")),
            high=_float(item.get("stck_hgpr") or item.get("high_price")),
            low=_float(item.get("stck_lwpr") or item.get("low_price")),
            close=_float(
                item.get("stck_prpr")
                or item.get("stck_clpr")
                or item.get("close_price")
            ),
            volume=_int(item.get("cntg_vol") or item.get("acml_vol") or item.get("volume")),
            change_pct=_float(item.get("prdy_ctrt") or item.get("change_pct")),
        ))

    return result


def parse_stock_info(raw: dict[str, Any]) -> StockInfo:
    """Parse KIS stock-info response → StockInfo."""
    code = str(raw.get("pdno") or raw.get("stck_shrn_iscd") or raw.get("stock_code") or "")
    name = str(raw.get("prdt_name") or raw.get("stock_name") or raw.get("hts_kor_isnm") or "")

    # Market inference from code or explicit field
    market_str = str(raw.get("mrkt_cls_nm") or raw.get("market_code") or "").lower()
    if "kosdaq" in market_str:
        market = Market.KOSDAQ
    elif "kospi" in market_str:
        market = Market.KOSPI
    else:
        market = _infer_market(code)

    return StockInfo(code=code, name=name, market=market)


def build_order_payload(
    code: str,
    side: OrderSide,
    quantity: int,
    account_no: str,
    account_product_code: str,
    price: float | None = None,
) -> dict[str, Any]:
    """Build KIS order-cash request payload.

    Args:
        code: 6-digit stock code
        side: BUY or SELL
        quantity: Number of shares
        account_no: 8-digit account number (CANO)
        account_product_code: 2-digit product code (ACNT_PRDT_CD)
        price: Limit price (None = market order)

    Returns:
        KIS order-cash payload dict.
    """
    payload: dict[str, Any] = {
        "CANO": account_no,
        "ACNT_PRDT_CD": account_product_code,
        "PDNO": code,
        "ORD_DVSN": "01" if price is None else "00",  # 01=시장가, 00=지정가
        "ORD_QTY": str(quantity),
    }

    if price is not None:
        payload["ORD_UNPR"] = str(int(price))

    return payload


def _infer_market(code: str) -> Market:
    """Infer market (KOSPI/KOSDAQ) from stock code.

    This is a heuristic — KIS may provide explicit market info.
    KOSPI codes are typically 6 digits starting with 0 or 1.
    KOSDAQ codes typically start with 2 or 3.
    Note: This is not 100% accurate; always prefer explicit market data when available.
    """
    code = code.strip()
    if not code or len(code) < 6:
        return Market.KOSPI  # default

    first_digit = code[0]
    if first_digit in ("2", "3", "4", "5", "6", "8", "9"):
        return Market.KOSDAQ
    return Market.KOSPI


def parse_index_history(raw: dict[str, Any], code: str = "KOSPI") -> list[PriceData]:
    """KIS 지수 일봉 응답 → PriceData 리스트.

    KIS inquire-daily-indexchartprice (FHPUP02110000) 응답 파싱.
    지수 데이터는 종목과 필드명이 다름:
      - stck_bsop_date → date
      - bstp_nmix_prpr → close
      - bstp_nmix_oprc → open
      - bstp_nmix_hgpr → high
      - bstp_nmix_lwpr → low
      - acml_vol → volume
    """
    items = raw.get("output2") or raw.get("output") or []
    if not isinstance(items, list):
        items = [items] if items else []

    result: list[PriceData] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        date_str = str(item.get("stck_bsop_date") or item.get("date") or "")
        try:
            ts = datetime.strptime(date_str, "%Y%m%d") if len(date_str) == 8 else datetime.now()
        except ValueError:
            ts = datetime.now()

        close = _float(item.get("bstp_nmix_prpr"))
        if close == 0.0:
            continue  # 데이터 없는 row 스킵

        result.append(PriceData(
            code=code,
            timestamp=ts,
            open=_float(item.get("bstp_nmix_oprc")),
            high=_float(item.get("bstp_nmix_hgpr")),
            low=_float(item.get("bstp_nmix_lwpr")),
            close=close,
            volume=_int(item.get("acml_vol")),
        ))

    # 오름차순 정렬 (오래된→최신) — regime 평가가 closes[-1]를 최신으로 가정
    result.sort(key=lambda p: p.timestamp)
    return result


def parse_order_status(raw: dict[str, Any], order_id: str) -> OrderStatus:
    """KIS 주문조회(inquire_order) 응답 → OrderStatus.

    KIS inquire-order 응답의 output 필드에서 주문 상태를 판단:
      - odno: 주문번호
      - ord_qty: 주문수량
      - ccld_qty: 체결수량
      - rjpg_yn: 거절여부 (Y/N)
      - cncl_yn: 취소여부 (Y/N)

    Args:
        raw: KIS 원본 응답 dict.
        order_id: 확인할 주문번호.

    Returns:
        OrderStatus enum.
    """
    output = raw.get("output")
    if not isinstance(output, dict):
        output = raw.get("output1") or raw.get("output2") or {}

    odno = str(output.get("odno") or "")
    if odno and odno != order_id:
        for key in ("output1", "output2"):
            items = raw.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and str(item.get("odno") or "") == order_id:
                        output = item
                        break

    if not output.get("odno"):
        output = raw.get("output2") or raw.get("output1") or output

    rjpg_yn = str(output.get("rjpg_yn") or "N")
    cncl_yn = str(output.get("cncl_yn") or "N")
    ccld_qty = _float(output.get("ccld_qty") or 0)
    ord_qty = _float(output.get("ord_qty") or 1)

    if rjpg_yn == "Y":
        return OrderStatus.REJECTED
    if cncl_yn == "Y":
        return OrderStatus.CANCELLED
    if ccld_qty >= ord_qty and ord_qty > 0:
        return OrderStatus.FILLED
    if ccld_qty > 0:
        return OrderStatus.PARTIALLY_FILLED
    return OrderStatus.SUBMITTED


def parse_unfilled_orders(raw: dict[str, Any]) -> list:
    """Parse KIS 미체결 주문 목록 응답.

    KIS 응답 형식:
    {
        "output": [...],  # 각 주문의 배열
        "output1": {"총주문수": "..."},
        "rt_cd": "0",
    }

    output 각 항목:
        odno: 주문번호
        pdno: 종목코드
        prdt_name: 종목명
        ord_qty: 주문수량
        ord_unpr: 주문가격
        ord_tmd: 주문시각(HHMMSS)
        sll_buy_dvsn_cd: 01=매도, 02=매수
        ord_dvsn_cd: 00=지정가, 01=시장가
        ord_psbl_qty: 주문가능수량(미체결수량)
    """
    from core.types import UnfilledOrder

    items = raw.get("output") or raw.get("output2") or []
    if not isinstance(items, list):
        return []

    result = []
    for item in items:
        try:
            side_str = item.get("sll_buy_dvsn_cd", "")
            side = "buy" if side_str == "02" else "sell"
            order_dvsn = item.get("ord_dvsn_cd", "00")
            result.append(UnfilledOrder(
                code=item.get("pdno", ""),
                name=item.get("prdt_name", ""),
                side=side,
                quantity=int(float(item.get("ord_psbl_qty", "0"))),
                price=float(item.get("ord_unpr", "0")),
                order_id=item.get("odno", ""),
                order_time=item.get("ord_tmd", ""),
                order_dvsn=order_dvsn,
            ))
        except (ValueError, TypeError):
            continue
    return result


def build_cancel_payload(
    code: str,
    order_id: str,
    quantity: int,
    account_no: str,
    account_product_code: str,
) -> dict[str, str]:
    """Build KIS 주문취소 요청 payload."""
    return {
        "CANO": account_no,
        "ACNT_PRDT_CD": account_product_code,
        "KRX_FWDG_ORD_ORGNO": "",
        "ORGN_ODNO": order_id,
        "ORD_DVSN": "00",
        "QTY_ALL_ORD_YN": "Y",
        "PDNO": code,
        "ORD_QTY": str(quantity),
    }
