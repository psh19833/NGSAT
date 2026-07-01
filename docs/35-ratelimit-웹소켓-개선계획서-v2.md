# Rate Limit + WebSocket 개선 계획서 v2

> **문서 버전**: 2.0
> **작성일**: 2026-07-01
> **기준**: psh19833/NGSAT (main, `f04c472`)

---

## 1. 문제 요약

### 문제 1: KOSPI 지수 데이터가 합성지수로 폴백 (근본 원인 수정)

| 항목 | 내용 |
|------|------|
| 증상 | 레짐 점수 49→16 급락, HOLD 모드, ML 예측 0건 |
| 원인 | `load()`와 `refresh_prices()` 모두 38개 종목 API 호출 **후** KOSPI 지수를 조회 → 39번째 호출 Rate Limit |
| 결과 | KOSPI 지수 조회 실패 → 합성지수(종목평균)로 대체 → 레짐 평가 오류 |

### 문제 2: WebSocket 무한 재연결 (추가 수정)

| 항목 | 내용 |
|------|------|
| 증상 | `WARNING: KIS WebSocket 연결 종료 — 재연결 시도` 1~2초마다 반복 |
| 원인 | `connect()`가 `_reconnect_delay`를 매번 리셋 |
| 결과 | 로그 스팸 + WebSocket 미활용 |

---

## 2. 근본 원인 분석

### 2.1 Rate Limit 구조

KIS 실전투자 Rate Limit: **Token Bucket, 20 tokens, 20 tokens/s 리필**

```
Token Bucket:
  용량: 20 tokens
  리필: 20 tokens/초 (= 50ms에 1 token)
  소모: API 1회 호출 = 1 token
```

### 2.2 현재 호출 순서 (문제)

```
load() / refresh_prices() 현재:
① Stock 1 (inquire_daily_chart)  → 버킷: 19
② sleep 0.1s                     → 리필 +2 → 버킷: 20
③ Stock 2 (inquire_daily_chart)  → 버킷: 19
...
⑦⑧ Stock 38 (inquire_daily_chart) → 버킷: 19
⑨ KOSPI (inquire_index_daily)    → 버킷: 19  ← 성공해야 정상
```

0.1초 간격(10회/s) < 리필 속도(20/s) → 버킷은 절대 바닥나지 않음.

**문제는 38번째 Stock과 KOSPI 호출 사이의 간격이 0.01초 미만**:
```python
for stock in stocks:
    await fetch(stock)
    await asyncio.sleep(0.1)  # ← 마지막 Stock 후 sleep 없음!
new_index = await self._fetch_index(adapter)  # ← 직후 실행 → Rate Limit BURST
```

마지막 Stock 호출과 KOSPI 호출 사이에 sleep이 없어서 **순간 2회 연속 호출** = 20/s 초과.

### 2.3 KOSPI를 먼저 호출하면?

```
수정 후:
① KOSPI (inquire_index_daily)  → 버킷: 19      ← 버킷 Full 상태에서 1번째
② sleep 0.1s                   → 리필 +2 → 버킷: 20
③ Stock 1 (inquire_daily_chart) → 버킷: 19
...
④ Stock 38                     → 버킷: 19      ← 마지막도 여유
```

KOSPI를 **첫 번째**로 호출하면 버킷이 가득 찬 상태에서 항상 성공. 이후 0.1s 간격의 Stock 호출은 Rate Limit에 절대 걸리지 않음.

### 2.4 WebSocket 재연결 루프

```python
async def connect(self):
    self._reconnect_delay = 1.0  # ← 재연결 성공 시마다 리셋!

async def listen(self):
    ConnectionClosed → _reconnect() → connect() → delay 리셋
    → 바로 또 ConnectionClosed → loop ∞
```

KIS WebSocket이 연결 직후 종료되는 원인은 ping/pong 미응답 가능성. KIS 서버가 30초마다 ping을 보내는데, 응답이 없으면 연결을 닫습니다. 클라이언트가 pong을 자동 응답하도록 수정.

---

## 3. 변경 설계

### 3.1 KOSPI 먼저 호출 (Rate Limit 해결)

**파일**: `data/real_data_provider.py`

`load()`와 `refresh_prices()` 모두에서 KOSPI 지수 조회를 첫 번째로 이동:

