"""NGSAT Telegram bot — notifications and remote control.

Sends notifications to the operator and accepts commands for
remote control of the trading system.

Commands:
  /start    — 자동매매 시작
  /stop     — 자동매매 일시정지
  /shutdown — 시스템 종료
  /status   — 현재 상태 조회
  /account  — 계좌 현황 조회
  /positions — 보유 포지션 조회
  /forcesell <code> — 강제 매도
  /forcehold <code> — 강제 홀드
"""

from __future__ import annotations

from typing import Any

from core.logger import logger
from messaging.notifier import (
    NotificationMessage,
    build_daily_report_notification,
    build_force_hold_notification,
    build_force_sell_notification,
    build_system_event_notification,
    build_trade_notification,
)


class TelegramBot:
    """Telegram bot for NGSAT notifications and remote control.

    Handles:
    - Sending notifications (trades, reports, events)
    - Processing commands from the operator
    - Routing commands to the orchestrator/controller

    The bot does NOT make trading decisions — it only relays
    operator commands to the trading system.
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
    ):
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._bot = None  # python-telegram-bot Bot instance
        self._orchestrator = None  # Set by set_orchestrator()

    @property
    def is_configured(self) -> bool:
        """Check if bot token and chat ID are configured."""
        return bool(self._bot_token and self._chat_id)

    def set_orchestrator(self, orchestrator) -> None:
        """Connect the bot to the trading orchestrator."""
        self._orchestrator = orchestrator

    async def send_notification(self, message: NotificationMessage) -> bool:
        """Send a notification message to Telegram.

        Args:
            message: NotificationMessage to send.

        Returns:
            True if sent successfully.
        """
        if not self.is_configured:
            logger.debug("텔레그램 미설정 — 알림 건너뜀")
            return False

        try:
            # Use python-telegram-bot if available
            if self._bot is None:
                try:
                    import importlib
                    telegram_lib = importlib.import_module("telegram")
                    self._bot = telegram_lib.Bot(token=self._bot_token)
                except ImportError:
                    logger.warning("python-telegram-bot 미설치")
                    return False

            await self._bot.send_message(
                chat_id=self._chat_id,
                text=message.text,
                parse_mode=None,  # Plain text for maximum compatibility
            )
            return True

        except Exception as e:
            logger.error(f"텔레그램 전송 실패: {type(e).__name__}: {e}")
            return False

    async def send_trade_notification(
        self,
        side: str,
        code: str,
        name: str,
        quantity: int,
        price: float,
        reason: str,
    ) -> bool:
        """Send a trade execution notification."""
        msg = build_trade_notification(side, code, name, quantity, price, reason)
        return await self.send_notification(msg)

    async def send_daily_report(
        self,
        date: str,
        total_trades: int,
        buy_count: int,
        sell_count: int,
        total_pnl: float,
        win_rate: float,
        current_capital: float,
        positions_summary: str = "",
    ) -> bool:
        """Send the daily report."""
        msg = build_daily_report_notification(
            date, total_trades, buy_count, sell_count,
            total_pnl, win_rate, current_capital, positions_summary,
        )
        return await self.send_notification(msg)

    async def send_system_event(
        self,
        event_type: str,
        message: str,
    ) -> bool:
        """Send a system event notification."""
        msg = build_system_event_notification(event_type, message)
        return await self.send_notification(msg)

    def get_command_help(self) -> str:
        """Return command help text."""
        return (
            "NGSAT 명령어:\n"
            "/start — 자동매매 시작\n"
            "/stop — 자동매매 일시정지\n"
            "/shutdown — 시스템 종료\n"
            "/status — 현재 상태\n"
            "/account — 계좌 현황\n"
            "/positions — 보유 포지션\n"
            "/forcesell <코드> — 강제 매도\n"
            "/forcehold <코드> — 강제 홀드\n"
            "/help — 도움말"
        )

    async def start_polling(self) -> None:
        """Start polling for Telegram commands (runs forever).

        Uses get_updates long-polling to receive operator commands.
        Runs as a background task — non-blocking for the trading loop.
        """
        if not self.is_configured:
            logger.warning("텔레그램 미설정 — 폴링 시작 불가")
            return

        logger.info("텔레그램 명령어 폴링 시작")
        if self._bot is None:
            import importlib
            telegram_lib = importlib.import_module("telegram")
            self._bot = telegram_lib.Bot(token=self._bot_token)

        offset: int = 0
        while True:
            try:
                updates = await self._bot.get_updates(offset=offset, timeout=30)
                for update in updates:
                    offset = update.update_id + 1
                    if not update.message or not update.message.text:
                        continue
                    text = update.message.text.strip()
                    if not text.startswith("/"):
                        continue

                    # Parse command and args
                    parts = text[1:].split(None, 1)
                    cmd = parts[0].lower().split("@")[0]  # remove @botname
                    arg = parts[1] if len(parts) > 1 else ""

                    logger.info(f"텔레그램 명령어: /{cmd} {arg} (from {update.message.chat.id})")

                    # Only respond to configured chat_id
                    chat_id = str(update.message.chat.id)
                    if chat_id != self._chat_id:
                        await self._bot.send_message(
                            chat_id=chat_id,
                            text="권한이 없습니다. 관리자만 사용할 수 있습니다."
                        )
                        continue

                    response = await self.process_command(cmd, arg)
                    try:
                        await self._bot.send_message(
                            chat_id=self._chat_id,
                            text=response,
                            parse_mode=None,
                        )
                    except Exception as e:
                        logger.error(f"응답 전송 실패: {e}")

            except Exception as e:
                logger.error(f"텔레그램 폴링 오류: {type(e).__name__}: {e}")
                import asyncio
                await asyncio.sleep(5)  # backoff on error

    async def process_command(self, command: str, args: str = "") -> str:
        """Process a command from the operator.

        Args:
            command: Command name (without /).
            args: Command arguments.

        Returns:
            Response text (Korean).
        """
        if self._orchestrator is None:
            return "거래 시스템이 연결되지 않았습니다"

        controller = self._orchestrator.controller

        if command == "start":
            msg = controller.start()
            await self.send_system_event("start", msg)
            return msg

        elif command == "stop":
            msg = controller.stop()
            await self.send_system_event("stop", msg)
            return msg

        elif command == "shutdown":
            msg = controller.shutdown()
            await self.send_system_event("shutdown", msg)
            return msg

        elif command == "status":
            state = controller.state.value
            risk_halted = self._orchestrator.risk_manager.is_halted
            risk_reason = self._orchestrator.risk_manager.halt_reason or "없음"

            return (
                f"NGSAT 상태\n"
                f"──────────\n"
                f"운영: {state}\n"
                f"리스크 중단: {'예' if risk_halted else '아니오'}\n"
                f"중단 사유: {risk_reason}"
            )

        elif command == "account":
            try:
                import asyncio
                account = await self._orchestrator._broker.get_account_summary()
                return (
                    f"계좌 현황\n"
                    f"──────────\n"
                    f"총 자산: {account.total_asset:,.0f}원\n"
                    f"예수금: {account.deposit:,.0f}원\n"
                    f"평가금: {account.total_eval:,.0f}원\n"
                    f"손익: {account.total_profit_loss:+,.0f}원 ({account.total_profit_loss_pct:+.1f}%)"
                )
            except Exception as e:
                return f"계좌 조회 실패: {e}"

        elif command == "positions":
            try:
                positions = await self._orchestrator._broker.get_positions()
                if not positions:
                    return "보유 포지션 없음"

                lines = ["보유 포지션", "──────────"]
                for p in positions:
                    lines.append(
                        f"{p.name}({p.code}) {p.quantity}주\n"
                        f"  매수가: {p.buy_price:,.0f} | 현재: {p.current_price:,.0f}\n"
                        f"  손익: {p.profit_loss:+,.0f}원 ({p.profit_loss_pct:+.1f}%)\n"
                        f"  손절선: -{p.stop_loss_pct:.1f}%"
                    )
                return "\n".join(lines)

            except Exception as e:
                return f"포지션 조회 실패: {e}"

        elif command == "forcesell":
            if not args:
                return "사용법: /forcesell <종목코드>"
            result = await self._orchestrator.force_sell(args)
            if result.success:
                return f"강제 매도 완료: {result.name}({result.code}) {result.quantity}주"
            else:
                return f"강제 매도 실패: {result.error}"

        elif command == "forcehold":
            if not args:
                return "사용법: /forcehold <종목코드>"
            controller.force_hold(args)
            return f"강제 홀드 설정: {args}"

        elif command == "help":
            return self.get_command_help()

        else:
            return f"알 수 없는 명령어: /{command}\n{self.get_command_help()}"
