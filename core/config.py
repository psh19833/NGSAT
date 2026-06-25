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
class StrategyConfig:
    """전략·정책 설정 — 모든 매매 수치를 한 곳에서 관리.
    
    .env 또는 기본값으로 설정 가능. 값 변경 시 재기동 없이
    설정 파일만 수정하면 반영.
    """
    # ── 진입/청산 임계 ──
    buy_threshold: float = 0.65         # ML 예측 확률 ≥ 65% → 매수
    sell_threshold: float = 0.35        # ML 예측 확률 ≤ 35% → 매도

    # ── 레짐 판정 ──
    regime_bull_threshold: float = 65.0    # ≥ 65점 → 강세
    regime_bear_threshold: float = 35.0    # ≤ 35점 → 약세
    regime_weight_ma: float = 35.0
    regime_weight_rsi: float = 20.0
    regime_weight_bollinger: float = 20.0
    regime_weight_change_rate: float = 15.0
    regime_weight_volume: float = 10.0

    # ── 스크리너 ──
    screener_bull_min_score: float = 60.0
    screener_bull_max_candidates: int = 15
    screener_neutral_min_score: float = 70.0
    screener_neutral_max_candidates: int = 10
    screener_bear_min_score: float = 80.0
    screener_bear_max_candidates: int = 5

    # ── 모드 전환 ──
    mode_high_volatility_atr_pct: float = 1.5   # ATR ≥ 1.5% → 고변동성(단타)
    mode_low_volatility_atr_pct: float = 0.5     # ATR ≤ 0.5% → 저변동성(스윙)

    # ── 모드별 리스크 ──
    mode_swing_stop_loss_pct: float = 3.0
    mode_swing_daily_loss_pct: float = 5.0
    mode_swing_position_size: float = 0.10
    mode_short_stop_loss_pct: float = 1.5
    mode_short_daily_loss_pct: float = 3.0
    mode_short_position_size: float = 0.05
    mode_hold_stop_loss_pct: float = 3.0
    mode_hold_daily_loss_pct: float = 5.0
    mode_hold_position_size: float = 0.0


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
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
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

    # Strategy settings
    s = config.strategy
    s.buy_threshold = float(os.getenv("NGSAT_BUY_THRESHOLD", "0.65"))
    s.sell_threshold = float(os.getenv("NGSAT_SELL_THRESHOLD", "0.35"))
    s.regime_bull_threshold = float(os.getenv("NGSAT_REGIME_BULL_THRESHOLD", "65.0"))
    s.regime_bear_threshold = float(os.getenv("NGSAT_REGIME_BEAR_THRESHOLD", "35.0"))
    s.regime_weight_ma = float(os.getenv("NGSAT_REGIME_WEIGHT_MA", "35.0"))
    s.regime_weight_rsi = float(os.getenv("NGSAT_REGIME_WEIGHT_RSI", "20.0"))
    s.regime_weight_bollinger = float(os.getenv("NGSAT_REGIME_WEIGHT_BOLLINGER", "20.0"))
    s.regime_weight_change_rate = float(os.getenv("NGSAT_REGIME_WEIGHT_CHANGE_RATE", "15.0"))
    s.regime_weight_volume = float(os.getenv("NGSAT_REGIME_WEIGHT_VOLUME", "10.0"))
    s.screener_bull_min_score = float(os.getenv("NGSAT_SCREENER_BULL_MIN_SCORE", "60.0"))
    s.screener_bull_max_candidates = int(os.getenv("NGSAT_SCREENER_BULL_MAX_CANDIDATES", "15"))
    s.screener_neutral_min_score = float(os.getenv("NGSAT_SCREENER_NEUTRAL_MIN_SCORE", "70.0"))
    s.screener_neutral_max_candidates = int(os.getenv("NGSAT_SCREENER_NEUTRAL_MAX_CANDIDATES", "10"))
    s.screener_bear_min_score = float(os.getenv("NGSAT_SCREENER_BEAR_MIN_SCORE", "80.0"))
    s.screener_bear_max_candidates = int(os.getenv("NGSAT_SCREENER_BEAR_MAX_CANDIDATES", "5"))
    s.mode_high_volatility_atr_pct = float(os.getenv("NGSAT_MODE_HIGH_VOL_ATR_PCT", "1.5"))
    s.mode_low_volatility_atr_pct = float(os.getenv("NGSAT_MODE_LOW_VOL_ATR_PCT", "0.5"))
    s.mode_swing_stop_loss_pct = float(os.getenv("NGSAT_MODE_SWING_STOP_LOSS", "3.0"))
    s.mode_swing_daily_loss_pct = float(os.getenv("NGSAT_MODE_SWING_DAILY_LOSS", "5.0"))
    s.mode_swing_position_size = float(os.getenv("NGSAT_MODE_SWING_POSITION_SIZE", "0.10"))
    s.mode_short_stop_loss_pct = float(os.getenv("NGSAT_MODE_SHORT_STOP_LOSS", "1.5"))
    s.mode_short_daily_loss_pct = float(os.getenv("NGSAT_MODE_SHORT_DAILY_LOSS", "3.0"))
    s.mode_short_position_size = float(os.getenv("NGSAT_MODE_SHORT_POSITION_SIZE", "0.05"))
    s.mode_hold_stop_loss_pct = float(os.getenv("NGSAT_MODE_HOLD_STOP_LOSS", "3.0"))
    s.mode_hold_daily_loss_pct = float(os.getenv("NGSAT_MODE_HOLD_DAILY_LOSS", "5.0"))
    s.mode_hold_position_size = float(os.getenv("NGSAT_MODE_HOLD_POSITION_SIZE", "0.0"))

    # Telegram
    config.telegram.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    return config
