# WebSocket 기반 실시간 분봉 수집 — 실행계획서

**버전:** 1.0
**작성일:** 2026-07-02
**기획서:** `docs/39-WebSocket-분봉수집-기획서.md`

---

## Phase 1 — WebSocket on_price 확장 + 분봉 집계 (리스크 ZERO~LOW, 2시간)

### 1-1: on_price 콜백 확장

**파일:** `data/adapters/kis/websocket_client.py:65-66`, `:269-280`

```python
# 변경 전
self.on_price: Optional[Callable[[str, float, int, str], None]] = None

# 변경 후 — 시가/고가/저가 추가
self.on_price: Optional[Callable[[str, float, float, float, float, int, str], None]] = None
# on_price(code, price, high, low, open, volume, timestamp)
```

**리스크:** ZERO — 콜백 시그니처 변경, 실사용처 1곳(`real_data_provider.py`)만 수정

### 1-2: `_handle_message`에 체결가 필드 추가

**파일:** `websocket_client.py:269-280`

```python
# body에서 추가 추출
stck_oprc = body.get("stck_oprc", "0")   # 시가
stck_hgpr = body.get("stck_hgpr", "0")   # 고가
stck_lwpr = body.get("stck_lwpr", "0")   # 저가
```

### 1-3: MinuteBarBuilder 신규 생성

**파일:** `data/minute_bar_builder.py` (신규, ~150줄)

| 메서드 | 역할 |
|:------|:-----|
| `feed(code, price, high, low, open, volume, timestamp)` | WebSocket tick 수신 → 분 단위 버퍼에 적재 |
| `_flush(code, current_minute)` | 1분 경과 시 OHLCV 캔들 완성 → `_bars[code]`에 추가 |
| `get_bars(code, n=60)` | 최근 N개 분봉 반환 (REST API 대체용) |
| `has_enough(code, min_bars=60)` | 분봉 ML 활성화 조건 확인 |

**핵심 로직:**
```python
def feed(self, code, price, high, low, open, volume, timestamp):
    minute = timestamp[:4]  # HHMM

    if self._current_minute[code] != minute:
        self._flush(code, self._current_minute[code])  # 이전 분 마감

    self._buffers[code].append({
        "price": price, "high": high, "low": low,
        "open": open, "volume": volume
    })
    self._current_minute[code] = minute

def _flush(self, code, minute):
    ticks = self._buffers[code]
    if not ticks:
        self._current_minute[code] = datetime.now().strftime("%H%M")
        return

    bar = PriceData(
        timestamp=datetime.now(),
        open=ticks[0]["open"],
        high=max(t["high"] for t in ticks),
        low=min(t["low"] for t in ticks),
        close=ticks[-1]["price"],
        volume=ticks[-1]["volume"],
    )
    self._bars[code].append(bar)
    self._buffers[code] = []
    # 최대 120개 유지
    if len(self._bars[code]) > self._max_bars:
        self._bars[code] = self._bars[code][-self._max_bars:]
```

**리스크:** LOW — 순수 메모리 연산, I/O 없음, 동시성 Lock만 적용

---

## Phase 2 — 실데이터 연결 (리스크 LOW, 1시간)

### 2-1: real_data_provider.py WebSocket 연동

**파일:** `data/real_data_provider.py:327-338`

```python
# 변경 전: 단순 가격만 캐시 업데이트
def on_price(code, price, volume, ts):
    prices[-1] = PriceData(close=price, ...)

# 변경 후: 분봉 집계 + 캐시 업데이트
minute_builder = MinuteBarBuilder()

def on_price(code, price, high, low, open, volume, ts):
    minute_builder.feed(code, price, high, low, open, volume, ts)
    prices[-1] = PriceData(close=price, ...)  # 기존 캐시 업데이트 유지
```

**리스크:** LOW — 기존 캐시 업데이트 유지, MinuteBarBuilder는 추가

### 2-2: 분봉 데이터 소스 전환

**파일:** `live/orchestrator.py:810-819` (`_fetch_minute_prices`)

```python
# 변경 전: REST API만
async def _fetch_minute_prices(self, code):
    return await self._broker.get_minute_history(code)

# 변경 후: 로컬 캐시 우선, REST fallback
async def _fetch_minute_prices(self, code):
    bars = self._minute_builder.get_bars(code, 60)
    if bars and len(bars) >= 30:
        return bars
    return await self._broker.get_minute_history(code)  # REST fallback
```

**리스크:** LOW — REST fallback 경로 유지, 데이터 부족 시 자동 폴백

---

## Phase 3 — 분봉 스크리너 변별력 + ML 활성화 (리스크 ZERO, 1시간)

### 3-1: 분봉 스크리너 지표 세분화

**파일:** `strategy/minute_screener.py`

| 지표 | 현재 구간 | 변경 |
|:----|:---------|:-----|
| RSI | 30~70 = +15 | 40~60=+15, 30~40/+60~70=+10, 25~30/+70~75=+5 |
| 모멘텀 | -2~0% = -5 | -1~0%=-3, -2~-1%=-8, <-2%=-15 |
| 거래량 | 0.5~2x = +5 | 0.8~1.2x=+5, 1.2~1.5x=+8, >1.5x=+12 |
| 변동성 | 0.5~2% = +10 | 1~1.5%=+10, 0.5~1%=+5, >2%=-5 |

### 3-2: 분봉 ML 조건 완화

**파일:** `live/orchestrator.py:496, 734`

```
len(mp) >= 60 조건 유지 — 60분 후부터 항상 충족
60분 미만: REST fallback (30개 데이터)
```

---

## Phase 4 — REST API 호출 최적화 (리스크 ZERO, 30분)

### 4-1: 분봉 REST API 제거

`orchestrator.py`에서 `_fetch_minute_prices()`가 항상 로컬 캐시 반환 → `inquire_time_chart` 호출 0회

### 4-2: Rate Limit 여유 확보

| 항목 | 현재 | 변경 후 |
|:----|:----|:--------|
| 사이클당 REST 호출 | 15~16회 | **1회** (계좌조회) |
| Rate Limit 여유 | 4~5회/초 | **19회/초** |
| 40종목 동시 분봉 | 불가능 | **가능** |

---

## 실행 순서 요약

```
Phase 1 (2시간) ─── WebSocket on_price 확장 + MinuteBarBuilder 신규
       ↓
Phase 2 (1시간) ─── 실데이터 연결 + 분봉 소스 전환
       ↓
Phase 3 (1시간) ─── 분봉 스크리너 변별력 + ML 활성화 조건
       ↓
Phase 4 (30분) ─── REST API 호출 정리 + Rate Limit 확보
                    ↓
              pytest 329+ passed
              서버 재시작
```

**총 예상: 4~5시간**

---

## 롤백 계획

| 단계 | 롤백 방법 |
|:----|:----------|
| Phase 1 | MinuteBarBuilder만 추가 → 제거해도 무영향 |
| Phase 2 | `_fetch_minute_prices()` 원복 → REST API 모드 |
| Phase 3 | minute_screener.py 원복 |
| Phase 4 | 영향 없음 (호출 감소만) |
