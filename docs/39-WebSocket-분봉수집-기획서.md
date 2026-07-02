# WebSocket 기반 실시간 분봉 수집 — 기획서

**버전:** 1.0
**작성일:** 2026-07-02
**상태:** 기획 (미구현)

---

## 1. 배경 및 문제 정의

### 1.1 현재 문제

NGSAT의 분봉 데이터 수집은 **REST API 폴링 방식**으로 동작합니다.

```
매 사이클(10초)마다 REST API 15회 호출
  → KIS Rate Limit (EGW00201) 초과 → 데이터 누락
  → 분봉 30개만 반환 (60개 필요)
  → 분봉 ML 비활성화 (30 < 60)
  → 분봉 스크리너 점수 수렴
```

### 1.2 KIS API 특성 (공식 문서 기준)

| 구분 | REST API | WebSocket |
|:----|:--------|:----------|
| 통신 방식 | 요청-응답 (Request-Response) | 지속 연결 (Push) |
| 연결 상태 | 매 요청마다 연결/종료 | 연결 지속 |
| 주요 용도 | 주문, 잔고조회, 과거차트 | **실시간 시세, 체결가** |
| Rate Limit | 초당 20회 제한 | **구독 기반, 제한 없음** |
| 40종목 동시 | 불가능 | **가능** |

---

## 2. 현황 분석

### 2.1 이미 구축된 WebSocket 인프라

`data/adapters/kis/websocket_client.py` — **이미 40종목 실시간 체결가 구독 중**

| 항목 | 현재 상태 |
|:----|:---------|
| WebSocket 연결 | ✅ 40종목 구독 완료 |
| 체결가 수신 | ✅ H0UCNT0 (주식체결가) |
| 가격 업데이트 | ✅ `on_price` → 캐시 업데이트 |
| **분봉 데이터 수집** | ❌ **미구현** |

```python
# 현재 on_price: 단순 가격만 캐시 업데이트
def on_price(code, price, volume, ts):
    prices[-1] = PriceData(close=price, volume=volume, ...)
```

### 2.2 WebSocket 체결가 메시지 (이미 수신 중)

KIS WebSocket이 보내는 체결가 메시지에는 분봉 생성에 필요한 **모든 필드**가 포함되어 있으나, 현재 `on_price` 콜백이 4개 필드만 전달:

| WebSocket 필드 | 설명 | 현재 전달 여부 |
|:-------------|:----|:------------:|
| `stck_prpr` | 현재가 | ✅ `price` |
| `stck_oprc` | 시가 | ❌ |
| `stck_hgpr` | 고가 | ❌ |
| `stck_lwpr` | 저가 | ❌ |
| `acml_vol` | 누적거래량 | ✅ `volume` |
| `stck_cntg_hour` | 체결시간 | ✅ `ts` |

**→ on_price 확장만으로 분봉 생성에 필요한 모든 데이터 확보 가능**

---

## 3. 제안 아키텍처

### 3.1 전체 구조

```
                    ┌─────────────────────────────────┐
                    │     KIS WebSocket (H0UCNT0)     │
                    │     40종목 실시간 체결가 Push     │
                    └────────────┬────────────────────┘
                                 │ 1초 단위 tick 데이터
                                 ▼
              ┌─────────────────────────────────────┐
              │         MinuteBarBuilder            │
              │  (data/minute_bar_builder.py)       │
              │                                     │
              │  code → { ticks[], current_minute } │
              │  1분마다 OHLCV 캔들 집계             │
              │  PriceData 리스트 유지 (최대 120개)   │
              └────────────┬────────────────────────┘
                           │ 사이클마다 조회 (REST API 0회)
                           ▼
              ┌─────────────────────────────────────┐
              │  Step 5b: 분봉 스크리너              │
              │  40종목 실시간 점수 (매 사이클 변동)   │
              ├─────────────────────────────────────┤
              │  Step 6: 분봉 ML 진입/청산           │
              │  60+ bars 보장 → 항상 활성화          │
              └─────────────────────────────────────┘
```

### 3.2 MinuteBarBuilder 상세

```python
class MinuteBarBuilder:
    """WebSocket tick 데이터 → 1분봉 OHLCV 집계."""

    def __init__(self, max_bars: int = 120):
        self._buffers: dict[str, list[dict]] = {}    # code → 현재 분 ticks
        self._bars: dict[str, list[PriceData]] = {}  # code → 완성된 분봉 리스트

    def feed(self, code: str, price: float, high: float, low: float,
             open: float, volume: int, timestamp: str) -> None:
        """WebSocket tick 데이터 수신 → 분봉 집계."""

    def get_bars(self, code: str, n: int = 60) -> list[PriceData]:
        """최근 N개 분봉 반환 (REST API 대체)."""
```

### 3.3 데이터 흐름

```
기존:
  REST API inquire_time_chart (30개) → Rate Limit 문제
  → 30개만 있어 분봉 ML 비활성화

변경 후:
  WebSocket on_price 확장 → MinuteBarBuilder.feed()
  → 1분 후: 분봉 1개 완성
  → 60분 후: 분봉 60개 확보 (REST API 0회)
  → 분봉 ML 항상 활성화
```

---

## 4. 변경 범위

| 파일 | 변경 내용 | 크기 |
|:----|:---------|:----:|
| `websocket_client.py` | `on_price` 콜백에 high/low/open 추가 | **+3줄** |
| `data/minute_bar_builder.py` | **신규** — 분봉 집계 엔진 | **~150줄** |
| `real_data_provider.py` | `on_price` → `MinuteBarBuilder.feed()` 연결 | **~10줄** |
| `orchestrator.py` | `_fetch_minute_prices()` → 로컬 캐시 우선 조회 | **~5줄** |
| `strategy/minute_screener.py` | 분봉 스크리너에 변별력 개선 (Phase B-1 보강) | **~20줄** |

**총 예상: ~200줄, 3~4시간**

---

## 5. 리스크 검토

| 항목 | 리스크 | 사유 | 대책 |
|:----|:-----:|:-----|:-----|
| **WebSocket on_price 확장** | **ZERO** | 기존 콜백 시그니처만 변경, 로직 변경 없음 | 하위호환 유지 |
| **MinuteBarBuilder 신규** | **LOW** | 순수 메모리 연산, DB/파일 없음, REST API 호출 없음 | 동시성 Lock만 신경쓰면 OK |
| **분봉 데이터 소스 전환** | **LOW** | REST → 로컬 캐시. REST 폴백 경로 유지 | 데이터 부족 시 REST fallback |
| **분봉 스크리너 변별력** | **ZERO** | 기존 로직 세분화, 독립적 변경 | — |
| **40종목 분봉 ML** | **LOW** | 60개 bars 보장, REST 의존성 제거 | 초기 60분은 REST fallback |

---

## 6. REST API 호출 감소 효과

| API | 현재 (사이클당) | 변경 후 (사이클당) | 감소 |
|:---|:--------------:|:----------------:|:----:|
| `inquire_time_chart` (분봉) | 15회 | **0회** | **-100%** |
| `inquire_balance` (계좌) | 1회 | 1회 (캐시 5초) | 동일 |
| `inquire_daily_chart` (일봉) | 0회 (1일 1회) | 0회 (1일 1회) | 동일 |
| **총 REST 호출** | **16회/사이클** | **1회/사이클** | **-94%** |

---
