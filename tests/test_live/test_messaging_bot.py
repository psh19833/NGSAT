"""Tests for NGSAT Telegram bot command processing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.types import AccountSummary, Market, Position
from messaging.bot import TelegramBot


class MockOrchestrator:
    """Mock orchestrator for testing."""

    def __init__(self):
        self.controller = MagicMock()
        self.controller.state.value = "idle"
        self.controller.is_running = False
        self.controller.is_force_hold = MagicMock(return_value=False)
        self.controller.start = MagicMock(return_value="자동매매 시작")
        self.controller.stop = MagicMock(return_value="일시정지")
        self.controller.shutdown = MagicMock(return_value="종료")
        self.controller.force_hold = MagicMock()

        self.risk_manager = MagicMock()
        self.risk_manager.is_halted = False
        self.risk_manager.halt_reason = None

        self._broker = AsyncMock()
        self._broker.get_account_summary = AsyncMock(return_value=AccountSummary(
            total_asset=10_000_000, deposit=5_000_000,
            total_eval=5_000_000, total_profit_loss=100000,
            total_profit_loss_pct=1.0,
        ))
        self._broker.get_positions = AsyncMock(return_value=[])

        self._cycle_count = 0

    async def force_sell(self, code):
        from live.executor import ExecutionResult
        return ExecutionResult(
            success=True, order_id="TEST001",
            code=code, name="삼성전자", quantity=10,
            price=70000, amount=700000,
            action="force_sell", reason="강제 매도",
        )

    async def get_account_summary(self):
        return await self._broker.get_account_summary()


@pytest.fixture
def bot():
    return TelegramBot(bot_token="test_token", chat_id="test_chat")


@pytest.fixture
def bot_with_orch():
    bot = TelegramBot(bot_token="test", chat_id="test")
    bot.set_orchestrator(MockOrchestrator())
    return bot


class TestTelegramBot:
    """Telegram bot tests."""

    def test_is_configured(self):
        bot = TelegramBot("token", "chat")
        assert bot.is_configured is True

    def test_not_configured(self):
        bot = TelegramBot("", "")
        assert bot.is_configured is False

    def test_command_help(self, bot):
        help_text = bot.get_command_help()
        assert "/start" in help_text
        assert "/stop" in help_text
        assert "/forcesell" in help_text
        assert "/help" in help_text

    @pytest.mark.asyncio
    async def test_process_start_command(self, bot_with_orch):
        result = await bot_with_orch.process_command("start")
        assert "시작" in result

    @pytest.mark.asyncio
    async def test_process_stop_command(self, bot_with_orch):
        bot_with_orch._orchestrator.controller.is_running = True
        result = await bot_with_orch.process_command("stop")
        assert "정지" in result

    @pytest.mark.asyncio
    async def test_process_status_command(self, bot_with_orch):
        result = await bot_with_orch.process_command("status")
        assert "NGSAT 상태" in result
        assert "idle" in result

    @pytest.mark.asyncio
    async def test_process_account_command(self, bot_with_orch):
        result = await bot_with_orch.process_command("account")
        assert "계좌 현황" in result
        assert "10,000,000" in result

    @pytest.mark.asyncio
    async def test_process_positions_command(self, bot_with_orch):
        result = await bot_with_orch.process_command("positions")
        assert "포지션" in result

    @pytest.mark.asyncio
    async def test_process_forcesell_command(self, bot_with_orch):
        result = await bot_with_orch.process_command("forcesell", "005930")
        assert "강제 매도" in result

    @pytest.mark.asyncio
    async def test_process_forcesell_no_code(self, bot_with_orch):
        result = await bot_with_orch.process_command("forcesell", "")
        assert "사용법" in result

    @pytest.mark.asyncio
    async def test_process_forcehold_command(self, bot_with_orch):
        result = await bot_with_orch.process_command("forcehold", "005930")
        assert "홀드" in result

    @pytest.mark.asyncio
    async def test_process_help_command(self, bot_with_orch):
        result = await bot_with_orch.process_command("help")
        assert "/start" in result

    @pytest.mark.asyncio
    async def test_unknown_command(self, bot_with_orch):
        result = await bot_with_orch.process_command("nonexistent")
        assert "알 수 없는" in result

    @pytest.mark.asyncio
    async def test_command_without_orchestrator(self, bot):
        result = await bot.process_command("start")
        assert "연결되지" in result

    @pytest.mark.asyncio
    async def test_send_notification_not_configured(self):
        """Send should silently fail when not configured."""
        from messaging.notifier import NotificationMessage
        bot = TelegramBot("", "")
        result = await bot.send_notification(NotificationMessage(text="test"))
        assert result is False
