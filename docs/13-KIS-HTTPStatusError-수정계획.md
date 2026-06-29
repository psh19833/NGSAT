# KIS HTTPStatusError 수정 계획서

> **문서:** docs/13-KIS-HTTPStatusError-수정계획.md
> **작성일:** 2026-06-29
> **상태:** 초안 (리뷰 필요)

---

## 1. 문제 정의

### 1.1 증상

대시보드 계좌 잔고 조회 시 약 60~70% 확률로 `KIS GET inquire_balance HTTP 실패: HTTPStatusError` 오류 발생. 나머지 30~40%는 정상 응답.

### 1.2 관찰된 패턴 (로그 분석)

| 시간 | `inquire_balance` 결과 | 동시 요청 |
|------|----------------------|-----------|
| 09:10:57 | 실패 (HTTPStatusError) | startup |
| 09:10:59 | 실패 (HTTPStatusError) | dashboard: `/api/account` + `/api/positions` 동시 호출 |
| 09:11:02 | 실패 (HTTPStatusError) | 동시 호출 |
| 09:11:07 | **성공** (총자산 조회) | 단일 요청 |
| 09:11:12 | **성공** | 단일 요청 |
| 09:11:17 | 실패 | 동시 호출 |
| 09:11:22 | **성공** | 단일 요청 |
| 09:11:27 | 실패 | 동시 호출 |
| 09:11:32 | **성공** | 단일 요청 |

**패턴:** 동시에 2개 요청이 들어가면 실패, 단일 요청은 성공.

---

## 2. 원인 분석

### 2.1 직접 원인: inquire_balance 중복 호출

동일한 `inquire_balance` 엔드포인트가 동시에 2번 호출됨:

**경로 1 — 대시보드 (5초 자동 새로고침)**
```
GET /api/account  → orch._broker.get_account_summary()  → HTTP GET inquire_balance
GET /api/positions → orch._broker.get_positions()       → HTTP GET inquire_balance  ← 동시!
```

**경로 2 — 매매 사이클 (10초 주기)**
```python
# orchestrator.py:197
account = await self._broker.get_account_summary()  # inquire_balance

# orchestrator.py:480
return await self._broker.get_positions()            # inquire_balance (동일 사이클)
```

### 2.2 근본 원인 (추정, 90% 신뢰도)

**KIS API Rate Limiting (HTTP 429 Too Many Requests)**

근거:
1. 시세 조회 API (inquire_price, inquire_daily_chart)는 **대부분 성공** → quotation endpoint는 제한이 느슨함
2. 잔고 조회 API (inquire_balance)만 **간헐적 실패** → trading endpoint는 제한이 엄격함
3. **단일 요청은 성공, 동시 요청은 실패**하는 명확한 상관관계
4. KIS 공지사항: **"[중요] 한국투자증권 Open API 신규 고객 초당 호출 제한 안내 (2026.03.20)"**

잠재적 다른 원인:
- HTTP 401: 가능성 낮음 (토큰 발급 성공, 시세 API는 정상)
- HTTP 503: 가능성 낮음 (일부 요청은 성공하므로)
- Hashkey 누락: 일부 KIS TR_ID는 hashkey 필수. 하지만 본 계획서에서 추가 확인 필요

### 2.3 2차 문제: 에러 로깅 부족

`client.py` 124~125번 줄:
```python
except httpx.HTTPError as e:
    logger.error(f"KIS GET {endpoint_name} HTTP 실패: {type(e).__name__}")
```

`type(e).__name__`만 출력할 뿐 **실제 HTTP 상태 코드**(401/429/503 등)와 응답 본문을 기록하지 않아 정확한 원인 파악이 불가능.

---

## 3. 수정 방안

### 3.1 선택지 A: short-TTL 캐싱 (추천)

**설명:** `inquire_balance` 응답을 KisAdapter 레벨에서 1~2초간 캐싱. 동일한 100ms 내에 들어온 2번째 요청은 실제 API 호출 없이 캐시 반환.

**수정 파일:** `data/adapters/kis/adapter.py`

```
KisAdapter
├── _balance_cache: dict {
│     "summary": (timestamp, AccountSummary),
│     "positions": (timestamp, list[Position])
│   }
├── get_account_summary() → 캐시 확인 → 미스 시 HTTP → 캐시 저장
├── get_positions()       → 캐시 확인 → 미스 시 HTTP → 캐시 저장
```

**장점:**
- 가장 적은 코드 변경 (1개 파일, ~30줄 추가)
- API 호출 횟수를 실질적으로 절반으로 줄임
- KIS Rate Limit에 즉시 대응
- 기존 로직 변경 없음 (투명한 캐싱)

