"""Tests for NGSAT Telegram notifier message builders."""

from __future__ import annotations

import pytest

from telegram.notifier import (
    NotificationMessage,
    build_daily_report_notification,
    build_force_hold_notification,
    build_force_sell_notification,
    build_system_event_notification,
    build_trade_notification,
)


class TestTradeNotification:
    """Trade notification message tests."""

    def test_buy_notification(self):
        msg = build_trade_notification(
            side="buy", code="005930", name="삼성전자",
            quantity=10, price=70000,
            reason="ML 예측: 매수 (상승 확률 72%)",
        )
        assert "매수" in msg.text
        assert "삼성전자" in msg.text
        assert "005930" in msg.text
        assert "10" in msg.text
        assert "70,000" in msg.text
        assert "72%" in msg.text
        assert msg.level == "success"

    def test_sell_notification(self):
        msg = build_trade_notification(
            side="sell", code="005930", name="삼성전자",
            quantity=5, price=72000,
            reason="ML 추론: 매도 — 상승 확률 저하",
        )
        assert "매도" in msg.text
        assert msg.level == "info"

    def test_notification_has_emoji(self):
        msg = build_trade_notification("buy", "005930", "삼성전자", 10, 70000, "reason")
        assert len(msg.emoji) > 0


class TestDailyReportNotification:
    """Daily report notification tests."""

    def test_daily_report_content(self):
        msg = build_daily_report_notification(
            date="2025-06-25",
            total_trades=5, buy_count=3, sell_count=2,
            total_pnl=150000, win_rate=60.0,
            current_capital=10150000,
            positions_summary="삼성전자(005930) 10주",
        )
        assert "일일 보고" in msg.text
        assert "2025-06-25" in msg.text
        assert "5건" in msg.text
        assert "60.0%" in msg.text
        assert "+150,000" in msg.text
        assert "삼성전자" in msg.text

    def test_daily_report_negative_pnl(self):
        msg = build_daily_report_notification(
            date="2025-06-25",
            total_trades=3, buy_count=1, sell_count=2,
            total_pnl=-200000, win_rate=33.3,
            current_capital=9800000,
        )
        assert "-200,000" in msg.text


class TestSystemEventNotification:
    """System event notification tests."""

    def test_start_event(self):
        msg = build_system_event_notification("start", "자동매매 시작")
        assert "매매 시작" in msg.text
        assert msg.level == "success"

    def test_stop_event(self):
        msg = build_system_event_notification("stop", "일시정지")
        assert "일시정지" in msg.text
        assert msg.level == "info"

    def test_halt_event(self):
        msg = build_system_event_notification("halt", "일일 손실 한도 도달")
        assert "자동 중단" in msg.text
        assert msg.level == "warning"

    def test_error_event(self):
        msg = build_system_event_notification("error", "KIS API 연결 실패")
        assert "오류" in msg.text
        assert msg.level == "error"

    def test_unknown_event_type(self):
        msg = build_system_event_notification("custom_event", "custom message")
        assert "custom" in msg.text


class TestForceNotifications:
    """Force sell/hold notification tests."""

    def test_force_sell_notification(self):
        msg = build_force_sell_notification("005930", "삼성전자", 10, 71000)
        assert "강제 매도" in msg.text
        assert "삼성전자" in msg.text
        assert "10" in msg.text
        assert msg.level == "warning"

    def test_force_hold_notification(self):
        msg = build_force_hold_notification("005930", "삼성전자")
        assert "강제 홀드" in msg.text
        assert "삼성전자" in msg.text
        assert msg.level == "info"
