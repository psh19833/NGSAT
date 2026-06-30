# Rate Limit 잔여 버그 해결 계획서

> 기준: psh19833/NGSAT main 브랜치 (`0162aca`, 2026-06-30)

---

## 현황 분석

### 증상

1. 대시보드 포지션 메뉴: "보유 포지션이 없습니다" 간헐적 표시
2. 계좌현황: `KIS HTTP error on inquire_balance: HTTP 500` 간헐적 발생

### 이전 수정 (0162aca) — 불완전했던 이유

`get_positions()`가 `get_account_summary()`의 캐시된 raw 응답을 재사용하도록 수정했으나, **fallback 경로가 남아있어 race condition 발생**.

### 여전한 문제: Race Condition

대시보드는 5초마다 모든 API를 **동시에**(Promise.all) 호출합니다:

```
Time 0ms:  /api/account  → get_account_summary() → API 호출 시작 (캐시 미스)
Time 0ms:  /api/positions → get_positions() → _balance_raw_cache 아직 비어있음
                                       → fallback: _cached_balance("positions")
                                       → 캐시 미스 → 별도 API 호출!
Time 10ms: KIS에 2개 요청 동시 도착 → Rate Limit 초과 → HTTP 500
```

`get_account_summary()`가 API 호출을 마치고 `_balance_raw_cache`에 저장하기도 전에, `get_positions()`가 fallback 경로로 자체 API를 호출해버립니다.

### Fallback 경로 제거가 필요한 이유

현재 코드:
```python
async def get_positions(self):
    # 1) 캐시된 raw 응답 확인
    if "summary" in self._balance_raw_cache: ...
    
    # 2) FALLBACK: 자체 API 호출 ← 이 경로가 계속 rate limit 유발
    return await self._cached_balance("positions", _fetch)
```

fallback이 실행되는 조건:
- 최초 호출 시 (캐시 워밍 전)
- 캐시 TTL(5초) 만료 직후 동시 요청
- 서버 재시작 후 첫 요청

---

## 해결 방안

### 핵심: get_positions()가 절대 API를 호출하지 않음

`get_positions()`가 `get_account_summary()`에 위임하여 inquire_balance를 **무조건 1회만** 호출하도록 강제.

```python
async def get_positions(self) -> list[Position]:
    # get_account_summary()를 먼저 호출하여 raw 응답 캐싱 보장
    # (이미 캐시되어 있으면 즉시 반환, 없으면 1회 API 호출)
    await self.get_account_summary()
    
    # 캐시된 raw 응답에서 포지션 파싱 — API 호출 0회
    now = time.monotonic()
    if "summary" in self._balance_raw_cache:
        ts, raw = self._balance_raw_cache["summary"]
        if now - ts < _BALANCE_CACHE_TTL:
            return parse_positions(raw)
    
    # fallback 제거 — 이 경로에 도달하는 것은 버그
    logger.error("get_positions: _balance_raw_cache 누락 — 비정상 상태")
    return []
```

### 변경 사항

| 파일 | 변경 | 영향 |
|------|------|------|
| `adapter.py` | `get_positions()` fallback 제거 → `get_account_summary()` 위임 | **API 호출 무조건 1회** |
| `adapter.py` | `_cached_balance("positions")` 관련 중복 코드 제거 | dead code 정리 |

### 타당성 검증

| 우려 | 해결 |
|------|------|
| `get_account_summary()`가 실패하면? | 예외 전파 → `get_positions()`도 실패. 이전과 동일 |
| `get_account_summary()`가 AccountSummary 반환 → overhead? | 이미 캐시되어 있으면 즉시 반환 (0ms) |
| dashboard가 positions만 필요할 때? | account도 항상 함께 폴링하므로 차이 없음 |

### 리스크

| 항목 | 등급 | 설명 |
|------|:----:|------|
| API 호출 증가 | ZERO | 오히려 2회→1회로 감소 |
| 타입 안전성 | ZERO | raw dict → parse_positions() 동일 |
| 롤백 | ZERO | `git revert` 1회 |
| 기존 동작 변경 | ZERO | 반환 값 동일, 예외 처리 동일 |

### 적용 후 예상 동작

```
Time 0ms:  /api/account  → get_account_summary() → 캐시 미스 → API 호출 (1회)
Time 0ms:  /api/positions → get_positions() → get_account_summary() 호출
                                       → 이미 진행 중인 API 호출 대기 (await)
                                       → 완료 후 캐시된 raw 응답 사용
                                    → API 호출 0회
```

**결과**: 어떤 상황에서도 inquire_balance는 초당 1회만 호출. Rate Limit (EGW00215) 완전 해결.

---

## 진행 계획

| 단계 | 내용 | 소요 |
|------|------|------|
| 1 | adapter.py get_positions() fallback 제거 + 위임 로직 | 5분 |
| 2 | pytest 실행 | 2분 |
| 3 | 서버 재시작 + 검증 | 3분 |
| 4 | 커밋 + 푸시 | 1분 |
