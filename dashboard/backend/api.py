"""NGSAT dashboard backend — FastAPI API server.

Provides REST API endpoints for the React frontend dashboard.
Completely new design — not based on SAT3.

Endpoints:
  GET  /api/status           — 시스템 상태
  GET  /api/account           — 계좌 현황
  GET  /api/positions         — 보유 포지션
  GET  /api/trades            — 거래 내역
  GET  /api/regime            — 현재 레짐
  POST /api/control/start     — 매매 시작
  POST /api/control/stop      — 매매 일시정지
  POST /api/control/shutdown  — 시스템 종료
  POST /api/control/forcesell — 강제 매도
  POST /api/control/forcehold — 강제 홀드
  WS   /ws/realtime           — 실시간 업데이트
"""

from __future__ import annotations
from dataclasses import asdict
from datetime import datetime

from fastapi import FastAPI
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from core.logger import logger


# ── Request models ──

class ForceSellRequest(BaseModel):
    code: str


class ForceHoldRequest(BaseModel):
    code: str


# ── App factory ──

def create_app(orchestrator=None, config=None) -> FastAPI:
    """Create the FastAPI dashboard app.
    
    Args:
        orchestrator: TradingOrchestrator instance. If None, 
                      endpoints return "not connected" responses.
    
    Returns:
        FastAPI application.
    """
    app = FastAPI(
        title="NGSAT Dashboard API",
        description="New Generation Stock Auto Trader — Dashboard",
        version="0.1.0",
    )
    
    # CORS for frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Dev: allow all. Production: restrict.
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Serve React frontend static files (built by npm run build)
    # Catch-all: non-API paths serve index.html (SPA support)
    from pathlib import Path
    frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if frontend_dist.exists():
        import mimetypes
        mimetypes.init()

        @app.exception_handler(404)
        async def serve_frontend(request, exc):
            # API routes return 404 as usual — only catch non-/api paths
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)

            file_path = frontend_dist / request.url.path.lstrip("/")
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))

            # SPA: all other paths serve index.html
            index = frontend_dist / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        logger.info(f"대시보드 프론트엔드 마운트: {frontend_dist}")
    else:
        logger.warning(f"프론트엔드 빌드 파일 없음: {frontend_dist} — npm run build 필요")
    
    # Store orchestrator reference
    app.state.orchestrator = orchestrator
    app.state.config = config
    
    def _get_orchestrator():
        return app.state.orchestrator
    
    def _not_connected():
        return {"error": "거래 시스템이 연결되지 않았습니다", "connected": False}
    
    def _get_app_config():
        """Get StrategyConfig from app state."""
        if app.state.config is None:
            return None
        return app.state.config.strategy
    
    def _update_env_from_config(cfg):
        """Write StrategyConfig values to .env file with file locking."""
        from pathlib import Path
        from core.config import PROJECT_ROOT
        import fcntl

        env_path = Path(PROJECT_ROOT) / ".env"

        if not env_path.exists():
            return

        # Build lookup of NGSAT_* env var names → config attribute
        field_map = {
            "NGSAT_BUY_THRESHOLD": "buy_threshold",
            "NGSAT_SELL_THRESHOLD": "sell_threshold",
            "NGSAT_REGIME_BULL_THRESHOLD": "regime_bull_threshold",
            "NGSAT_REGIME_BEAR_THRESHOLD": "regime_bear_threshold",
            "NGSAT_REGIME_WEIGHT_MA": "regime_weight_ma",
            "NGSAT_REGIME_WEIGHT_RSI": "regime_weight_rsi",
            "NGSAT_REGIME_WEIGHT_BOLLINGER": "regime_weight_bollinger",
            "NGSAT_REGIME_WEIGHT_CHANGE_RATE": "regime_weight_change_rate",
            "NGSAT_REGIME_WEIGHT_VOLUME": "regime_weight_volume",
            "NGSAT_SCREENER_BULL_MIN_SCORE": "screener_bull_min_score",
            "NGSAT_SCREENER_BULL_MAX_CANDIDATES": "screener_bull_max_candidates",
            "NGSAT_SCREENER_NEUTRAL_MIN_SCORE": "screener_neutral_min_score",
            "NGSAT_SCREENER_NEUTRAL_MAX_CANDIDATES": "screener_neutral_max_candidates",
            "NGSAT_SCREENER_BEAR_MIN_SCORE": "screener_bear_min_score",
            "NGSAT_SCREENER_BEAR_MAX_CANDIDATES": "screener_bear_max_candidates",
            "NGSAT_MODE_HIGH_VOL_ATR_PCT": "mode_high_volatility_atr_pct",
            "NGSAT_MODE_LOW_VOL_ATR_PCT": "mode_low_volatility_atr_pct",
            "NGSAT_MODE_SWING_STOP_LOSS": "mode_swing_stop_loss_pct",
            "NGSAT_MODE_SWING_DAILY_LOSS": "mode_swing_daily_loss_pct",
            "NGSAT_MODE_SWING_POSITION_SIZE": "mode_swing_position_size",
            "NGSAT_MODE_SHORT_STOP_LOSS": "mode_short_stop_loss_pct",
            "NGSAT_MODE_SHORT_DAILY_LOSS": "mode_short_daily_loss_pct",
            "NGSAT_MODE_SHORT_POSITION_SIZE": "mode_short_position_size",
            "NGSAT_MODE_HOLD_STOP_LOSS": "mode_hold_stop_loss_pct",
            "NGSAT_MODE_HOLD_DAILY_LOSS": "mode_hold_daily_loss_pct",
            "NGSAT_MODE_HOLD_POSITION_SIZE": "mode_hold_position_size",
        }

        # Read + write with exclusive file lock (동시 대시보드 요청 안전)
        with open(env_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                lines = f.readlines()

                seen = set()
                new_lines = []
                for line in lines:
                    updated = False
                    for env_key, attr in field_map.items():
                        if line.startswith(env_key + "=") or line.startswith(env_key + " ="):
                            val = getattr(cfg, attr, None)
                            if val is not None:
                                new_lines.append(f"{env_key}={val}\n")
                            else:
                                new_lines.append(line)
                            seen.add(env_key)
                            updated = True
                            break
                    if not updated:
                        new_lines.append(line)

                # Append strategy keys that weren't in the file
                for env_key in field_map:
                    if env_key not in seen:
                        attr = field_map[env_key]
                        val = getattr(cfg, attr, None)
                        if val is not None:
                            new_lines.append(f"{env_key}={val}\n")

                # Write back (seek + truncate to replace content in-place)
                f.seek(0)
                f.writelines(new_lines)
                f.truncate()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    
    # ── Status ──
    @app.get("/api/status")
    async def get_status():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        controller = orch.controller
        risk = orch.risk_manager
        
        return {
            "connected": True,
            "state": controller.state.value,
            "is_running": controller.is_running,
            "risk_halted": risk.is_halted,
            "risk_reason": risk.halt_reason,
            "cycle_count": orch._cycle_count,
            "current_mode": orch._current_mode,
            "mode_stop_loss_pct": risk.effective_stop_loss_pct,
            "mode_daily_loss_limit": risk.effective_daily_loss_limit,
            "server_time": datetime.now().isoformat(),
        }
    
    # ── Account ──
    @app.get("/api/account")
    async def get_account():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        try:
            account = await orch._broker.get_account_summary()
            return {
                "connected": True,
                "total_asset": account.total_asset,
                "deposit": account.deposit,
                "total_eval": account.total_eval,
                "total_profit_loss": account.total_profit_loss,
                "total_profit_loss_pct": account.total_profit_loss_pct,
                "daily_loss": account.daily_loss,
                "daily_loss_pct": account.daily_loss_pct,
            }
        except Exception as e:
            return {"error": str(e), "connected": True}
    
    # ── Positions ──
    @app.get("/api/positions")
    async def get_positions():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        try:
            positions = await orch._broker.get_positions()
            return {
                "connected": True,
                "positions": [
                    {
                        "code": p.code,
                        "name": p.name,
                        "market": p.market.value,
                        "quantity": p.quantity,
                        "buy_price": p.buy_price,
                        "current_price": p.current_price,
                        "buy_amount": p.buy_amount,
                        "eval_amount": p.eval_amount,
                        "profit_loss": p.profit_loss,
                        "profit_loss_pct": p.profit_loss_pct,
                        "stop_loss_pct": p.stop_loss_pct,
                        "stop_loss_reason": p.stop_loss_reason,
                        "is_force_hold": orch.controller.is_force_hold(p.code),
                    }
                    for p in positions
                ],
            }
        except Exception as e:
            return {"error": str(e), "connected": True}
    
    # ── Trades (from database) ──
    @app.get("/api/trades")
    async def get_trades(limit: int = 50):
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        try:
            records = orch._trade_repo.get_recent_trades(limit)
            trades = [
                {
                    "date": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
                    "code": r.code, "name": r.name,
                    "side": r.side, "quantity": r.quantity,
                    "price": r.price, "amount": r.amount,
                    "action": r.action, "reason": r.reason,
                    "mode": r.mode,
                }
                for r in records
            ]
            return {"connected": True, "trades": trades}
        except Exception as e:
            return {"connected": True, "trades": [], "message": f"거래 내역 조회 오류: {e}"}
    
    # ── Regime ──
    @app.get("/api/regime")
    async def get_regime():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        if orch._last_regime is None:
            return {
                "connected": True,
                "regime": "unknown",
                "score": 0,
                "reason": "아직 레짐 평가가 실행되지 않았습니다",
            }
        
        regime = orch._last_regime
        regime_kr = {
            "bull": "강세장",
            "neutral": "중립장",
            "bear": "약세장",
        }.get(regime.regime.value, regime.regime.value)
        
        return {
            "connected": True,
            "regime": regime.regime.value,
            "regime_kr": regime_kr,
            "score": regime.score,
            "reason": regime.reason,
            "evidence": regime.evidence,
        }
    
    # ── Control: Start ──
    @app.post("/api/control/start")
    async def control_start():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        msg = orch.controller.start()
        return {"connected": True, "message": msg, "state": orch.controller.state.value}
    
    # ── Control: Stop ──
    @app.post("/api/control/stop")
    async def control_stop():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        msg = orch.controller.stop()
        return {"connected": True, "message": msg, "state": orch.controller.state.value}
    
    # ── Control: Shutdown ──
    @app.post("/api/control/shutdown")
    async def control_shutdown():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        msg = orch.controller.shutdown()
        return {"connected": True, "message": msg, "state": orch.controller.state.value}

    # ── Control: Restart ──
    @app.post("/api/control/restart")
    async def control_restart():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        msg = orch.controller.restart()
        return {"connected": True, "message": msg, "state": orch.controller.state.value}

    # ── Control: Force Sell ──
    @app.post("/api/control/forcesell")
    async def control_force_sell(req: ForceSellRequest):
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        result = await orch.force_sell(req.code)
        return {
            "connected": True,
            "success": result.success,
            "message": result.reason if result.success else result.error,
            "order_id": result.order_id,
        }
    
    # ── Control: Force Hold ──
    @app.post("/api/control/forcehold")
    async def control_force_hold(req: ForceHoldRequest):
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        
        orch.controller.force_hold(req.code)
        return {"connected": True, "message": f"강제 홀드 설정: {req.code}"}
    
    # ── Strategy Config ──
    @app.get("/api/strategy/config")
    async def get_strategy_config():
        """현재 전략·정책 설정값 반환."""
        from dataclasses import asdict
        cfg = _get_app_config()
        if cfg is None:
            return {"connected": False}
        return {"connected": True, "config": asdict(cfg)}
    
    @app.put("/api/strategy/config")
    async def update_strategy_config(data: dict):
        """전략·정책 설정값 업데이트 → .env 반영.
        
        Body: { "buy_threshold": 0.70, ... } (부분 업데이트 가능)
        "reset": true → 기본값으로 복원
        """
        cfg = _get_app_config()
        if cfg is None:
            return {"connected": False}
        
        if data.get("reset"):
            # 기본값으로 복원: 복원된 config 반환
            from core.config import StrategyConfig
            restored = StrategyConfig()
            _update_env_from_config(restored)
            return {"connected": True, "message": "기본값으로 복원 완료", "config": asdict(restored), "restart_required": True}
        
        # 부분 업데이트
        updated = 0
        for key, value in data.items():
            if key in ("connected", "reset", "restart_required"):
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, value)
                updated += 1
        
        if updated > 0:
            _update_env_from_config(cfg)
        
        return {"connected": True, "message": f"{updated}개 설정 저장 완료", "config": asdict(cfg), "restart_required": updated > 0}
    
    # ── Diagnosis ──
    @app.get("/api/diagnosis")
    async def get_diagnosis():
        orch = _get_orchestrator()
        if orch is None:
            return _not_connected()
        diag = orch._last_diagnosis
        if diag is None:
            return {"connected": True, "message": "아직 진단 데이터가 없습니다"}
        return {"connected": True, **diag}
    
    # ── Health ──
    @app.get("/api/health")
    async def health():
        return {"status": "ok", "service": "NGSAT Dashboard API"}
    
    # ── WebSocket: Realtime ──
    @app.websocket("/ws/realtime")
    async def websocket_realtime(ws: WebSocket):
        await ws.accept()
        try:
            # Send initial status
            orch = _get_orchestrator()
            if orch:
                await ws.send_json({
                    "type": "status",
                    "state": orch.controller.state.value,
                    "is_running": orch.controller.is_running,
                })
            
            # Keep connection alive
            while True:
                # Wait for client messages (ping/pong)
                data = await ws.receive_text()
                if data == "ping":
                    await ws.send_text("pong")
                    
        except WebSocketDisconnect:
            logger.info("대시보드 WebSocket 연결 종료")
        except Exception as e:
            logger.error(f"WebSocket 오류: {e}")
    
    return app