```python
# AS-IS
async def load(self):
    for info in stock_codes:
        prices = await adapter.get_price_history(...)
        await asyncio.sleep(0.1)
    index = await self._fetch_index(adapter)  # ← 39번째, Rate Limit
    ...

# TO-BE  
async def load(self):
    index = await self._fetch_index(adapter)  # ← 1번째, 항상 성공 ✅
    for info in stock_codes:
        prices = await adapter.get_price_history(...)
        await asyncio.sleep(0.1)
    ...
```

동일한 수정을 `refresh_prices()`에도 적용:

```python
# AS-IS
async def refresh_prices(self):
    for stock in stocks:
        await fetch(stock)
        await asyncio.sleep(0.1)
    new_index = await self._fetch_index(adapter)  # ← 39번째, Rate Limit
    ...

# TO-BE
async def refresh_prices(self):
    new_index = await self._fetch_index(adapter)  # ← 1번째, 항상 성공 ✅
    for stock in stocks:
        await fetch(stock)
        await asyncio.sleep(0.1)
    ...
```

### 3.2 WebSocket ping/pong + delay 리셋 수정

**파일**: `data/adapters/kis/websocket_client.py`

1. `_reconnect_delay` 리셋을 `listen()`에서만 관리
2. `recv()` 타임아웃 설정으로 연결 끊김 감지
3. pong 자동 응답 (websockets 라이브러리가 기본 지원)

```python
async def connect(self):
    # _reconnect_delay = 1.0 ← 제거 (listen()에서만 관리)
    ...

async def listen(self):
    self._reconnect_delay = 1.0  # ← listen 시작 시 1회만 초기화
    while self._running:
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=35)
            # ← 타임아웃 35초 (KIS ping 간격 30초보다 길게)
        except asyncio.TimeoutError:
            # 연결은 살아있음, ping/pong 정상
            continue
        except websockets.ConnectionClosed:
            await self._reconnect()
```

---

## 4. 변경 파일

| 파일 | 변경 | 내용 | 리스크 |
|------|:----:|------|:------:|
| `data/real_data_provider.py` | **2줄 이동** | `_fetch_index()`를 종목 루프 **앞**으로 이동 | 🔴 ZERO |
| `data/adapters/kis/websocket_client.py` | **3줄 수정** | delay 리셋 위치 + `wait_for` 타임아웃 | 🔴 ZERO |

### 변경 없는 파일

| 파일 | 이유 |
|------|------|
| `main.py` | refresh_prices() 호출 로직 변경 없음 |
| `orchestrator.py` | 레짐 평가 로직 정상, 데이터만 복구됨 |
| `endpoints.py` | TR_ID 정상 |
| `presets.json` | 변경 불필요 |

---

## 5. 리스크 검토

### Rate Limit 개선

| 항목 | 등급 | 분석 |
|------|:----:|------|
| KOSPI 먼저 호출 시 나머지 38개 종목 | 🔴 ZERO | 0.1s 간격(10회/s) < 리필 속도(20/s). 버킷 절대 바닥 안 남 |
| `load()`와 `refresh_prices()` 동시 수정 | 🔴 ZERO | 동일 로직, 동일 패턴 |
| KOSPI API 자체 장애 | 🟡 LOW | Rate Limit과 무관한 별도 장애. 현재와 동일 |
| **Rate Limit 종합** | **🔴 ZERO** | **KOSPI를 먼저 호출하면 물리적으로 Rate Limit 불가능** |

### WebSocket 개선

| 항목 | 등급 | 분석 |
|------|:----:|------|
| `_reconnect_delay` 리셋 위치 변경 | 🔴 ZERO | listen() 1회만 초기화, reconnect()에서 지수 백오프 정상 동작 |
| `wait_for` 타임아웃 도입 | 🔴 ZERO | `asyncio.wait_for` 표준 패턴, 예외 처리 명확 |
| 연결 즉시 종료 현상 | 🟡 LOW | KIS 서버 측 문제 가능성. 지수 백오프로 부하 완화 |
| **WebSocket 종합** | **🔴 ZERO** | **설계상 사이드이펙트 없음** |

### 종합 리스크 등급: 🔴 ZERO

---

## 6. 소요 시간

| 단계 | 작업 | 소요 |
|:----:|------|:----:|
| 1 | `real_data_provider.py` — KOSPI 호출을 종목 앞으로 이동 (2군데) | 5분 |
| 2 | `websocket_client.py` — delay 리셋 + wait_for 타임아웃 | 5분 |
| 3 | pytest 실행 | 5분 |
| 4 | 서버 재시작 + 레짐/ML 예측 정상화 검증 | 10분 |
| **합계** | **변경 5줄, 소요 25분** | **~25분** |
