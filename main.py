"""NGSAT main entry point — integrates and starts all system components.

Wires together:
  - KIS Broker Adapter (data layer)
  - ML Model (loaded from disk or trained)
  - Trading Orchestrator (live trading cycle)
  - Dashboard API (FastAPI server)
  - Telegram Bot (notifications + remote control)

Usage:
  python main.py                    # Start with default config
  python main.py --train            # Train model before starting
  python main.py --backtest         # Run backtest only
  python main.py --no-dashboard     # Start without dashboard
  python main.py --no-telegram      # Start without telegram bot
"""

from __future__ import annotations

import asyncio
import argparse
import signal
import sys
from pathlib import Path
from typing import Any

from core.config import load_config
from core.logger import logger, setup_logger


def parse_args():
    parser = argparse.ArgumentParser(
        description="NGSAT — New Generation Stock Auto Trader",
    )
    parser.add_argument(
        "--train", action="store_true",
        help="ML 모델 학습 후 시작",
    )
    parser.add_argument(
        "--backtest", action="store_true",
        help="백테스트만 실행 (실거래 없음)",
    )
    parser.add_argument(
        "--no-dashboard", action="store_true",
        help="대시보드 API 서버 비활성화",
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="텔레그램 봇 비활성화",
    )
    parser.add_argument(
        "--model-path", type=str, default="",
        help="ML 모델 파일 경로 (기본: models/trained/price_rise_model.pkl)",
    )
    parser.add_argument(
        "--tick-interval", type=int, default=10,
        help="매매 사이클 주기 (초, 기본: 10)",
    )
    return parser.parse_args()


async def run_backtest(config):
    """Run a backtest — attempts real KIS data first, falls back to synthetic."""
    from backtest.data_loader import generate_synthetic_index, generate_synthetic_universe
    from backtest.engine import BacktestEngine
    from backtest.report import generate_report, print_report
    from ml.training.trainer import train_from_price_data

    logger.info("=== NGSAT 백테스트 모드 ===")

    # ── Try KIS real data first ──
    universe = []
    index_prices = []
    try:
        from data.real_data_provider import RealDataProvider
        logger.info("KIS 실데이터 로드 시도...")
        provider = RealDataProvider()
        universe, index_prices = await provider.load()
        if universe:
            logger.info(f"KIS 실데이터 로드 성공: {len(universe)}종목, 지수 {len(index_prices)}일")
        else:
            logger.warning("KIS 데이터 없음 — 합성 데이터로 대체")
    except Exception as e:
        logger.warning(f"KIS 데이터 로드 실패: {type(e).__name__}: {e} — 합성 데이터로 대체")

    # ── Fallback to synthetic ──
    if not universe:
        logger.info("합성 데이터 생성 중...")
        universe = generate_synthetic_universe(n_stocks=20, n_days=250, seed=42)
        index_prices = generate_synthetic_index(n_days=250, seed=100)

    all_prices = [prices for _, prices in universe]
    codes = [info.code for info, _ in universe]

    # Train model
    logger.info("ML 모델 학습 중...")
    model, train_result = train_from_price_data(
        all_prices, codes,
        model_type=config.strategy.ml_model_type,
        forward_days=config.strategy.ml_swing_forward_days,
        forward_threshold=0.02,
    )
    logger.info(train_result.reason)

    # Run backtest with strategy config
    logger.info("백테스트 실행 중...")
    from strategy.regime import init_regime_config
    from strategy.screener import init_screener_config
    from strategy.mode_selector import init_mode_selector_config
    init_regime_config(config.strategy)
    init_screener_config(config.strategy)
    init_mode_selector_config(config.strategy)
    engine = BacktestEngine(
        model,
        initial_capital=10_000_000,
        buy_threshold=config.strategy.buy_threshold,
        sell_threshold=config.strategy.sell_threshold,
    )
    # start_day: last ~1 month of the shortest stock's data
    if universe:
        min_stock_days = min(len(prices) for _, prices in universe)
        start_day = max(20, min_stock_days - 22)  # ~1 month
    else:
        start_day = 60
    result = engine.run(universe, index_prices, start_day=start_day)

    # Generate and print report
    report = generate_report(result)
    print()
    print_report(report)

    return result