**단점:**
- 캐시 TTL 동안 데이터가 약간 지연될 수 있음 (1~2초)
- 인메모리 캐시라 프로세스 재시작 시 초기화

**위험도:** LOW

---

### 3.2 선택지 B: 에러 로깅 개선 + 캐싱 (강력 추천)

**설명:** 선택지 A + `client.py`에 HTTP 상태 코드와 응답 본문 로깅 추가.

**추가 수정 파일:** `data/adapters/kis/client.py`

**수정 포인트:**
```python
# client.py before (line 124-125)
except httpx.HTTPError as e:
    logger.error(f"KIS GET {endpoint_name} HTTP 실패: {type(e).__name__}")

# client.py after
except httpx.HTTPStatusError as e:
    logger.error(
        f"KIS GET {endpoint_name} 실패: HTTP {e.response.status_code} "
        f"body={e.response.text[:500]}"
    )
except httpx.TimeoutException as e:
    logger.error(f"KIS GET {endpoint_name} 타임아웃")
except httpx.HTTPError as e:
    logger.error(f"KIS GET {endpoint_name} HTTP 실패: {type(e).__name__}")
```

**장점:**
- HTTP 429인지 503인지 401인지 **정확히 진단 가능**
- 이후 추가 조치 방향 결정 가능
- 선택지 A의 모든 장점 포함

**단점:**
- 2개 파일 수정 필요
- KIS 응답 본문에敏感 정보 없어야 함 (token, account_no 확인 필요)

**위험도:** LOW (추가 로깅만으로 즉시 진단 가능)

---

### 3.3 선택지 C: 단일 API 엔드포인트 병합 (고려)

**설명:** 대시보드의 `/api/account`와 `/api/positions`를 하나의 `/api/account-overview`로 통합하여 1회의 `inquire_balance` 호출로 두 데이터를 모두 반환.

**수정 파일:** `dashboard/backend/api.py`, 프론트엔드 JS

**장점:**
- 근본적으로 중복 호출 제거
- 네트워크 효율 향상

**단점:**
- 프론트엔드 수정 필요 (React/Vue 상태 관리 변경)
- 대시보드의 두 API를 별도로 사용하는 다른 클라이언트(텔레그램 봇)에 영향
- 변경 범위가 큼
- 캐싱 없이 자체로는 완전한 해결책이 아님 (매매 사이클에서도 2번 호출됨)

**위험도:** MEDIUM

---

## 4. 권장안

> **B → A 순서로 적용 권장**

| 단계 | 작업 | 난이도 | 효과 | 위험 |
|------|------|--------|------|------|
| 1 | 먼저 client.py 로깅 개선 (선택지 B의 로깅 부분만) | 하 | 정확한 상태 코드 확인 | 없음 |
| 2 | HTTP 429 확인 후 → adapter.py 캐싱 적용 (선택지 A) | 하 | API 호출 50% 감소 | 낮음 |
| 3 | 선택적으로 → WebSocket 재연결 로직 개선 | 중 | 안정적 실시간 시세 | 중간 |

---

## 5. 구현 상세 (선택지 B)

### 5.1 client.py 로깅 개선

```python
# data/adapters/kis/client.py
# before (line 121-126)
try:
    resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
    resp.raise_for_status()
except httpx.HTTPError as e:
    logger.error(f"KIS GET {endpoint_name} HTTP 실패: {type(e).__name__}")
    raise BrokerError(f"KIS HTTP error on {endpoint_name}: {type(e).__name__}") from e

# after
try:
    resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
    resp.raise_for_status()
except httpx.HTTPStatusError as e:
    status = e.response.status_code
    snippet = (e.response.text or "")[:300]
    logger.error(
        f"KIS GET {endpoint_name} HTTP {status}: "
        f"body={snippet}"
    )
    raise BrokerError(
        f"KIS HTTP error on {endpoint_name}: HTTP {status}"
    ) from e
except httpx.TimeoutException as e:
    logger.error(f"KIS GET {endpoint_name} 타임아웃")
    raise BrokerError(f"KIS HTTP timeout on {endpoint_name}") from e
except httpx.HTTPError as e:
    logger.error(f"KIS GET {endpoint_name} HTTP 실패: {type(e).__name__}")
    raise BrokerError(f"KIS HTTP error on {endpoint_name}: {type(e).__name__}") from e
```

(POST 메서드에도 동일한 로깅 개선 적용)

### 5.2 adapter.py 캐싱

