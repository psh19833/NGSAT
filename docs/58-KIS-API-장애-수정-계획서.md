# KIS API 장애 수정 계획서

**기반 문서**: `docs/57-KIS-API-장애-원인분석.md`  
**수정 금지 사항**: 없음 (모든 수정은 프론트엔드/통신 레이어, 매매 전략 비변경)

---

## 1. 문제 재정의

### 1.1 WebSocket 재연결 루프 (근본 원인)

WebSocket 연결이 즉시 종료되는 현상. `connect()`가 호출될 때마다 KIS `/oauth2/Approval`로 새 approval_key 발급 → 1~2초 간격 REST API 호출로 Rate Limit 소진.

### 1.2 Token 만료 미대응

OAuth2 access token 만료 시 EGW00123 응답이 오나, 클라이언트가 이를 감지하고 재발급하는 로직 없음. `client.py`의 `get()/post()`가 HTTP 500을 받으면 즉시 BrokerError throw.

### 1.3 Rate Limit 소진

WebSocket approval_key 발급(1~2초 간격) + 사이클 REST API 호출이 KIS 허용치 초과.

### 1.4 프론트엔드 trades API `limit=undefined`

브라우저 캐시로 이전 JS 번들 사용. Vite 빌드 파일명 해시로 자동 처리되나, 기존 캐시 만료 필요.

---

## 2. 수정 방안

### 2.1 A안: WebSocket approval_key 캐싱 + 재연결 개선 (WebSocket 클라이언트)

**대상 파일**: `data/adapters/kis/websocket_client.py`

**변경 내용**:

```python
# 1. approval_key를 token manager와 통합 (최초 1회만 발급)
async def connect(self):
    if not self._approval_key:  # 최초 1회만 발급
        self._approval_key = await self._get_approval_key()
    # WebSocket 연결
    ...

# 2. connect() 실패 시 _reconnect() 사용 (지수 백오프)
# connect() 내부에서 재시도하지 않고 listen()의 _reconnect()에 위임

# 3. WebSocket 종료 원인 로깅 강화
# "연결 종료"에 close code + reason 추가
```

**변경량**: ~10줄  
**리스크**: 낮음 (approval_key 캐싱만 변경, 연결 로직 동일)

#### 2.1.1 상세

현재 `connect()`가 호출될 때마다 `_get_approval_key()`를 실행함:

```python
async def connect(self):
    self._approval_key = await self._get_approval_key()  # ← 매번 실행
    ...
```

수정:
```python
async def connect(self):
    if self._approval_key is None:  # 최초 1회만
        self._approval_key = await self._get_approval_key()
    ...
```

그리고 `listen()` 루프에서 연결 종료 시 `connect()` 재호출 대신 `_reconnect()`를 통해 approval_key 유지 + 지수 백오프 적용.

### 2.2 B안: Token 만료 자동 재발급 (HTTP 클라이언트)

**대상 파일**: `data/adapters/kis/client.py`

**변경 내용**:

```python
async def get(self, endpoint_name, params=None, extra_headers=None):
    ...
    try:
        resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        body = e.response.text or ""
        if "EGW00123" in body:  # token expired
            logger.info(f"토큰 만료 감지 — 재발급 후 재시도: {endpoint_name}")
            # 토큰 캐시 무효화
            self._token_manager.invalidate()
            # 새 토큰으로 헤더 재생성
            headers = await self._build_headers(ep, extra_headers)
            # 재시도 (1회)
            resp = await client.get(url, params=params, headers=headers, timeout=self._timeout)
            resp.raise_for_status()
        else:
            raise BrokerError(...)
    ...
```

**변경량**: ~15줄  
**리스크**: 낮음 (EGW00123 감지 시 1회 재시도, 실패 시 기존대로 BrokerError)

#### 2.2.1 token_manager에 `invalidate()` 추가

```python
def invalidate(self) -> None:
    """토큰 강제 무효화 — EGW00123 수신 시 호출."""
    self._cached_token = None
    # 디스크 캐시도 삭제
    if self._CACHE_FILE.exists():
        self._CACHE_FILE.unlink()
```

**변경량**: ~8줄  
**리스크**: 없음

### 2.3 C안: 프론트엔드 캐시 갱신

**대상**: `dashboard/frontend/`

**변경 내용**:
- Vite 빌드 시 output 파일명에 content hash 자동 포함 (이미 적용됨)
- `index.html`에 `<meta http-equiv="Cache-Control" content="no-cache">` 추가
- (선택) 서버 응답 헤더에 `Cache-Control: no-cache` 설정

**변경량**: 1행  
**리스크**: 없음

---

## 3. 리스크 분석

| 안 | 변경량 | 리스크 | 설명 |
|:-:|:------:|:------:|:------|
| A안 | ~10줄 | 낮음 | approval_key 캐싱만 추가. WebSocket 자체 로직 변경 없음 |
| B안 | ~23줄 | 낮음 | EGW00123 감지 시 1회 재시도. 실패 시 기존 BrokerError 유지 |
| C안 | 1행 | 없음 | meta 태그 추가 |

**종합 리스크**: 매우 낮음. 모든 변경은 실패 시 기존 동작(fallback) 유지.

---

## 4. 실행 계획

### Phase 1: A안 — WebSocket approval_key 캐싱 (1순위)

| 단계 | 파일 | 내용 | 예상 시간 |
|:----:|:-----|:------|:---------:|
| 1 | `websocket_client.py` | `connect()`에서 approval_key 최초 1회만 발급 | 2분 |
| 2 | `websocket_client.py` | 연결 종료 시 `_reconnect()` 사용 강제 (지수 백오프) | 3분 |
| 3 | `websocket_client.py` | 종료 원인 로깅 강화 (close code + reason) | 2분 |

### Phase 2: B안 — Token 만료 자동 재발급 (2순위)

| 단계 | 파일 | 내용 | 예상 시간 |
|:----:|:-----|:------|:---------:|
| 4 | `token_manager.py` | `invalidate()` 메서드 추가 (캐시 무효화) | 2분 |
| 5 | `client.py` | `get()`/`post()`에 EGW00123 감지 + 재시도 로직 | 5분 |

### Phase 3: C안 — 프론트엔드 캐시 (3순위)

| 단계 | 파일 | 내용 | 예상 시간 |
|:----:|:-----|:------|:---------:|
| 6 | `index.html` | Cache-Control meta 태그 추가 | 1분 |

---

## 5. 롤백 방안

| 변경 | 롤백 |
|:-----|:------|
| A안: approval_key 캐싱 | 조건문 제거 (원복) |
| B안: client.py 재시도 | `except` 블록 제거 |
| B안: invalidate() | 메서드 제거 (호출부 1행 제거) |
| C안: meta 태그 | 1행 제거 |
