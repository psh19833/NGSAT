"""KIS OAuth token manager.

Issues, caches, and refreshes KIS API access tokens.
Token plaintext is never exposed in repr/log.

Reference: KIS OAuth2 password-grant flow
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from core.exceptions import BrokerError
from core.logger import logger


@dataclass(frozen=True)
class KisToken:
    """KIS API access token.
    
    Attributes:
        access_token: JWT access token (never logged).
        token_type: Always "Bearer".
        expires_in: Token lifetime in seconds.
        issued_at: Token issue time (UTC).
    """
    access_token: str = field(repr=False)  # Never expose in repr
    token_type: str
    expires_in: int
    issued_at: datetime

    @property
    def expires_at(self) -> datetime:
        """Token expiry time (UTC)."""
        return self.issued_at + timedelta(seconds=self.expires_in)

    @property
    def is_expired(self) -> bool:
        """Check if token is expired (with 5-minute safety margin)."""
        margin = timedelta(minutes=5)
        return datetime.now(timezone.utc) >= (self.expires_at - margin)

    @property
    def authorization_header(self) -> str:
        """Ready-to-use Authorization header value."""
        return f"{self.token_type} {self.access_token}"


class KisTokenManager:
    """Manages KIS OAuth token lifecycle.
    
    - Issues new tokens via password-grant flow
    - Caches token in memory
    - Auto-refreshes when expired
    - Never logs token plaintext
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        base_url: str,
    ):
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._cached_token: KisToken | None = None

    @property
    def is_configured(self) -> bool:
        """Check if credentials are present."""
        return bool(self._app_key and self._app_secret and self._base_url)

    async def get_token(self, http_client: Any) -> KisToken:
        """Get a valid token, issuing or refreshing as needed.
        
        Args:
            http_client: httpx.AsyncClient instance for making HTTP calls.
        
        Returns:
            Valid KisToken.
        
        Raises:
            BrokerError: If token issuance fails.
        """
        if self._cached_token and not self._cached_token.is_expired:
            return self._cached_token

        return await self._issue_token(http_client)

    async def _issue_token(self, http_client: Any) -> KisToken:
        """Issue a new access token from KIS OAuth endpoint.
        
        POST /oauth2/tokenP
        Body: { "grant_type": "client_credentials", "appkey": ..., "appsecret": ... }
        """
        from data.adapters.kis.endpoints import get_endpoint

        ep = get_endpoint("token_issue")
        url = f"{self._base_url}{ep.path}"

        payload = {
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        }

        logger.info("KIS 토큰 발급 요청")

        try:
            resp = await http_client.post(url, json=payload, timeout=30.0)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"KIS 토큰 발급 실패: {type(e).__name__}")
            raise BrokerError(f"KIS token issuance failed: {type(e).__name__}") from e

        data = resp.json()

        if "access_token" not in data:
            msg_cd = data.get("msg_cd", "unknown")
            msg1 = data.get("msg1", "unknown")
            logger.error(f"KIS 토큰 발급 거부: msg_cd={msg_cd}, msg1={msg1}")
            raise BrokerError(f"KIS token rejected: {msg_cd} {msg1}")

        token = KisToken(
            access_token=data["access_token"],
            token_type=data.get("token_type", "Bearer"),
            expires_in=int(data.get("expires_in", 86400)),
            issued_at=datetime.now(timezone.utc),
        )

        self._cached_token = token
        logger.info(f"KIS 토큰 발급 성공 (유효 {token.expires_in}초)")
        return token

    def clear_cache(self) -> None:
        """Clear cached token (force re-issue on next call)."""
        self._cached_token = None
        logger.info("KIS 토큰 캐시 삭제")
