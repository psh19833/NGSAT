"""NGSAT core configuration module.

Loads environment variables from .env and provides
typed access to all system configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


class Environment(str, Enum):
    """Runtime environment."""
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    BACKTEST = "backtest"


class MarketRegime(str, Enum):
    """Market regime classification."""
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"


class OrderSide(str, Enum):
    """Order direction."""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    """Order lifecycle status."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class DatabaseConfig:
    """Database connection configuration."""
    url: str = "postgresql://localhost:5432/ngsat"
    pool_size: int = 10
    max_overflow: int = 20
    echo: bool = False


@dataclass
class KISConfig:
    """KIS (Korea Investment) API configuration.
    
    All secrets are loaded from .env — never hardcoded.
    """
    base_url: str = ""
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    account_product_code: str = ""

    @property
    def is_configured(self) -> bool:
        """Check if all required credentials are present."""
        return bool(self.base_url and self.app_key and self.app_secret)


@dataclass
class RiskConfig:
    """Risk management configuration."""
    daily_loss_limit_pct: float = 5.0       # 일일 총손실 한도 (%)
    default_stop_loss_pct: float = 3.0      # 종목별 기본 손절선 (%)
    max_stop_loss_pct: float = 5.0          # 종목별 최대 손절선 (%)
    kospi_weight: float = 0.7               # 코스피 선호 비중
    kosdaq_weight: float = 0.3              # 코스닥 비중


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    bot_token: str = ""
    chat_id: str = ""

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)


@dataclass
class Config:
    """Master configuration object."""
    env: Environment = Environment.DEVELOPMENT
    debug: bool = True
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    kis: KISConfig = field(default_factory=KISConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)


def load_config(env_file: str | None = None) -> Config:
    """Load configuration from environment variables.
    
    Args:
        env_file: Path to .env file. Defaults to PROJECT_ROOT/.env
    
    Returns:
        Config object with all settings populated.
    """
    if load_dotenv is not None:
        env_path = env_file or str(PROJECT_ROOT / ".env")
        load_dotenv(env_path)

    config = Config()
    
    # Environment
    env_str = os.getenv("NGSAT_ENV", "development")
    config.env = Environment(env_str)
    config.debug = os.getenv("NGSAT_DEBUG", "true").lower() == "true"

    # Database
    config.database.url = os.getenv(
        "NGSAT_DB_URL",
        "postgresql://localhost:5432/ngsat"
    )
    config.database.echo = os.getenv("NGSAT_DB_ECHO", "false").lower() == "true"

    # KIS API (secrets — from .env only)
    config.kis.base_url = os.getenv("KIS_BASE_URL", "")
    config.kis.app_key = os.getenv("KIS_APP_KEY", "")
    config.kis.app_secret = os.getenv("KIS_APP_SECRET", "")
    config.kis.account_no = os.getenv("KIS_ACCOUNT_NO", "")
    config.kis.account_product_code = os.getenv("KIS_ACNT_PRDT_CD", "")

    # Risk management
    config.risk.daily_loss_limit_pct = float(
        os.getenv("NGSAT_DAILY_LOSS_LIMIT", "5.0")
    )
    config.risk.default_stop_loss_pct = float(
        os.getenv("NGSAT_DEFAULT_STOP_LOSS", "3.0")
    )
    config.risk.max_stop_loss_pct = float(
        os.getenv("NGSAT_MAX_STOP_LOSS", "5.0")
    )

    # Telegram
    config.telegram.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    return config
