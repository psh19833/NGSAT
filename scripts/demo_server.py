"""Demo dashboard API server — Mock orchestrator for UI testing only.
No real trading, no KIS connection. Safe to run anytime.
Usage: python scripts/demo_server.py
"""
import sys
from pathlib import Path

# Ensure NGSAT root in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.backend.api import create_app
from tests.test_live.test_dashboard_api import MockOrchestrator
from core.config import StrategyConfig, Config
import uvicorn

if __name__ == "__main__":
    # Build config with strategy defaults
    config = Config()
    config.strategy = StrategyConfig()

    app = create_app(MockOrchestrator(), config)
    print("=" * 50)
    print("  NGSAT 데모 대시보드 API 서버")
    print("  http://127.0.0.1:8001")
    print("  /api/health, /api/status, /api/control/* ")
    print("  실제 매매 없음 — UI 확인용")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info")
