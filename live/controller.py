"""NGSAT live trading controller.

Provides human control over the automated trading system:
- Start / Stop / Shutdown
- Force sell (강제 매도)
- Force hold (강제 홀드)

The controller is the ONLY entry point for human intervention.
All trading logic is automated; humans only control the on/off switch.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from core.logger import logger


class TradingState(str, Enum):
    """Trading system state."""
    IDLE = "idle"            # 대기 중 (시작 전)
    RUNNING = "running"      # 매매 자동 진행 중
    PAUSED = "paused"        # 일시 정지
    HALTED = "halted"        # 리스크 한도 도달로 자동 중단
    SHUTDOWN = "shutdown"    # 완전 종료


class TradingController:
    """Controls the automated trading lifecycle.
    
    Human operations:
    - start(): Begin automated trading
    - stop(): Pause trading (can resume)
    - shutdown(): Completely stop and clean up
    - force_sell(code): Force sell a specific position
    - force_hold(code): Prevent auto-sell of a position
    
    Automated:
    - The orchestrator runs the buy/sell cycle autonomously
    - Human never directly triggers individual trades
    """

    def __init__(self):
        self._state = TradingState.IDLE
        self._force_hold_codes: set[str] = set()
        self._started_at: Optional[datetime] = None

    @property
    def state(self) -> TradingState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == TradingState.RUNNING

    def start(self) -> str:
        """Start automated trading.
        
        Returns:
            Status message.
        """
        if self._state == TradingState.RUNNING:
            return "이미 매매가 진행 중입니다"

        if self._state == TradingState.HALTED:
            return "리스크 한도로 자동 중단됨 — 새 거래일에 해제됩니다"

        self._state = TradingState.RUNNING
        self._started_at = datetime.now()
        msg = "자동매매 시작"
        logger.info(msg)
        return msg

    def stop(self) -> str:
        """Pause trading (can resume with start()).
        
        Returns:
            Status message.
        """
        if self._state != TradingState.RUNNING:
            return "매매가 진행 중이 아닙니다"

        self._state = TradingState.PAUSED
        msg = "자동매매 일시 정지"
        logger.info(msg)
        return msg

    def shutdown(self) -> str:
        """Completely shut down trading.
        
        Returns:
            Status message.
        """
        self._state = TradingState.SHUTDOWN
        self._force_hold_codes.clear()
        msg = "자동매매 종료"
        logger.info(msg)
        return msg

    def force_sell(self, code: str, name: str = "") -> str:
        """Force sell a position — 강제 매도.
        
        Args:
            code: Stock code to force sell.
            name: Stock name (for logging).
        
        Returns:
            Status message.
        """
        label = f"{name}({code})" if name else code
        msg = f"강제 매도 지시: {label}"
        logger.info(msg)
        # Actual execution handled by orchestrator
        return msg

    def force_hold(self, code: str, name: str = "") -> str:
        """Force hold a position — 강제 홀드.
        
        Prevents the auto-sell logic from selling this position.
        
        Args:
            code: Stock code to force hold.
            name: Stock name (for logging).
        
        Returns:
            Status message.
        """
        label = f"{name}({code})" if name else code
        self._force_hold_codes.add(code)
        msg = f"강제 홀드 지시: {label}"
        logger.info(msg)
        return msg

    def release_hold(self, code: str, name: str = "") -> str:
        """Release force hold on a position.
        
        Args:
            code: Stock code to release.
            name: Stock name (for logging).
        
        Returns:
            Status message.
        """
        label = f"{name}({code})" if name else code
        self._force_hold_codes.discard(code)
        msg = f"강제 홀드 해제: {label}"
        logger.info(msg)
        return msg

    def is_force_hold(self, code: str) -> bool:
        """Check if a position is under force hold."""
        return code in self._force_hold_codes

    def halt_by_risk(self, reason: str) -> None:
        """Halt trading due to risk limit (called by RiskManager).
        
        Args:
            reason: Why trading was halted.
        """
        self._state = TradingState.HALTED
        logger.warning(f"리스크 자동 중단: {reason}")

    def restart(self) -> str:
        """서버 재시작 — 모든 상태를 초기화하고 대기 상태로.

        강제홀드 목록도 초기화. HALTED/SHUTDOWN 상태에서도 사용 가능.
        
        Returns:
            Status message.
        """
        self._state = TradingState.IDLE
        self._force_hold_codes.clear()
        self._started_at = None
        msg = "서버 재시작 완료"
        logger.info(msg)
        return msg