```python
# data/adapters/kis/adapter.py
import time

_BALANCE_CACHE_TTL = 1.5  # seconds

class KisAdapter(BrokerAdapter):
    def __init__(self, ...):
        ...
        self._balance_cache: dict[str, tuple[float, Any]] = {}  # key → (timestamp, data)

    async def _cached_balance(self, key: str, fetcher):
        """1.5초 TTL 캐시로 inquire_balance 중복 호출 방지"""
        now = time.monotonic()
        if key in self._balance_cache:
            ts, data = self._balance_cache[key]
            if now - ts < _BALANCE_CACHE_TTL:
                logger.debug(f"Balance cache hit: {key} ({now-ts:.1f}s old)")
                return data
        data = await fetcher()
        self._balance_cache[key] = (now, data)
        return data

    async def get_account_summary(self) -> AccountSummary:
        async def _fetch():
            params = { ... }
            resp = await self._http.get("inquire_balance", params=params)
            if not resp.success:
                raise BrokerError(...)
            return parse_account_summary(resp.raw)
        return await self._cached_balance("summary", _fetch)

    async def get_positions(self) -> list[Position]:
        async def _fetch():
            params = { ... }
            resp = await self._http.get("inquire_balance", params=params)
            if not resp.success:
                raise BrokerError(...)
            return parse_positions(resp.raw)
        return await self._cached_balance("positions", _fetch)
```

---

## 6. 리스크 분석

### 6.1 리스크 매트릭스

| 리스크 | 영향 | 확률 | 대응 |
|--------|------|------|------|
| 캐시 TTL 동안 계좌 변경사항 미반영 | 사용자 경험 저하 (1~2초 지연) | 낮음 | TTL을 1.5초로 설정, 1초 이하로도 충분 |
| 캐시 누적으로 메모리 사용 증가 | 무시 가능 (< 1KB) | 낮음 | 2개 키만 저장, 추가 리스크 없음 |
| 캐시와 실제 잔고 불일치로 잘못된 주문 판단 | 매매 오류 가능성 | **극히 낮음** | 캐시는 대시보드 표시용. 실제 주문 판단은 trading cycle에서도 동일 API 호출하므로 캐시와 무관 |
| HTTP 429가 아닌 다른 원인(예: hashkey 필요) | 캐싱만으로 해결 안 됨 | 중간 | **선택지 B의 로깅 개선으로 먼저 확인 후 진행 권장** |

### 6.2 Hashkey 가능성 검토

KIS API 2023년 이후 일부 TR_ID는 Hashkey 필수. `inquire_balance` (TTTC8434R)가 hashkey를 요구할 가능성:

- 시세 조회 TR_ID (FHKST03010100 등) → hashkey 불필요 ✅ (시세 API는 정상)
- 잔고 조회 TR_ID (TTTC8434R) → hashkey **필요할 가능성 있음** ❓

**조치:** 선택지 B의 로깅 개선을 1순위로 적용하여 HTTP 상태 코드를 확인. HTTP 400번대라면 hashkey 가능성 검토.

### 6.3 WebSocket 연결 타임아웃 (참고)

별도 확인된 `KIS WebSocket 연결 실패: timed out during opening handshake`는 인프라 문제일 가능성이 높음 (WSL 네트워크, 방화벽, 또는 KIS WebSocket 서버 상태). 본 이슈와 직접적 연관은 낮음.

---

## 7. 테스트 계획

### 7.1 사전 확인 (로깅 개선만 우선 적용)

1. HTTP 상태 코드 확인: `client.py` 패치 후 30분간 로그 모니터링
2. `HTTP 429` 확인 → 캐싱 적용 진행
3. 그 외 상태 코드 → 해당 원인에 맞는 추가 조치

### 7.2 캐싱 적용 후 검증

| 테스트 | 방법 | 기준 |
|--------|------|------|
| 중복 호출 제거 | 5초간 대시보드 새로고침 + 로그 확인 | inquire_balance 호출이 2회 연속 아닌 1회로 감소 |
| Rate Limit 해소 | 1시간 연속 모니터링 | HTTPStatusError 0건 |
| 데이터 정합성 | 캐시 히트 시 잔고/포지션 정확도 | 실제 잔고와 차이 없음 |
| 폴백 동작 | TTL 초과 시 새로 호출하는지 확인 | 정상 |

### 7.3 통합 테스트

```bash
cd /home/psh19/NGSAT
source .venv/bin/activate
pytest tests/test_live/test_dashboard_api.py -v -k "account or position"
pytest tests/test_live/test_orchestrator.py -v
```

---

## 8. 실행 일정 (예상)

| 단계 | 작업 | 예상 시간 |
|------|------|----------|
| 1 | client.py 로깅 개선 + 테스트 | 15분 |
| 2 | 로그 모니터링 (상태 코드 확인) | 10분 |
| 3 | adapter.py 캐싱 구현 | 20분 |
| 4 | 단위 테스트 + 통합 테스트 | 15분 |
| 5 | 재시작 + 실시간 모니터링 | 15분 |
| **합계** | | **~75분** |
