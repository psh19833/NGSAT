# KIS API 장애 원인 분석 보고서

**작성일**: 2026-07-07 09:10 KST  
**상태**: 시스템 running (사이클 #3986) but API 호출 전부 실패 중

---

## 1. 현황

| 항목 | 상태 |
|:-----|:------|
| 서버 | running, 사이클 ~3986 |
| 계좌 조회 | ❌ HTTP 500 (EGW00123 / EGW00215) |
| 미체결 조회 | ❌ HTTP 500 (EGW00123) |
| WebSocket | ❌ 연결→종료 무한 루프 (1초 간격) |
| 레짐 표시 | ⚠️ 장 마감 상태 (API 실패로 미갱신) |
| 대시보드 trades | ❌ `limit=undefined` (브라우저 캐시) |

---

## 2. 로그 분석

### 2.1 WebSocket 재연결 루프 (근본 원인)

```
09:07:21 approval_key 발급 성공
09:07:21 WebSocket 연결됨
09:07:21 WebSocket 연결 종료 — 재연결 시도
09:07:21 재연결 (1초 후...)
09:07:23 approval_key 발급 성공    ← REST API 호출
09:07:23 WebSocket 연결됨
09:07:23 WebSocket 연결 종료 — 재연결 시도
... (무한 반복, 1~2초 간격)
```

**원인**: `websocket_client.py`의 `connect()`가 호출될 때마다 KIS `/oauth2/Approval` 엔드포인트로 **새 approval_key를 발급**받음. WebSocket 연결이 즉시 종료되므로 1~2초마다 approval_key 발급 요청이 발생 → Rate Limit 소진.

`_reconnect()` 메서드(지수 백오프 있음)가 사용되지 않고, 상위 호출부에서 `connect()`를 반복 호출하는 구조.

### 2.2 Token 만료 (EGW00123)

```json
{"rt_cd":"1","msg1":"기간이 만료된 token 입니다.","msg_cd":"EGW00123"}
```

**원인**: KIS access token(OAuth2)이 만료되었으나 로컬 캐시(`KisTokenManager._cached_token`)가 만료를 감지하지 못함. `token_manager.py:41-44`의 `is_expired` 체크에 5분 마진이 있으나, 서버-클라이언트 간 시각 차이 또는 KIS 측 토큰 폐기로 인해 캐시된 토큰이 무효화됨.

`_build_headers()` → `get_token()` → 캐시 유효하다고 판단 → 만료 토큰 사용 → EGW00123

### 2.3 Rate Limit (EGW00215)

```json
{"rt_cd":"1","msg1":"원장에서 허용 가능한 초당 거래건수를 초과하였습니다.","msg_cd":"EGW00215"}
```

**원인**: WebSocket 재연결 approval_key 발급(1~2초 간격) + 사이클 REST API 호출(inquire_balance, inquire_unfilled 등)이 KIS 제한 초과.

### 2.4 프론트엔드 trades API 422

```
GET /api/trades?limit=undefined&offset=undefined HTTP/1.1" 422
```

**원인**: 브라우저가 이전 JavaScript 번들을 캐싱. `PAGE_SIZE` 중복 선언을 제거한 최신 빌드가 아닌, 이전 빌드가 서빙됨. Ctrl+F5 필요.

---

## 3. 문제 계통도

```
WebSocket 즉시 종료
  → connect() 반복 호출
    → approval_key 발급 REST API 호출 (1~2초 간격)
      → KIS Rate Limit 소진 (EGW00215)
        → inquire_balance 실패 (EGW00215)
        → inquire_unfilled 실패 (EGW00123)
          → 계좌/포지션 데이터 없음
            → 레짐/사이클 정보 갱신 불가
```

---

## 4. 수정 권장 사항 (수정 금지, 참고용)

### 4.1 WebSocket 재연결 (근본 원인, 긴급)

| 문제 | `connect()`가 호출될 때마다 새 approval_key 발급 |
|:-----|:------|
| 해결 | WebSocket approval_key를 **token manager와 통합**하여 캐싱. 연결 종료 시 `connect()` 재호출 대신 `_reconnect()`(지수 백오프 내장) 사용. 또는 approval_key를 최초 1회만 발급하고 이후 재사용. |
| 예상 효과 | Rate Limit 호출 1/100 수준으로 감소 |

### 4.2 Token 만료 EGW00123

| 문제 | KIS OAuth token 만료 후에도 로컬 캐시가 유효하다고 판단 |
|:-----|:------|
| 해결 | `EGW00123` 응답 수신 시 `KisTokenManager._cached_token`을 강제 무효화하고 새 토큰 발급 후 재시도 (`client.py`의 `get()`/`post()`에 재시도 로직 추가). |
| 예상 효과 | 토큰 만료 후 1회 재시도로 자동 복구 |

### 4.3 프론트엔드 캐시

| 문제 | 브라우저가 이전 JS 번들 사용 |
|:-----|:------|
| 해결 | Vite 빌드 시 파일명에 해시 포함 (자동) + 사용자 Ctrl+F5 안내 |
| 예상 효과 | 새로고침으로 해결 |

---

## 5. 현재 영향

| 항목 | 영향도 | 설명 |
|:-----|:------:|:------|
| 실거래 매매 | **중단** | 계좌/잔고/포지션 조회 불가 → 신규 진입/청산 불가 |
| WebSocket 시세 | **중단** | 실시간 가격 수신 불가 |
| 대시보드 | **표시 오류** | 계좌/포지션 데이터 없음 |
| Telegram 알림 | **영향 없음** | 메시지 발송 자체는 별도 채널 |
