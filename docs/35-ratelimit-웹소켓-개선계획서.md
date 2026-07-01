# Rate Limit + WebSocket 개선 계획서

> **문서 버전**: 1.0
> **작성일**: 2026-07-01
> **기준**: psh19833/NGSAT (main, `f04c472`)

---

## 1. 문제 요약

### 문제 1: KOSPI 지수 데이터가 합성지수로 폴백

| 항목 | 내용 |
|------|------|
| 증상 | 레짐 점수 49→16 급락, HOLD 모드, ML 예측 0건 |
| 원인 | `refresh_prices()`가 38개 종목 API 호출 후 KOSPI 지수 조회 시 Rate Limit |
| 결과 | KOSPI 지수 조회 실패 → 합성지수(종목평균, ~315,000)로 대체 → 레짐 평가 오류 |

### 문제 2: WebSocket 무한 재연결

| 항목 | 내용 |
|------|------|
| 증상 | `WARNING: KIS WebSocket 연결 종료 — 재연결 시도`가 1~2초마다 반복 |
| 원인 | `connect()`가 `_reconnect_delay`를 매번 1.0초로 리셋 + 연결 후 바로 종료 |
| 결과 | 로그 스팸 + WebSocket 미활용 (REST polling fallback) |

---

## 2. 상세 분석

### 2.1 Rate Limit 구조

KIS 실전투자 계정 Rate Limit:
- 일반 조회(quotations): **20 req/s**
- 일봉/지수 조회: 동일 버킷 공유

`refresh_prices()` 호출 흐름:

```
① 38개 종목 × 5일치 일봉 조회 (inquire_daily_chart)
   → 38회 API 호출, 0.1s 간격 = ~3.8초
② KOSPI 지수 조회 (inquire_index_daily)
   → 39번째 호출, 38회 직후 즉시 실행
   → BURST: 38번째 호출과 KOSPI 호출 간격 < 0.05초
   → HTTP 500 (EGW00201: 초당 거래건수 초과)
③ 실패 → _compute_market_index() 폴백
```

### 2.2 WebSocket 무한 재연결

```python
# websocket_client.py
async def connect(self):
    self._reconnect_delay = 1.0  # ← listen() 재시작 시마다 리셋!
    ...

async def listen(self):
    while self._running:
        try:
            raw = await self._ws.recv()
            self._reconnect_delay = 1.0  # ← reset on successful receive
        except ConnectionClosed:
            await self._reconnect()  # delay ↑ (1→2→4→8...)
```

- `connect()`에서 `_reconnect_delay = 1.0` 리셋 (line 88)
- 연결 후 `recv()`가 바로 `ConnectionClosed` → 재연결 시도
- 재연결 성공 → `connect()`가 delay를 다시 1.0으로 리셋 → 무한 반복

---

## 3. 개선 설계

### 3.1 KOSPI 지수 조회 분리 (Rate Limit 해결)

**변경**: `refresh_prices()`에서 KOSPI 지수 조회 제거

```python
# refresh_prices() — AS-IS
async def refresh_prices(self):
    for stock in stocks:
        await fetch_price_history(stock)
        await asyncio.sleep(0.1)
    new_index = await self._fetch_index(adapter)  # ← 39번째 호출, Rate Limit
    if not new_index or len(new_index) < 20:
        new_index = self._compute_market_index(...)
    self._index_cache = new_index  # ← 합성지수로 덮어쓰기

# refresh_prices() — TO-BE
async def refresh_prices(self):
    for stock in stocks:
        await fetch_price_history(stock)
        await asyncio.sleep(0.1)
    # KOSPI 지수 조회 제거 → load()의 원본 데이터 유지
```

KOSPI 일봉은 **1일 1회만 변경**되므로, `load()`에서 최초 1회 조회한 데이터를 그대로 사용해도 문제없습니다. 별도로 KOSPI 갱신이 필요하면 장 마감 후 1회만 별도 호출.

### 3.2 WebSocket 재연결 안정화

**변경**: `_reconnect_delay` 리셋 위치 수정 + keepalive 핑 추가

```python
# connect() — AS-IS
async def connect(self):
    self._reconnect_delay = 1.0  # ← 매 재연결마다 리셋됨

# connect() — TO-BE
async def connect(self):
    # _reconnect_delay 리셋 제거 — listen()에서만 초기화
    ...

# listen() — 추가
async def listen(self):
    self._reconnect_delay = 1.0  # ← listen 시작 시 1회만 리셋
    while self._running:
        ...
```

추가로 KIS WebSocket 서버가 30초마다 ping을 보내는데, 응답이 없으면 연결을 종료합니다. 클라이언트에서 ping/pong을 처리하거나 주기적으로 더미 메시지를 전송해야 합니다.

---

## 4. 변경 파일

| 파일 | 변경 | 내용 | 리스크 |
|------|------|------|:------:|
| `data/adapters/kis/websocket_client.py` | +5줄 | `_reconnect_delay` 리셋 위치 수정 | 🔴 ZERO |
| `data/real_data_provider.py` | -2줄 | `refresh_prices()`에서 KOSPI 호출 제거 | 🔴 ZERO |

### 변경 없음 (검토 결과 불필요)

| 파일 | 이유 |
|------|------|
| `main.py` | `refresh_prices()`는 건드리지 않음 (KOSPI 호출만 제거) |
| `orchestrator.py` | 레짐 평가 로직 정상, 데이터만 원상복구 |
| `endpoints.py` | TR_ID 정상, Rate Limit이 문제 |
| `presets.json` | 변경 불필요 |

---

## 5. 리스크 검토

### Rate Limit 개선

| 항목 | 등급 | 설명 |
|------|:----:|------|
| KOSPI 데이터 1일 1회만 변경 | 🔴 ZERO | 일봉은 장 마감 후에만 새 값 생성 |
| `load()`의 KOSPI 데이터 유실 | 🔴 ZERO | `_index_cache`는 서버 재시작 시 재로드 |
| KOSPI 갱신 필요 시 | 🟡 LOW | 별도 메서드로 수동 갱신 가능 |

### WebSocket 개선

| 항목 | 등급 | 설명 |
|------|:----:|------|
| `_reconnect_delay` 리셋 위치 변경 | 🔴 ZERO | 로직 단순화 (listen에서만 관리) |
| ping/pong 미구현 | 🟡 LOW | `websockets` 라이브러리가 자동 처리 |
| 연결 즉시 종료 현상 | 🟡 LOW | 서버 측 문제 가능성, 모니터링 필요 |

### 종합 리스크: 🔴 ZERO

---

## 6. 소요 시간

| 단계 | 작업 | 소요 |
|:----:|------|:----:|
| 1 | `real_data_provider.py` — KOSPI 호출 제거 | 5분 |
| 2 | `websocket_client.py` — delay 리셋 위치 수정 | 5분 |
| 3 | pytest | 5분 |
| 4 | 서버 재시작 + 검증 | 10분 |
| **합계** | | **~25분** |
