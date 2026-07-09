#!/usr/bin/env bash
# NGSAT 서버 재시작 스크립트 — 좀비 프로세스 방지
# Usage: bash scripts/restart_server.sh [--tick-interval=10]

set -e

PIDFILE="/tmp/ngsat-server.pid"
cd "$(dirname "$0")/.." || exit 1
TICK_INTERVAL=10

# Parse args
for arg in "$@"; do
  case $arg in
    --tick-interval=*) TICK_INTERVAL="${arg#*=}" ;;
  esac
done

echo "=== NGSAT 서버 재시작 ==="

# 1) 기존 프로세스 종료 (PID file 우선, fallback: pkill)
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "🔴 기존 프로세스 PID=$OLD_PID 종료 중..."
        kill -TERM "$OLD_PID" 2>/dev/null || true
        # 최대 10초 대기
        for i in $(seq 1 10); do
            if ! kill -0 "$OLD_PID" 2>/dev/null; then
                echo "   → 정상 종료 확인 (${i}초)"
                break
            fi
            sleep 1
        done
        # 그래도 살아있으면 SIGKILL
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "   → 강제 종료 (SIGKILL)"
            kill -9 "$OLD_PID" 2>/dev/null || true
        fi
    else
        echo "   PID=$OLD_PID 없음 (이미 종료됨)"
    fi
    rm -f "$PIDFILE"
else
    echo "⚠️  PID file 없음 — pkill fallback"
    # signal_handler 버그 수정 후 SIGTERM으로 정상 종료됨
    pkill -f 'python.*main.py' 2>/dev/null || true
    sleep 2
fi

# 2) 중복 프로세스 최종 확인
REMAINING=$(ps aux | grep 'python main.py' | grep -v grep | wc -l)
if [ "$REMAINING" -gt 0 ]; then
    echo "⚠️  잔여 프로세스 ${REMAINING}개 강제 종료"
    ps aux | grep 'python main.py' | grep -v grep | awk '{print $2}' | xargs -r kill -9
    sleep 1
fi

# 3) Python 캐시 정리 (디버그 코드 변경사항 반영)
rm -f ml/__pycache__/inference*.pyc 2>/dev/null || true

# 4) 새 서버 시작
echo "🟢 새 서버 시작 (tick=${TICK_INTERVAL}초)..."
nohup .venv/bin/python main.py --tick-interval="$TICK_INTERVAL" > /dev/null 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "   PID=$NEW_PID"

# 5) 시작 확인 (최대 30초 대기)
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/api/status > /dev/null 2>&1; then
        echo "✅ 서버 기동 완료 (${i}초)"
        break
    fi
    sleep 1
done

# 6) 컨트롤러 시작
sleep 2
curl -sf -X POST http://localhost:8000/api/control/start > /dev/null 2>&1 && \
    echo "✅ 컨트롤러 시작 완료"

echo "=== 재시작 완료 ==="