async def run_live(config, args):
    """Start live trading system with all components."""
    from data.adapters.kis.adapter import KisAdapter
    from dashboard.backend.api import create_app
    from live.orchestrator import TradingOrchestrator
    from ml.training.trainer import PriceRiseModel
    from messaging.bot import TelegramBot

    logger.info("=== NGSAT 실거래 모드 시작 ===")

    # ── 1. Load ML model ──
    model_path = args.model_path or str(
        Path(__file__).resolve().parent / "models" / "trained" / "price_rise_model.pkl"
    )

    try:
        model = PriceRiseModel.load(model_path)
        logger.info(f"ML 모델 로드: {model_path}")
    except Exception:
        logger.warning(f"저장된 모델 없음 ({model_path}). 모델 학습이 필요합니다.")
        logger.warning("python main.py --train 으로 모델을 먼저 학습하세요.")
        return

    # ── 2. Create KIS adapter ──
    if not config.kis.is_configured:
        logger.error("KIS API 설정이 없습니다. .env 파일을 확인하세요.")
        return

    broker = KisAdapter(
        app_key=config.kis.app_key,
        app_secret=config.kis.app_secret,
        base_url=config.kis.base_url,
        account_no=config.kis.account_no,
        account_product_code=config.kis.account_product_code,
    )
    logger.info("KIS 어댑터 연결 완료")

    # ── 3. Create orchestrator ──
    orchestrator = TradingOrchestrator(
        broker=broker,
        model=model,
        risk_config=config.risk,
        strategy_config=config.strategy,
        buy_threshold=config.strategy.buy_threshold,
        sell_threshold=config.strategy.sell_threshold,
        db_url=config.database.url,
        db_pool_size=config.database.pool_size,
        db_max_overflow=config.database.max_overflow,
    )
    logger.info("오케스트레이터 초기화 완료")

    # Inject strategy config into module-level globals (screener, mode_selector)
    from strategy.screener import init_screener_config
    from strategy.mode_selector import init_mode_selector_config
    init_screener_config(config.strategy)
    init_mode_selector_config(config.strategy)
    logger.info("전략 설정 주입 완료 (스크리너 + 모드선택기)")

    # ── 4. Telegram bot (optional) ──
    telegram_bot = None
    if not args.no_telegram and config.telegram.is_configured:
        telegram_bot = TelegramBot(
            bot_token=config.telegram.bot_token,
            chat_id=config.telegram.chat_id,
        )
        telegram_bot.set_orchestrator(orchestrator)
        await telegram_bot.send_system_event("start", "NGSAT 시스템 기동")
        logger.info("텔레그램 봇 연결 완료")
    elif not args.no_telegram:
        logger.info("텔레그램 미설정 — 봇 비활성화")

    # ── 5. Dashboard API (optional) ──
    dashboard_app = None
    api_server = None
    if not args.no_dashboard:
        import uvicorn
        dashboard_app = create_app(orchestrator, config)
        logger.info("대시보드 API 준비 완료 (포트 8000)")

    # ── 6. Start trading loop ──
    tick_interval = args.tick_interval
    logger.info(f"매매 사이클 주기: {tick_interval}초")

    async def trading_loop():
        """Main trading loop — runs orchestrator cycle with real KIS data."""
        from data.real_data_provider import RealDataProvider

        data_provider = RealDataProvider()
        universe, index_prices = await data_provider.load()

        if not universe:
            logger.error("실데이터 로드 실패 — 시스템 중단")
            return

        logger.info(f"KIS 실데이터 연결 완료: {len(universe)}종목, 지수 {len(index_prices)}일")

        while True:
            try:
                # Refresh latest price data each cycle
                universe, index_prices = await data_provider.refresh_prices()

                if orchestrator.controller.is_running:
                    result = await orchestrator.run_cycle(index_prices, universe)

                    # Send telegram notification for trades
                    if telegram_bot and (result.buys_executed > 0 or result.sells_executed > 0):
                        await telegram_bot.send_system_event(
                            "info",
                            result.reason,
                        )

                await asyncio.sleep(tick_interval)

            except asyncio.CancelledError:
                logger.info("매매 루프 종료")
                break
            except Exception as e:
                logger.error(f"매매 루프 오류: {type(e).__name__}: {e}")
                if telegram_bot:
                    await telegram_bot.send_system_event("error", str(e))
                await asyncio.sleep(tick_interval)

    # ── 7. Start everything ──
    tasks: list[asyncio.Task] = []

    # Trading loop
    tasks.append(asyncio.create_task(trading_loop()))

    # Dashboard API server
    # Telegram command polling
    if telegram_bot:
        tasks.append(asyncio.create_task(telegram_bot.start_polling()))

    if dashboard_app:
        config_uvicorn = uvicorn.Config(
            dashboard_app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
        )
        api_server = uvicorn.Server(config_uvicorn)
        tasks.append(asyncio.create_task(api_server.serve()))

    logger.info("NGSAT 시스템 기동 완료")
    logger.info("  - 매매 사이클: {}초 주기".format(tick_interval))
    if dashboard_app:
        logger.info("  - 대시보드: http://localhost:8000")
    if telegram_bot:
        logger.info("  - 텔레그램: 활성화")
    logger.info("  - 제어: 대시보드 또는 텔레그램 /start 로 매매 시작")

    # ── 8. Wait for shutdown signal ──
    stop_event = asyncio.Event()

    def signal_handler(sig, frame):
        logger.info(f"종료 신호 수신: {sig}")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        await stop_event.wait()
    finally:
        # Cleanup
        logger.info("NGSAT 종료 중...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        if telegram_bot:
            await telegram_bot.send_system_event("stop", "NGSAT 시스템 종료")

        await broker.close()
        logger.info("NGSAT 종료 완료")


async def train_model(config):
    """Train ML model and save to disk."""
    from backtest.data_loader import generate_synthetic_universe
    from ml.training.trainer import train_from_price_data

    logger.info("=== NGSAT ML 모델 학습 ===")
    logger.info("주의: 합성 데이터로 학습합니다. 실제 데이터 연결은 다음 업데이트입니다.")

    universe = generate_synthetic_universe(n_stocks=30, n_days=200, seed=42)
    all_prices = [prices for _, prices in universe]
    codes = [info.code for info, _ in universe]

    model, result = train_from_price_data(
        all_prices, codes,
        model_type=config.strategy.ml_model_type,
        forward_days=config.strategy.ml_swing_forward_days,
        forward_threshold=0.02,
    )

    if result.success:
        save_path = model.save()
        logger.info(f"모델 저장 완료: {save_path}")
        logger.info(result.reason)
    else:
        logger.error(f"학습 실패: {result.reason}")


def main():
    args = parse_args()
    config = load_config()

    setup_logger("ngsat", level=20, log_file="logs/ngsat.log")  # INFO + file

    logger.info("NGSAT — New Generation Stock Auto Trader")
    logger.info(f"환경: {config.env.value}")

    if args.backtest:
        asyncio.run(run_backtest(config))
    elif args.train:
        asyncio.run(train_model(config))
    else:
        asyncio.run(run_live(config, args))


if __name__ == "__main__":
    main()
