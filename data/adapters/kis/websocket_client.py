"""KIS WebSocket real-time price client.

Connects to KIS WebSocket server for real-time stock prices.
Falls back to REST API polling when WebSocket is unavailable.

WebSocket endpoints:
  Real: wss://openapi.koreainvestment.com:21000
  Demo: wss://openapi.koreainvestment.com:21001

Protocol:
  1. Get approval_key via REST POST /oauth2/Approval
  2. Connect WebSocket
  3. Subscribe with JSON: {header, body}
  4. Receive real-time price data
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import websockets

from core.logger import logger

# TR_ID constants
TR_REALTIME_PRICE = "H0UCNT0"    # 주식체결가 (실시간 현재가)
TR_REALTIME_ASKING = "H0UNCN0"   # 주식호가 (10단계)

WS_URL_REAL = "wss://openapi.koreainvestment.com:21000"
WS_URL_DEMO = "wss://openapi.koreainvestment.com:21001"


class KisWebSocketClient:
    """KIS 실시간 시세 WebSocket 클라이언트.

    Usage:
        ws = KisWebSocketClient(app_key, app_secret, base_url)
        ws.on_price = lambda code, price: print(code, price)
        await ws.connect()
        await ws.subscribe("005930")  # 삼성전자
        await ws.subscribe("000660")  # SK하이닉스
        # ... prices arrive via on_price callback
        await ws.disconnect()
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        base_url: str,
    ):
        self._app_key = app_key
        self._app_secret = app_secret
        self._ws_url = WS_URL_DEMO if "demo" in base_url else WS_URL_REAL
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._approval_key: Optional[str] = None
        self._subscribed: set[str] = set()
        self._running = False
        self._reconnect_delay = 1.0

        # Callbacks (set by consumer)
        self.on_price: Optional[Callable[[str, float, int, str], None]] = None
        """on_price(code, current_price, volume, timestamp_hhmmss)"""

        self.on_error: Optional[Callable[[Exception], None]] = None

    # ── Public API ──

    async def connect(self) -> bool:
        """Connect to WebSocket server.

        Returns True if connected, False if approval_key fails.
        """
        self._approval_key = await self._request_approval_key()
        if not self._approval_key:
            logger.error("KIS WebSocket: approval_key 발급 실패")
            return False

        try:
            self._ws = await websockets.connect(
                self._ws_url,
                ping_interval=30,
                ping_timeout=10,
                max_size=2 ** 20,  # 1MB
            )
            self._running = True
            self._reconnect_delay = 1.0

            # Resubscribe previous codes
            for code in self._subscribed:
                await self._send_subscribe(code)

            logger.info(f"KIS WebSocket 연결됨: {self._ws_url}")
            return True

        except Exception as e:
            logger.error(f"KIS WebSocket 연결 실패: {e}")
            self._running = False
            return False

    async def subscribe(self, code: str) -> None:
        """Subscribe to real-time price for a stock code."""
        self._subscribed.add(code)
        if self._ws and self._running:
            await self._send_subscribe(code)

    async def unsubscribe(self, code: str) -> None:
        """Unsubscribe from a stock code."""
        self._subscribed.discard(code)
        if self._ws and self._running:
            await self._send_unsubscribe(code)

    async def listen(self) -> None:
        """Listen for incoming messages (runs until disconnect or error).

        Callbacks on_price and on_error are invoked from this coroutine.
        Auto-reconnects with backoff on unexpected disconnects.
        """
        while self._running:
            try:
                if not self._ws:
                    if not await self._reconnect():
                        break
                    continue

                raw = await self._ws.recv()
                await self._handle_message(raw)
                self._reconnect_delay = 1.0  # reset on successful receive

            except websockets.ConnectionClosed:
                logger.warning("KIS WebSocket 연결 종료 — 재연결 시도")
                self._ws = None
                if not await self._reconnect():
                    break

            except Exception as e:
                logger.error(f"KIS WebSocket 수신 오류: {e}")
                if self.on_error:
                    self.on_error(e)
                self._ws = None
                if not await self._reconnect():
                    break

    async def disconnect(self) -> None:
        """Disconnect from WebSocket server."""
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("KIS WebSocket 연결 해제")

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and self._running

    @property
    def subscribed_count(self) -> int:
        return len(self._subscribed)

    # ── Internal ──

    async def _request_approval_key(self) -> Optional[str]:
        """Request WebSocket approval key from KIS REST API.
        
        Uses the same HTTP client pattern as the main KIS adapter.
        """
        import aiohttp
        url = f"https://openapi.koreainvestment.com:9443/oauth2/Approval"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "secretkey": self._app_secret,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as resp:
                    data = await resp.json()
                    key = data.get("approval_key")
                    if key:
                        logger.info("KIS approval_key 발급 성공")
                        return key
                    logger.error(f"KIS approval_key 발급 실패: {data}")
                    return None
        except Exception as e:
            logger.error(f"KIS approval_key 요청 오류: {e}")
            return None

    async def _reconnect(self) -> bool:
        """Reconnect with exponential backoff."""
        delay = min(self._reconnect_delay, 30.0)
        logger.info(f"KIS WebSocket 재연결 ({delay:.0f}초 후...)")
        await asyncio.sleep(delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
        return await self.connect()

    async def _send_subscribe(self, code: str) -> None:
        """Send subscribe message for a stock code."""
        if not self._ws:
            return
        msg = {
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": "1",  # 1=subscribe, 2=unsubscribe
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": TR_REALTIME_PRICE,
                    "tr_key": code,
                }
            },
        }
        try:
            await self._ws.send(json.dumps(msg))
            logger.debug(f"KIS WebSocket 구독: {code}")
        except Exception as e:
            logger.error(f"KIS WebSocket 구독 실패 {code}: {e}")

    async def _send_unsubscribe(self, code: str) -> None:
        """Send unsubscribe message."""
        if not self._ws:
            return
        msg = {
            "header": {
                "approval_key": self._approval_key,
                "custtype": "P",
                "tr_type": "2",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": TR_REALTIME_PRICE,
                    "tr_key": code,
                }
            },
        }
        try:
            await self._ws.send(json.dumps(msg))
        except Exception:
            pass

    async def _handle_message(self, raw: Any) -> None:
        """Parse and dispatch incoming WebSocket message."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Parse header
        header = data.get("header") or {}
        tr_id = header.get("tr_id", "")

        # Parse body
        body = data.get("body") or {}

        if tr_id == TR_REALTIME_PRICE and self.on_price:
            code = body.get("stck_shrn_iscd", "")
            try:
                price = float(body.get("stck_prpr", "0"))
            except (ValueError, TypeError):
                price = 0.0
            try:
                volume = int(body.get("acml_vol", "0"))
            except (ValueError, TypeError):
                volume = 0
            timestamp = body.get("stck_cntg_hour", "")
            self.on_price(code, price, volume, timestamp)
