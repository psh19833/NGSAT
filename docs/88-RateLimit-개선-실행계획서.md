# 88. KIS Rate Limit 개선 실행계획서

**버전:** 1.0
**작성일:** 2026-07-17
**상태:** 실행 전

---

## 1. 개요

KIS API 호출 시 `EGW00201 (초당 거래건수 초과)` Rate Limit 오류가 전체 일봉 조회의 **22.4%**에서 발생.
이로 인한 `BrokerError` → `TypeError` 체인이 Telegram으로 오류 메시지를 발송 중.

### 목표
- Rate Limit 실패율: **22.4% → 4% 이하** (82% 개선)
- TypeError 체인: **완전 차단**
- API 부하: **안정적 유지** (KIS 제한 이내)

---

## 2. 리스크 평가

### 🟡 Rate Limiter 파라미터 변경

| 리스크 | 심각도 | 확률 | 대응 |
|--------|--------|------|------|
| refresh_prices 지연 (5.0초 → 9.8초) | 낮음 | 100% | 10분 주기 대비 0.8% 증가, 영향 미미 |
| WebSocket/REST 동시 호출 경합 | 낮음 | 30% | WS는 별도 연결, 영향 없음 |
| burst=5로 급증 트래픽 대응 불가 | 중간 | 5% | 100종목 sequential 호출이므로 burst 부족 없음 |

### 🟡 EGW00201 재시도 추가

| 리스크 | 심각도 | 확률 | 대응 |
|--------|--------|------|------|
| 재시도로 인한 추가 부하 (+18%) | 낮음 | 100% | rate 10/sec로 여유 충분 |
| 재시도 중복 주문 | 높음 | 0% | 조회(GET) 전용, 주문과 무관 |
| 무한 재시도 루프 | 높음 | 0% | 1회만 재시도, 실패 시 BrokerError 정상 전파 |

### 🟡 차등 갱신 (active 10min / reserve 30min)

| 리스크 | 심각도 | 확률 | 대응 |
|--------|--------|------|------|
| reserve 편입 시 20분 구형 데이터 | 낮음 | 100% | 일봉 데이터, 20분 내 변화 극히 미미 |
| reserve 종목 갱신 누락 | 낮음 | 10% | 30분마다 갱신, 10분 지연보다 충분히 빠름 |

---

## 3. 실행 계획

### Step 1: Rate Limiter 파라미터 변경
**파일:** `data/adapters/kis/client.py`
**변경:** rate_per_sec=20→10, burst=30→5, max_concurrent=15→5

```python
# 변경 전
self._rate_limiter = KisRateLimiter(
    rate_per_sec=20, burst=30, max_concurrent=15,
)
# 변경 후
self._rate_limiter = KisRateLimiter(
    rate_per_sec=10, burst=5, max_concurrent=5,
)
```

### Step 2: EGW00201 재시도 로직 추가
**파일:** `data/adapters/kis/client.py`
**위치:** `get()` 메서드 HTTPStatusError 처리부 (라인 132-148), `post()` 동일

KIS HTTP 500 + EGW00201 응답 시 1초 대기 후 1회 재시도.
HTTP 500이지만 EGW00201이 아닌 경우(진짜 장애)는 재시도 없이 즉시 BrokerError.

```python
# P-88: Rate Limit(EGW00201) 재시도
if status == 500 and "EGW00201" in snippet:
    logger.warning(f"KIS Rate Limit — 1초 후 재시도: {endpoint_name}")
    await asyncio.sleep(1)
    resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
    resp.raise_for_status()
    return self._parse_response(resp.json(), endpoint_name)
```

### Step 3: 차등 갱신
**파일:** `data/real_data_provider.py`
**변경:** `refresh_prices()`에 active/reserve 구분 로직 추가

UniverseManager에서 active/reserve 구분 정보를 받아:
- active: 10분마다 전량 갱신 (기존과 동일)
- reserve: 30분마다 갱신 (3 cycles 중 1회만 갱신)

### Step 4: 서버 재시작 및 검증
- `restart_server.sh` 실행
- `/api/status` 정상 응답 확인
- 로그에서 EGW00201 재시도 로그 확인

---

## 4. 상세 구현

### 4.1 client.py Rate Limiter

변경 범위 최소화 — 1줄만 수정:
```python
self._rate_limiter = KisRateLimiter(
    rate_per_sec=10, burst=5, max_concurrent=5,
)
```

### 4.2 client.py retry (get 메서드)

```python
except httpx.HTTPStatusError as e:
    status = e.response.status_code
    snippet = (e.response.text or "")[:300]
    logger.error(f"KIS GET {endpoint_name} HTTP {status}: body={snippet}")
    # P-55: 토큰 만료(EGW00123) 시 1회 재발급 후 재시도
    if "EGW00123" in snippet and status == 500:
        ...
    # P-88: Rate Limit(EGW00201) 시 1초 후 1회 재시도
    if "EGW00201" in snippet and status == 500:
        logger.warning(f"KIS Rate Limit — 1초 후 재시도: {endpoint_name}")
        await asyncio.sleep(1)
        resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
        return self._parse_response(resp.json(), endpoint_name)
    raise BrokerError(...)
```

### 4.3 real_data_provider.py 차등 갱신

UniverseManager에 `get_refresh_groups()` 메서드 추가:
```python
def get_refresh_groups(self) -> tuple[list[str], list[str]]:
    """active=고빈도, reserve=저빈도 갱신 대상 반환"""
    return (list(self.active.keys()), list(self.reserve.keys()))
```

refresh_prices에서 30분 카운터로 reserve 갱신 제어:
```python
async def refresh_prices(self):
    ...
    # P-88: 차등 갱신 (active=10분, reserve=30분)
    active_codes, reserve_codes = [], []
    if hasattr(self, '_universe_manager') and self._universe_manager:
        active_codes, reserve_codes = self._universe_manager.get_refresh_groups()
        self._reserve_refresh_counter = getattr(self, '_reserve_refresh_counter', 0) + 1
    ...
    for i, (info, prices) in enumerate(self._universe_cache):
        if reserve_codes and info.code in reserve_codes:
            if self._reserve_refresh_counter % 3 != 0:  # 30분마다 (3/3)
                continue
        # 갱신 로직
```

---

## 5. 검증 계획

| 항목 | 방법 | 기준 |
|------|------|------|
| 서버 기동 | `/health` | 200 OK |
| API 정상 | `/api/status` | `connected: true` |
| Rate Limiter 변경 | 로그 확인 | burst 5, rate 10 적용 |
| 재시도 동작 | Rate Limit 상황 | "KIS Rate Limit — 1초 후 재시도" 로그 |
| 차등 갱신 | 로그 확인 | reserve 30분 간격 갱신 확인 |
| TypeError 차단 | 24h 모니터링 | `not subscriptable` 미발생 |

---

## 6. Rollback Plan

**즉시 rollback 필요 조건:**
- 서버 기동 실패
- `/api/status` 응답 불가
- TypeError 지속 발생

**Rollback 명령어:**
```bash
cd /home/psh19/NGSAT
git checkout -- data/adapters/kis/client.py
git checkout -- data/real_data_provider.py
bash scripts/restart_server.sh
```
