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
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
    training_days = config.strategy.ml_training_days
    try:
        from data.real_data_provider import RealDataProvider
        logger.info(f"KIS 실데이터 로드 시도 (기간: {training_days}일)...")
        provider = RealDataProvider(
            training_days=training_days,
            start_date=config.strategy.ml_training_start_date,
            end_date=config.strategy.ml_training_end_date,
        )
        universe, index_prices = await provider.load()
        if universe:
            logger.info(f"KIS 실데이터 로드 성공: {len(universe)}종목, 지수 {len(index_prices)}일")
        else:
            logger.warning("KIS 데이터 없음 — 합성 데이터로 대체")
    except Exception as e:
        logger.warning(f"KIS 데이터 로드 실패: {type(e).__name__}: {e} — 합성 데이터로 대체")

    # ── Fallback to synthetic ──
    if not universe:
        training_days = config.strategy.ml_training_days
        logger.info(f"합성 데이터 생성 중... (기간: {training_days}일)")
        universe = generate_synthetic_universe(n_stocks=20, n_days=training_days, seed=42)
        index_prices = generate_synthetic_index(n_days=training_days, seed=100)

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
    from backtest.engine import BacktestEngine
    engine = BacktestEngine(
        model,
        initial_capital=10_000_000,
        buy_threshold=config.strategy.buy_threshold,
        sell_threshold=config.strategy.sell_threshold,
        strategy_config=config.strategy,
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
        model.auto_select_model = config.strategy.ml_auto_select_model
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
        position_budget_pct=config.strategy.mode_swing_position_size,
        db_url=config.database.url,
        db_pool_size=config.database.pool_size,
        db_max_overflow=config.database.max_overflow,
    )
    logger.info("오케스트레이터 초기화 완료")

    # ── 3A. Restart recovery: sync positions from broker ──
    try:
        positions = await orchestrator._fetch_positions()
        if positions:
            logger.info(
                f"재시작 복구: KIS 계좌에서 {len(positions)}개 포지션 감지됨 — "
                + ", ".join(f"{p.name}({p.code}) {p.quantity}주" for p in positions[:5])
            )
        else:
            logger.info("재시작 복구: 보유 포지션 없음 — 신규 시작")
    except Exception as e:
        logger.warning(f"재시작 복구: 포지션 조회 실패 — {type(e).__name__}: {e}")

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
    import uvicorn  # imported here for scope; no_dashboard skips config below
    dashboard_app = None
    api_server = None
    if not args.no_dashboard:
        dashboard_app = create_app(orchestrator, config)
        logger.info("대시보드 API 준비 완료 (포트 8000)")

    # ── 6. Start trading loop ──
    tick_interval = args.tick_interval
    logger.info(f"매매 사이클 주기: {tick_interval}초")

    async def trading_loop():
        """Main trading loop — runs orchestrator cycle with real KIS data."""
        from data.real_data_provider import RealDataProvider
        from live.session_tracker import MarketSessionTracker

        data_provider = RealDataProvider(
            training_days=config.strategy.ml_training_days,
            start_date=config.strategy.ml_training_start_date,
            end_date=config.strategy.ml_training_end_date,
        )
        universe, index_prices = await data_provider.load()

        if not universe:
            logger.error("실데이터 로드 실패 — 시스템 중단")
            return

        logger.info(f"KIS 실데이터 연결 완료: {len(universe)}종목, 지수 {len(index_prices)}일")

        # Store references for manual retrain API
        if dashboard_app:
            dashboard_app.state.data_provider = data_provider
            dashboard_app.state.model = model
            dashboard_app.state.latest_universe = universe
            dashboard_app.state.latest_index_prices = index_prices

        _refresh_counter = 1  # Start at 1 so first % 30 check happens at cycle 30 // CHANGED TO 300 (50min)
        _consecutive_errors = 0
        _last_retrain_date = ""  # yyyy-mm-dd tracking for daily retrain
        session_tracker = MarketSessionTracker()

        def _is_after_market_close_kst() -> bool:
            """KST 기준 장 마감 여부 (15:30 이후)."""
            kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
            return kst_now.hour > 15 or (kst_now.hour == 15 and kst_now.minute >= 30)

        while True:
            try:
                # ── Market session tracking (장 시작/종료 감지) ──
                from core.types import is_market_hours
                session_result = session_tracker.update(is_market_hours())
                if session_result.get("changed"):
                    if telegram_bot:
                        state = session_result["state"]
                        if state == "open":
                            # 장 시작 — 계좌/레짐 정보 포함
                            try:
                                account = await orchestrator._broker.get_account_summary()
                                regime_info = f"레짐: {orchestrator._last_regime.regime.value}({orchestrator._last_regime.score:.0f}점)" if orchestrator._last_regime else "레짐: 미평가"
                                msg = (
                                    f"🌅 장 시작 (09:00)\n"
                                    f"──────────\n"
                                    f"{regime_info}\n"
                                    f"모드: {orchestrator._current_mode}\n"
                                    f"예수금: {account.deposit:,.0f}원\n"
                                    f"보유: {len(orchestrator._last_positions) if hasattr(orchestrator, '_last_positions') else '?'}개 종목"
                                )
                            except Exception:
                                msg = "🌅 장 시작 (09:00)"
                            await telegram_bot.send_system_event("market_open", msg)
                        else:
                            await telegram_bot.send_system_event("market_close", "🌇 장 종료 (15:30)")

                # ── 일일 보고서 전송 (장 마감 감지 후 1회) ──
                if telegram_bot and session_tracker.should_send_daily_report:
                    try:
                        from data.repository import TradeRepository
                        from core.models import TradeRecord
                        from sqlalchemy.orm import Session as SASession
                        from core.db import SessionLocal
                        db_session: SASession = SessionLocal()
                        try:
                            trade_repo = TradeRepository(db_session)
                            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                            trades = trade_repo.get_trades_by_date(today)
                            buy_count = sum(1 for t in trades if t.side == "buy")
                            sell_count = sum(1 for t in trades if t.side == "sell")
                            total_trades = len(trades)
                            account = await orchestrator._broker.get_account_summary()
                            positions = await orchestrator._broker.get_positions()
                            pos_summary = "\n".join(
                                f"{p.name} {p.quantity}주 ({p.profit_loss_pct:+.1f}%)"
                                for p in positions[:10]
                            ) if positions else "없음"
                            await telegram_bot.send_daily_report(
                                date=today,
                                total_trades=total_trades,
                                buy_count=buy_count,
                                sell_count=sell_count,
                                total_pnl=account.total_profit_loss,
                                win_rate=0.0,  # 별도 계산 로직 필요시 추가
                                current_capital=account.total_asset,
                                positions_summary=pos_summary,
                            )
                        finally:
                            db_session.close()
                    except Exception as e:
                        logger.error(f"일일 보고서 전송 실패: {e}")
                    session_tracker.mark_daily_report_sent()

                # 일봉 데이터는 장 마감 후에만 변경되므로 300사이클(50분) 간격으로 충분
                _refresh_counter += 1
                if _refresh_counter == 2 or _refresh_counter % 300 == 0:
                    # 2번째 사이클에서 최초 1회 즉시 refresh, 이후 50분마다
                    universe, index_prices = await data_provider.refresh_prices()
                    # Update app.state for manual retrain API
                    if dashboard_app:
                        dashboard_app.state.latest_universe = universe
                        dashboard_app.state.latest_index_prices = index_prices
                    # ── Auto daily retrain ──
                    if config.strategy.ml_auto_retrain and model:
                        _today_kst_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        if (_last_retrain_date != _today_kst_str
                                and _is_after_market_close_kst()):
                            _last_retrain_date = _today_kst_str
                            codes = [info.code for info, _ in universe]
                            prices_list = [prices for _, prices in universe]  # list[list[PriceData]]
                            logger.info(f"자동 재학습 시작: {len(codes)}개 종목")
                            try:
                                changed, result = model.auto_retrain(prices_list, codes)
                                if changed:
                                    model.save()
                                    logger.info(f"자동 재학습 완료: AUC={result.auc:.3f}")
                                    if dashboard_app:
                                        dashboard_app.state.model = model
                            except Exception as e:
                                logger.exception(f"자동 재학습 실패: {e}")

                if orchestrator.controller.is_running:
                    result = await orchestrator.run_cycle(index_prices, universe)

                    # 자동 프리셋 변경 시 텔레그램 알림
                    if telegram_bot and result.preset_change:
                        await telegram_bot.send_system_event(
                            "info",
                            f"🔄 프리셋 자동 전환: {result.preset_change}",
                        )
                    # Send telegram notification for trades
                    if telegram_bot and (result.buys_executed > 0 or result.sells_executed > 0):
                        await telegram_bot.send_system_event(
                            "info",
                            result.reason,
                        )

                    # Broadcast cycle result to WebSocket dashboard clients
                    if dashboard_app and hasattr(dashboard_app.state, 'broadcast'):
                        await dashboard_app.state.broadcast({
                            "type": "cycle",
                            "buys": result.buys_executed,
                            "sells": result.sells_executed,
                            "candidates": result.candidates_found,
                            "regime": result.regime.value if result.regime else "unknown",
                            "mode": result.mode,
                        })

                # Reset consecutive error counter on successful cycle
                _consecutive_errors = 0
                await asyncio.sleep(tick_interval)

            except asyncio.CancelledError:
                logger.info("매매 루프 종료")
                break
            except Exception as e:
                _consecutive_errors += 1
                logger.error(
                    f"매매 루프 오류 ({_consecutive_errors}/5): "
                    f"{type(e).__name__}: {e}"
                )
                if telegram_bot:
                    await telegram_bot.send_system_event(
                        "error",
                        f"매매 루프 오류 ({_consecutive_errors}회): {e}"
                    )
                if _consecutive_errors >= 5:
                    logger.critical(
                        "연속 에러 5회 — 매매 루프 중단"
                    )
                    if telegram_bot:
                        await telegram_bot.send_system_event(
                            "critical",
                            "연속 오류 5회로 시스템 중단"
                        )
                    break
                # 지수 백오프: 에러 반복 시 대기 시간 증가
                backoff = min(tick_interval * (2 ** _consecutive_errors), 300)
                await asyncio.sleep(backoff)

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

        # Send Telegram notification before shutdown
        if telegram_bot:
            try:
                await telegram_bot.send_system_event("stop", "NGSAT 시스템 종료")
            except Exception:
                pass

        # Cancel all running tasks
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Cleanup resources
        await broker.close()
        if orchestrator:
            await orchestrator.close()
        logger.info("NGSAT 종료 완료")


async def train_model(config):
    """Train ML model using real KIS data and save to disk."""
    from data.real_data_provider import RealDataProvider
    from ml.training.trainer import train_from_price_data

    logger.info("=== NGSAT ML 모델 학습 (KIS 실데이터) ===")

    data_provider = RealDataProvider(
        training_days=config.strategy.ml_training_days,
        start_date=config.strategy.ml_training_start_date,
        end_date=config.strategy.ml_training_end_date,
    )
    universe, index_prices = await data_provider.load()

    if not universe:
        logger.error("KIS 실데이터 로드 실패 — 학습 중단")
        return

    logger.info(f"KIS 실데이터 로드 완료: {len(universe)}종목")
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
