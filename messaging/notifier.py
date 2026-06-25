"""NGSAT Telegram notifier — sends trading notifications to the operator.

Sends clean, concise Korean notifications for:
- Trade executions (buy/sell with reason)
- Daily reports (end-of-day summary)
- System events (start/stop/halt/errors)

All messages follow the format:
  종목명(코드) — action — reason
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.logger import logger


@dataclass
class NotificationMessage:
    """A notification message ready to send.
    
    Attributes:
        text: Full message text (Korean).
        level: "info" / "warning" / "error" / "success".
        emoji: Visual indicator.
    """
    text: str
    level: str = "info"
    emoji: str = "📊"


# ── Message builders ──

def build_trade_notification(
    side: str,
    code: str,
    name: str,
    quantity: int,
    price: float,
    reason: str,
    action: str = "",
) -> NotificationMessage:
    """Build a trade execution notification.
    
    Format: 📌 매수/매도 | 종목명(코드) | 수량 | 가격 | 근거
    """
    if side == "buy":
        emoji = "🟢"
        action_kr = "매수"
        level = "success"
    else:
        emoji = "🔴"
        action_kr = "매도"
        level = "info"
    
    text = (
        f"{emoji} {action_kr} 체결\n"
        f"종목: {name}({code})\n"
        f"수량: {quantity}주\n"
        f"가격: {price:,.0f}원\n"
        f"근거: {reason}"
    )
    
    return NotificationMessage(text=text, level=level, emoji=emoji)


def build_daily_report_notification(
    date: str,
    total_trades: int,
    buy_count: int,
    sell_count: int,
    total_pnl: float,
    win_rate: float,
    current_capital: float,
    positions_summary: str = "",
) -> NotificationMessage:
    """Build a daily report notification.
    
    Format: 📋 일일 보고 | 날짜 | 거래요약 | 손익 | 보유현황
    """
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"
    
    text = (
        f"📋 일일 보고 ({date})\n"
        f"──────────\n"
        f"거래: {total_trades}건 (매수 {buy_count} / 매도 {sell_count})\n"
        f"승률: {win_rate:.1f}%\n"
        f"{pnl_emoji} 손익: {total_pnl:+,.0f}원\n"
        f"잔고: {current_capital:,.0f}원"
    )
    
    if positions_summary:
        text += f"\n──────────\n보유:\n{positions_summary}"
    
    return NotificationMessage(text=text, level="info", emoji="📋")


def build_system_event_notification(
    event_type: str,
    message: str,
) -> NotificationMessage:
    """Build a system event notification.
    
    Event types: start, stop, shutdown, halt, error, warning
    """
    event_config = {
        "start": ("🚀", "success", "매매 시작"),
        "stop": ("⏸️", "info", "매매 일시정지"),
        "shutdown": ("🛑", "info", "시스템 종료"),
        "halt": ("⚠️", "warning", "자동 중단"),
        "error": ("❌", "error", "오류 발생"),
        "warning": ("⚠️", "warning", "경고"),
    }
    
    emoji, level, default_msg = event_config.get(
        event_type, ("📢", "info", event_type)
    )
    
    text = f"{emoji} {default_msg}\n{message}"
    
    return NotificationMessage(text=text, level=level, emoji=emoji)


def build_force_sell_notification(
    code: str,
    name: str,
    quantity: int,
    price: float,
) -> NotificationMessage:
    """Build a force sell notification."""
    text = (
        f"🔴 강제 매도 실행\n"
        f"종목: {name}({code})\n"
        f"수량: {quantity}주\n"
        f"가격: {price:,.0f}원\n"
        f"근거: 대표님 직접 지시"
    )
    
    return NotificationMessage(text=text, level="warning", emoji="🔴")


def build_force_hold_notification(
    code: str,
    name: str,
) -> NotificationMessage:
    """Build a force hold notification."""
    text = (
        f"🔒 강제 홀드 설정\n"
        f"종목: {name}({code})\n"
        f"자동 매도 일시 중단"
    )
    
    return NotificationMessage(text=text, level="info", emoji="🔒")
