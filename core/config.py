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
    import warnings
    warnings.warn(
        "python-dotenv 미설치 — .env 파일이 로드되지 않습니다. "
        "환경변수가 직접 설정된 경우에만 정상 동작합니다. "
        "설치: pip install python-dotenv"
    )


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
    mode_short_stop_loss_pct: float = 1.5   # 단타 손절선 (%)
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
    regime_weight_ma: float = 30.0
    regime_weight_rsi: float = 20.0
    regime_weight_bollinger: float = 20.0
    regime_weight_change_rate: float = 15.0
    regime_weight_volume: float = 10.0
    regime_weight_adx: float = 5.0

    # ── 스크리너 ──
    screener_bull_min_score: float = 60.0
    screener_bull_max_candidates: int = 15
    screener_neutral_min_score: float = 35.0
    screener_neutral_max_candidates: int = 10
    screener_bear_min_score: float = 50.0
    screener_bear_max_candidates: int = 8

    # ── ML 학습 ──
    ml_model_type: str = "gradient_boosting"  # logistic/random_forest/gradient_boosting/xgboost/lightgbm
    ml_training_days: int = 250               # 학습 기간 (일), KIS API 조회 기간
    ml_training_start_date: str | None = None  # 과거 데이터 학습 시작일 (YYYY-MM-DD), 설정 시 days 무시
    ml_training_end_date: str | None = None    # 과거 데이터 학습 종료일 (YYYY-MM-DD)
    ml_auto_select_model: bool = False        # True: 5개 모델 전부 학습 후 최고 AUC로 자동 교체
    ml_auto_retrain: bool = False          # True: 매일 장 마감 후 자동 재학습
    ml_minute_auto_retrain: bool = False   # True: 장 마감 후 분봉ML(단타)도 자동 재학습
    ml_swing_forward_days: int = 3        # 스윙: N일 뒤 +2% 예측
    ml_forward_threshold: float = 0.02    # ML 일봉 양성 판정 임계 수익률 (2%)
    ml_short_forward_minutes: int = 60    # 단타: N분 뒤 +1.0% 예측 (분봉 ML threshold=0.01)
    ml_minute_forward_threshold: float = 0.01  # ML 분봉 양성 판정 임계 수익률 (1%)

    # ── 모드 전환 ──
    mode_high_volatility_atr_pct: float = 1.5   # ATR ≥ 1.5% → 고변동성(단타)
    mode_low_volatility_atr_pct: float = 0.5     # ATR ≤ 0.5% → 저변동성(스윙)

    # ── 모드별 리스크 ──
    mode_swing_stop_loss_pct: float = 3.0
    mode_swing_daily_loss_pct: float = 5.0
    mode_swing_position_size: float = 0.10
    mode_short_stop_loss_pct: float = 1.5   # 단타 손절선 (%)
    mode_short_daily_loss_pct: float = 3.0
    mode_short_position_size: float = 0.05
    mode_hold_stop_loss_pct: float = 3.0
    mode_hold_daily_loss_pct: float = 5.0
    mode_hold_position_size: float = 0.0

    # ── 포트폴리오 리스크 ──
    max_holdings: int = 10                # 최대 보유 종목 수 (0=제한 없음)
    max_sector_concentration: int = 3     # 동일 업종 최대 보유 수 (TR-5)
    kospi_bonus_score: float = 5.0        # KOSPI 가산점 (TR-8)
    kosdaq_bonus_score: float = 0.0       # KOSDAQ 가산점 (TR-8)
    daily_trade_limit: int = 20           # 일일 최대 거래 횟수 (TR-13)
    max_total_exposure_pct: float = 50.0  # 총 노출 한도 (자산 대비 %, TR-14)

    # ── 트레일링 스탑 (P1-1) ──
    trailing_stop_enabled: bool = True        # True=트레일링 스탑 활성 (ATR × 2.0, 수익 +1%부터)
    trailing_stop_atr_multiplier: float = 2.0 # ATR × N = 트레일링 폭
    trailing_stop_activate_pct: float = 1.0   # 수익 +N%부터 트레일링 스탑 활성화

    # ── 부분 청산 (P1-2) ──
    partial_tp_enabled: bool = False       # False=전량 매도, True=분할 익절
    partial_tp1_pct: float = 3.0           # 1차 익절 수익률 (%)
    partial_tp1_ratio: float = 0.5         # 1차 매도 비율 (50%)
    partial_tp2_pct: float = 6.0           # 2차 익절 수익률 (%)
    partial_tp2_ratio: float = 0.3         # 2차 매도 비율 (30%)


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


from functools import lru_cache


