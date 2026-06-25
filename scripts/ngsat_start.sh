#!/bin/bash
# NGSAT 기동 스크립트
# 사용법: ./scripts/ngsat_start.sh [mode]
# mode: live (기본) | backtest | train

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# 가상환경 활성화
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

# .env 확인
if [ ! -f .env ]; then
    echo "⚠ .env 파일이 없습니다. .env.example을 복사해서 생성하세요:"
    echo "  cp .env.example .env"
    echo "  그 후 KIS API 키, 계좌번호, 텔레그램 토큰을 입력하세요."
    exit 1
fi

MODE="${1:-live}"

echo "════════════════════════════════════════"
echo "  NGSAT — New Generation Stock Auto Trader"
echo "  모드: $MODE"
echo "════════════════════════════════════════"
echo ""

case "$MODE" in
    live)
        echo "실거래 모드로 시작합니다..."
        python main.py
        ;;
    backtest)
        echo "백테스트 모드로 시작합니다..."
        python main.py --backtest
        ;;
    train)
        echo "ML 모델 학습 모드로 시작합니다..."
        python main.py --train
        ;;
    *)
        echo "알 수 없는 모드: $MODE"
        echo "사용법: $0 [live|backtest|train]"
        exit 1
        ;;
esac
