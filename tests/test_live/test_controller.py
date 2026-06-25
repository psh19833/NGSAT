"""Tests for NGSAT trading controller."""

from __future__ import annotations

import pytest

from live.controller import TradingController, TradingState


@pytest.fixture
def controller():
    return TradingController()


class TestTradingLifecycle:
    """Start / Stop / Shutdown lifecycle."""

    def test_initial_state_is_idle(self, controller):
        assert controller.state == TradingState.IDLE
        assert controller.is_running is False

    def test_start(self, controller):
        msg = controller.start()
        assert controller.state == TradingState.RUNNING
        assert controller.is_running is True
        assert "시작" in msg

    def test_stop_from_idle(self, controller):
        """Stopping when not running should not change state."""
        msg = controller.stop()
        assert "진행 중이 아닙니다" in msg
        assert controller.state == TradingState.IDLE

    def test_stop_from_running(self, controller):
        controller.start()
        msg = controller.stop()
        assert controller.state == TradingState.PAUSED
        assert "정지" in msg

    def test_start_when_halted(self, controller):
        """Cannot start when halted by risk."""
        controller.halt_by_risk("일일 손실 한도")
        msg = controller.start()
        assert controller.state == TradingState.HALTED
        assert "자동 중단" in msg

    def test_shutdown(self, controller):
        controller.start()
        msg = controller.shutdown()
        assert controller.state == TradingState.SHUTDOWN
        assert "종료" in msg


class TestForceControls:
    """Force sell and force hold operations."""

    def test_force_hold(self, controller):
        msg = controller.force_hold("005930", "삼성전자")
        assert controller.is_force_hold("005930") is True
        assert "강제 홀드" in msg

    def test_release_hold(self, controller):
        controller.force_hold("005930", "삼성전자")
        msg = controller.release_hold("005930", "삼성전자")
        assert controller.is_force_hold("005930") is False
        assert "해제" in msg

    def test_force_sell(self, controller):
        msg = controller.force_sell("005930", "삼성전자")
        assert "강제 매도" in msg

    def test_shutdown_clears_force_holds(self, controller):
        controller.force_hold("005930", "삼성전자")
        controller.force_hold("000660", "SK하이닉스")
        controller.shutdown()
        assert controller.is_force_hold("005930") is False
        assert controller.is_force_hold("000660") is False