@lru_cache(maxsize=1)
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
    s.regime_weight_ma = float(os.getenv("NGSAT_REGIME_WEIGHT_MA", "30.0"))
    s.regime_weight_rsi = float(os.getenv("NGSAT_REGIME_WEIGHT_RSI", "20.0"))
    s.regime_weight_bollinger = float(os.getenv("NGSAT_REGIME_WEIGHT_BOLLINGER", "20.0"))
    s.regime_weight_change_rate = float(os.getenv("NGSAT_REGIME_WEIGHT_CHANGE_RATE", "15.0"))
    s.regime_weight_volume = float(os.getenv("NGSAT_REGIME_WEIGHT_VOLUME", "10.0"))
    s.regime_weight_adx = float(os.getenv("NGSAT_REGIME_WEIGHT_ADX", "5.0"))
    s.screener_bull_min_score = float(os.getenv("NGSAT_SCREENER_BULL_MIN_SCORE", "60.0"))
    s.screener_bull_max_candidates = int(os.getenv("NGSAT_SCREENER_BULL_MAX_CANDIDATES", "15"))
    s.screener_neutral_min_score = float(os.getenv("NGSAT_SCREENER_NEUTRAL_MIN_SCORE", "30.0"))
    s.screener_neutral_max_candidates = int(os.getenv("NGSAT_SCREENER_NEUTRAL_MAX_CANDIDATES", "10"))
    s.screener_bear_min_score = float(os.getenv("NGSAT_SCREENER_BEAR_MIN_SCORE", "50.0"))
    s.screener_bear_max_candidates = int(os.getenv("NGSAT_SCREENER_BEAR_MAX_CANDIDATES", "8"))
    s.mode_high_volatility_atr_pct = float(os.getenv("NGSAT_MODE_HIGH_VOL_ATR_PCT", "1.5"))
    s.mode_low_volatility_atr_pct = float(os.getenv("NGSAT_MODE_LOW_VOL_ATR_PCT", "0.5"))
    s.ml_model_type = os.getenv("NGSAT_ML_MODEL_TYPE", "gradient_boosting")
    s.ml_training_days = int(os.getenv("NGSAT_ML_TRAINING_DAYS", "250"))
    s.ml_training_start_date = os.getenv("NGSAT_ML_TRAINING_START_DATE", None)
    s.ml_training_end_date = os.getenv("NGSAT_ML_TRAINING_END_DATE", None)
    s.ml_auto_retrain = os.getenv("NGSAT_ML_AUTO_RETRAIN", "false").lower() == "true"
    s.ml_minute_auto_retrain = os.getenv("NGSAT_ML_MINUTE_AUTO_RETRAIN", "false").lower() == "true"
    s.ml_auto_select_model = os.getenv("NGSAT_ML_AUTO_SELECT_MODEL", "false").lower() == "true"
    s.ml_swing_forward_days = int(os.getenv("NGSAT_ML_SWING_FORWARD_DAYS", "3"))
    s.ml_short_forward_minutes = int(os.getenv("NGSAT_ML_SHORT_FORWARD_MINUTES", "60"))
    s.mode_swing_stop_loss_pct = float(os.getenv("NGSAT_MODE_SWING_STOP_LOSS", "3.0"))
    s.mode_swing_daily_loss_pct = float(os.getenv("NGSAT_MODE_SWING_DAILY_LOSS", "5.0"))
    s.mode_swing_position_size = float(os.getenv("NGSAT_MODE_SWING_POSITION_SIZE", "0.10"))
    s.mode_short_stop_loss_pct = float(os.getenv("NGSAT_MODE_SHORT_STOP_LOSS", "1.0"))
    s.mode_short_daily_loss_pct = float(os.getenv("NGSAT_MODE_SHORT_DAILY_LOSS", "3.0"))
    s.mode_short_position_size = float(os.getenv("NGSAT_MODE_SHORT_POSITION_SIZE", "0.05"))
    s.mode_hold_stop_loss_pct = float(os.getenv("NGSAT_MODE_HOLD_STOP_LOSS", "3.0"))
    s.mode_hold_daily_loss_pct = float(os.getenv("NGSAT_MODE_HOLD_DAILY_LOSS", "5.0"))
    s.mode_hold_position_size = float(os.getenv("NGSAT_MODE_HOLD_POSITION_SIZE", "0.0"))

    # Portfolio risk
    s.max_holdings = int(os.getenv("NGSAT_MAX_HOLDINGS", "10"))

    # Trailing stop (P1-1)
    s.trailing_stop_enabled = os.getenv("NGSAT_TRAILING_STOP_ENABLED", "false").lower() == "true"
    s.trailing_stop_atr_multiplier = float(os.getenv("NGSAT_TRAILING_STOP_ATR_MULTIPLIER", "2.0"))
    s.trailing_stop_activate_pct = float(os.getenv("NGSAT_TRAILING_STOP_ACTIVATE_PCT", "1.0"))

    # Partial take-profit (P1-2)
    s.partial_tp_enabled = os.getenv("NGSAT_PARTIAL_TP_ENABLED", "false").lower() == "true"
    s.partial_tp1_pct = float(os.getenv("NGSAT_PARTIAL_TP1_PCT", "3.0"))
    s.partial_tp1_ratio = float(os.getenv("NGSAT_PARTIAL_TP1_RATIO", "0.5"))
    s.partial_tp2_pct = float(os.getenv("NGSAT_PARTIAL_TP2_PCT", "6.0"))
    s.partial_tp2_ratio = float(os.getenv("NGSAT_PARTIAL_TP2_RATIO", "0.3"))

    # Telegram
    config.telegram.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    return config
