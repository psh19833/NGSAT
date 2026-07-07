"""KIS HTTP client — async transport layer.

Uses httpx.AsyncClient for non-blocking HTTP calls.
Handles:
- Authentication headers (token + appkey/appsecret)
- TR_ID injection
- Response validation (rt_cd check)
- Error handling with structured exceptions
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from core.exceptions import BrokerError, DataError
from core.logger import logger
from data.adapters.kis.endpoints import KisEndpoint, get_endpoint
from data.adapters.kis.token_manager import KisTokenManager


@dataclass
class KisResponse:
    """Parsed KIS API response."""
    success: bool
    data: dict[str, Any]           # output / output1 / output2
    raw: dict[str, Any]            # full response body
    rt_cd: str                     # KIS return code ("0" = success)
    msg_cd: str                    # KIS message code
    msg1: str                      # KIS message text


class KisHttpClient:
    """Async HTTP client for KIS API.

    All KIS REST calls go through this client.
    Handles auth, headers, response parsing, and error normalization.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        base_url: str,
        token_manager: KisTokenManager | None = None,
        timeout: float = 10.0,
    ):
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._token_manager = token_manager or KisTokenManager(
            app_key, app_secret, base_url
        )
        self._client: httpx.AsyncClient | None = None
        # C-1: 중앙 Rate Limit — 모든 KIS REST API 호출에 적용
        self._rate_semaphore = asyncio.Semaphore(15)  # 최대 15회 동시 호출
        self._last_request_time: float = 0.0
        self._rate_lock = asyncio.Lock()

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def _throttle(self) -> None:
        """중앙 Rate Limit: API 간 최소 50ms 간격."""
        async with self._rate_lock:
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < 0.05:
                await asyncio.sleep(0.05 - elapsed)
            self._last_request_time = time.monotonic()

    async def _build_headers(
        self,
        endpoint: KisEndpoint,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build authentication headers for a KIS API call.

        Token endpoint doesn't need auth headers.
        """
        headers: dict[str, str] = {
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }

        # Token endpoint doesn't need Bearer token
        if endpoint.name != "token_issue":
            token = await self._token_manager.get_token(await self._ensure_client())
            headers["authorization"] = token.authorization_header

        # TR_ID for endpoints that need it
        if endpoint.tr_id:
            headers["tr_id"] = endpoint.tr_id

        if extra:
            headers.update(extra)

        return headers

    async def get(
        self,
        endpoint_name: str,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> KisResponse:
        """Make a GET request to a KIS API endpoint.

        Args:
            endpoint_name: Name from the endpoint catalog.
            params: Query parameters.
            extra_headers: Additional headers.

        Returns:
            KisResponse with parsed data.

        Raises:
            BrokerError: On HTTP error or KIS API rejection.
            DataError: On data unavailability.
        """
        ep = get_endpoint(endpoint_name)
        client = await self._ensure_client()
        headers = await self._build_headers(ep, extra_headers)
        url = f"{self._base_url}{ep.path}"

        await self._throttle()
        async with self._rate_semaphore:
            try:
                resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                snippet = (e.response.text or "")[:300]
                logger.error(
                    f"KIS GET {endpoint_name} HTTP {status}: body={snippet}"
                )
                # P-55: 토큰 만료(EGW00123) 시 1회 재발급 후 재시도
                if "EGW00123" in snippet and status == 500:
                    logger.info(f"토큰 만료 감지 — 재발급 후 재시도: {endpoint_name}")
                    self._token_manager.invalidate()
                    headers = await self._build_headers(ep, extra_headers)
                    resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
                    resp.raise_for_status()
                    return self._parse_response(resp.json(), endpoint_name)
                raise BrokerError(
                    f"KIS HTTP error on {endpoint_name}: HTTP {status}"
                ) from e
            except httpx.TimeoutException as e:
                logger.error(f"KIS GET {endpoint_name} 타임아웃: {e}")
                raise BrokerError(f"KIS HTTP timeout on {endpoint_name}") from e
            except httpx.HTTPError as e:
                logger.error(f"KIS GET {endpoint_name} HTTP 실패: {type(e).__name__}: {e}")
                raise BrokerError(f"KIS HTTP error on {endpoint_name}: {type(e).__name__}") from e

            return self._parse_response(resp.json(), endpoint_name)

    async def post(
        self,
        endpoint_name: str,
        json_data: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> KisResponse:
        """Make a POST request to a KIS API endpoint.

        Args:
            endpoint_name: Name from the endpoint catalog.
            json_data: Request body.
            extra_headers: Additional headers (e.g. tr_id for orders).

        Returns:
            KisResponse with parsed data.

        Raises:
            BrokerError: On HTTP error or KIS API rejection.
        """
        ep = get_endpoint(endpoint_name)
        client = await self._ensure_client()
        headers = await self._build_headers(ep, extra_headers)
        headers["content-type"] = "application/json"
        url = f"{self._base_url}{ep.path}"

        await self._throttle()
        async with self._rate_semaphore:
            try:
                resp = await client.post(url, json=json_data, headers=headers, timeout=self._timeout)
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                snippet = (e.response.text or "")[:300]
                logger.error(
                    f"KIS POST {endpoint_name} HTTP {status}: body={snippet}"
                )
                # P-55: 토큰 만료(EGW00123) 시 1회 재발급 후 재시도
                if "EGW00123" in snippet and status == 500:
                    logger.info(f"토큰 만료 감지 — 재발급 후 재시도: {endpoint_name}")
                    self._token_manager.invalidate()
                    headers = await self._build_headers(ep, extra_headers)
                    headers["content-type"] = "application/json"
                    resp = await client.post(url, json=json_data, headers=headers, timeout=self._timeout)
                    resp.raise_for_status()
                    return self._parse_response(resp.json(), endpoint_name)
                raise BrokerError(
                    f"KIS HTTP error on {endpoint_name}: HTTP {status}"
                ) from e
            except httpx.TimeoutException as e:
                logger.error(f"KIS POST {endpoint_name} 타임아웃: {e}")
                raise BrokerError(f"KIS HTTP timeout on {endpoint_name}") from e
            except httpx.HTTPError as e:
                logger.error(f"KIS POST {endpoint_name} HTTP 실패: {type(e).__name__}: {e}")
                raise BrokerError(f"KIS HTTP error on {endpoint_name}: {type(e).__name__}") from e

            return self._parse_response(resp.json(), endpoint_name)

    def _parse_response(self, body: dict[str, Any], endpoint_name: str) -> KisResponse:
        """Parse and validate a KIS API response.

        KIS uses rt_cd="0" for success. Any other value is an error.
        """
        rt_cd = str(body.get("rt_cd", ""))
        msg_cd = str(body.get("msg_cd", ""))
        msg1 = str(body.get("msg1", ""))

        success = rt_cd == "0"

        if not success:
            logger.warning(
                f"KIS {endpoint_name} 거절: rt_cd={rt_cd}, msg_cd={msg_cd}, msg1={msg1}"
            )

        # Extract output data — KIS uses output/output1/output2
        data: dict[str, Any] = {}
        for key in ("output", "output1", "output2"):
            value = body.get(key)
            if isinstance(value, dict):
                data = value
                break
            elif isinstance(value, list) and value:
                data = value[0] if isinstance(value[0], dict) else {"_list": value}
                break

        if not data and success:
            data = body

        return KisResponse(
            success=success,
            data=data,
            raw=body,
            rt_cd=rt_cd,
            msg_cd=msg_cd,
            msg1=msg1,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            logger.info("KIS HTTP 클라이언트 종료")
