# NGSAT Phase B 실행계획서 — 리스크 검토 포함

> 기준: psh19833/NGSAT main (`7220284`)
> Phase A 완료: Rate Limit·Scaler·.env·_last_auc·폴링로딩 (5항목)

---

## Phase B 개요

**9개 항목, 예상 총 소요시간: 약 10~14일 (개발 기준)**
**공통 리스크**: 모든 항목이 Phase A 수정 완료 상태에서 독립적으로 진행 가능. 순서 의존성 없음.

---

## B-1. 미체결 주문 재시도 — 지수 백오프

### 상세

- **목적**: `BrokerError` 발생 시 로깅만 하고 종료하는 현재 구조에서, 일시적 네트워크 오류/API 장애 시 자동 재시도
- **대상 파일**: `live/executor.py` — `execute_buy()` / `execute_sell()`
- **방법**:
  ```python
  MAX_RETRIES = 3
  BASE_DELAY = 1.0  # 초
  for attempt in range(MAX_RETRIES):
      try:
          return await self._broker.submit_order(...)
      except BrokerError as e:
          if attempt == MAX_RETRIES - 1:
              raise  # 마지막 시도 실패 → 상위 전파
          delay = BASE_DELAY * (2 ** attempt)  # 1s → 2s → 4s
          logger.warning(f"주문 재시도 {attempt+1}/{MAX_RETRIES}: {e}")
          await asyncio.sleep(delay)
  ```

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **LOW** |
| **위험 요소** | 재시도로 인한 중복 주문 가능성 (KIS가 멱등성을 보장하지 않는 경우) |
| **완화 방안** | `attempt == 0`일 때만 `submit_order()` 호출, 이후에는 `get_order_status()`로 확인 후 필요시 재발행 |
| **롤백** | executor.py만 복원 (git checkout) |
| **검증 방법** | 네트워크 차단 후 재시도 로그 확인, 실제 주문 중복 없는지 KIS 계좌 조회 |

### 우선순위: Medium
Phase A에서 Rate Limit을 조정했으므로, 재시도 로직은 B-2(WebSocket) 이후가 적절.

---

## B-2. WebSocket 실시간 푸시

### 상세

- **목적**: `/ws/realtime` 엔드포인트가 ping/pong만 처리 → 실제 거래 체결/상태 변경/리스크 경고 푸시
- **대상 파일**: `dashboard/backend/api.py`, `App.jsx`
- **방법**:
  1. 백엔드: `asyncio.Queue` 기반 이벤트 브로드캐스트 구현
  2. 연결된 모든 WebSocket 클라이언트에 이벤트 전파
  3. 프론트엔드: WebSocket `onmessage` 핸들러에서 개별 state 갱신
  4. 폴링 제거 or 보조 수단으로 전환

```python
# api.py — 브로드캐스트 패턴
connected_ws: set[WebSocket] = set()

@app.websocket("/ws/realtime")
async def ws_realtime(ws: WebSocket):
    await ws.accept()
    connected_ws.add(ws)
    try:
        while True:
            data = await ws.receive_text()
            if data == "ping":
                await ws.send_text("pong")
    finally:
        connected_ws.discard(ws)

# 주문 체결 시 브로드캐스트
async def broadcast(event: dict):
    dead = set()
    for ws in connected_ws:
        try:
            await ws.send_json(event)
        except Exception:
            dead.add(ws)
    connected_ws -= dead
```

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **MEDIUM** |
| **위험 요소 1** | WebSocket 연결 급증 시 서버 메모리 증가 (연결당 ~2KB) |
| **완화** | `max_connections` 제한 (기본 100) + 헬스체크 추가 |
| **위험 요소 2** | 폴링과 동시 운영 시 중복 갱신으로 깜빡임 |
| **완화** | WebSocket 연결 시 폴링 타이머 중단, 해제 시 재시작 |
| **위험 요소 3** | 브로드캐스트 예외로 인한 연결 누수 |
| **완화** | `try/finally`로 항상 `connected_ws.discard()` 실행 |
| **롤백** | api.py + App.jsx 복원, 폴링 복구 |
| **검증 방법** | 브라우저 WebSocket 탭에서 메시지 수신 확인 |

### 우선순위: Medium
UX 개선 효과는 크지만, 운영 안정성(B-1)보다 후순위.

---

## B-3. ML 피처 확장 — 수급 + 재무 데이터

### 상세

- **목적**: 현재 기술적 지표(RSI, 볼린저, MA)만 사용 → 외국인/기관 순매수, 재무 데이터 추가
- **대상 파일**: `ml/features/builder.py`, `data/adapters/kis/`
- **방법**:
  1. KIS API에서 외국인 순매수 데이터 조회 (`inquire_foreign_investor`)
  2. 재무 데이터 조회 (PER, PBR, EPS, 매출성장률)
  3. `FeatureBuilder`에 새로운 피처 파이프라인 추가
  4. 기존 모델과의 호환성 유지 (새 피처는 Optional)

```python
# ml/features/builder.py — 추가 예시
features["foreign_net_buy_5d"] = foreign_net_buy_5d
features["foreign_holding_pct"] = foreign_holding_pct
features["per"] = per_value
features["pbr"] = pbr_value
features["revenue_growth_q"] = revenue_growth_q
```

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **HIGH** |
| **위험 요소 1** | 새 피처 추가로 기존 모델의 `predict_proba()` 입력 차원 불일치 → Model 재학습 필수 |
| **완화** | 새 피처를 `feature_cols`에 추가 전, 기존 모델과의 호환성 레이어 구현. 새 피처를 `Optional`로 처리하지 않고 `forward_fill(0)`으로 기본값 채움 |
| **위험 요소 2** | KIS 외국인/재무 API Rate Limit 추가 부담 (B-1에서 해결 전) |
| **완화** | 재무 데이터는 1일 1회만 조회 (변경 빈도 낮음) |
| **위험 요소 3** | 재무 데이터가 없는 종목(코스닥 일부)에서 NaN 피처 발생 |
| **완화** | `SimpleImputer(strategy='median')` 또는 해당 종목 제외 |
| **롤백** | builder.py 복원, 이전 모델 파일로 교체 |
| **소요 시간** | **3~5일** (KIS API 연동 1일, 피처 파이프라인 1일, 모델 재학습+검증 1~3일) |

### 우선순위: Low
시간 대비 리스크가 가장 높은 항목. Phase C로 이관 권장.

---

## B-4. save_minute_bars Exception 범위 축소

### 상세

- **목적**: `except Exception` → `except IntegrityError`로 축소
- **대상 파일**: `data/repository.py` — `save_minute_bar()`, `save_minute_bars()`
- **방법**:
  ```python
  # AS-IS
  except Exception:
      self._session.rollback()
  
  # TO-BE
  from sqlalchemy.exc import IntegrityError
  except IntegrityError:
      self._session.rollback()
  ```

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **ZERO** |
| **위험 요소** | 없음 — IntegrityError만 캐치, 실제 오류는 전파되어 로그에 표시 |
| **롤백** | 2줄 복원 |
| **검증 방법** | 중복 분봉 저장 시 정상 skip, 잘못된 데이터 저장 시 오류 로그 확인 |
| **소요 시간** | **15분** |

### 우선순위: **🔜 다음 진행 (Highest)**
리스크 0, 15분 소요. 바로 가능.

---

## B-5. 동적 Tailwind → 정적 COLOR_MAP

### 상세

- **목적**: `bg-${btn.color}/10` 템플릿 리터럴 → 정적 `COLOR_MAP` 객체
- **대상 파일**: `dashboard/frontend/src/components/ControlPanel.jsx`, `tailwind.config.js` (safelist 정리)
- **방법**:
  ```javascript
  const COLOR_MAP = {
    'ngsat-green': 'bg-ngsat-green/10 text-ngsat-green border-ngsat-green/20 hover:bg-ngsat-green/20',
    'ngsat-red': 'bg-ngsat-red/10 text-ngsat-red border-ngsat-red/20 hover:bg-ngsat-red/20',
    'ngsat-yellow': 'bg-ngsat-yellow/10 text-ngsat-yellow border-ngsat-yellow/20 hover:bg-ngsat-yellow/20',
    'ngsat-blue': 'bg-ngsat-blue/10 text-ngsat-blue border-ngsat-blue/20 hover:bg-ngsat-blue/20',
    'ngsat-purple': 'bg-ngsat-purple/10 text-ngsat-purple border-ngsat-purple/20 hover:bg-ngsat-purple/20',
  }
  // usage: className={COLOR_MAP[btn.color] || ''}
  ```

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **LOW** |
| **위험 요소** | `COLOR_MAP`에 없는 색상 사용 시 버튼 스타일 없음 |
| **완화** | `|| ''` fallback + `btn.color` 값 검증 로그 |
| **롤백** | ControlPanel.jsx + tailwind.config.js 복원 |
| **소요 시간** | **1시간** |

### 우선순위: High
safelist 의존성 제거. 버튼 UI 안정성 직결.

---

## B-6. ATR 기반 동적 포지션 사이징

### 상세

- **목적**: 모드별 고정 포지션 크기(스윙 10%/단타 5%) → ATR로 조정
- **대상 파일**: `live/executor.py` 또는 `live/orchestrator.py`
- **방법**:
  ```python
  # 기존: size_pct = 0.05 if is_short_term else 0.10
  # 변경:
  base_pct = 0.05 if is_short_term else 0.10
  atr_pct = estimate_atr(prices)  # 최근 14일 ATR / 현재가
  target_risk_pct = 0.02  # 포트폴리오 대비 목표 리스크 2%
  size_pct = base_pct * (target_risk_pct / max(atr_pct, 0.005))
  size_pct = min(size_pct, base_pct * 2)  # 최대 2배
  ```

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **MEDIUM** |
| **위험 요소 1** | ATR 급등 시 포지션 0으로 축소 → 기회 손실 |
| **완화** | `min_pct = base_pct * 0.3` 하한 설정 (완전히 배제되지 않음) |
| **위험 요소 2** | ATR 계산 오류 시 부자연스러운 포지션 크기 |
| **완화** | 이상치 클리핑 (`percentile` 기반) |
| **소요 시간** | **2일** |

### 우선순위: Medium
리스크 관리 개선이나, B-4/B-5보다 영향 범위 큼.

---

## B-7. pnlColor 부호 컨벤션 검증

### 상세

- **목적**: `pnlColor(-account.daily_loss)`의 부호 일관성 확인 및 수정
- **대상 파일**: `dashboard/frontend/src/utils.js`, `AccountCard.jsx`
- **방법**:
  1. KIS API 응답에서 `daily_loss` 부호 컨벤션 확인 (양수=손실 or 음수=손실)
  2. `total_profit_loss`와 동일한 컨벤션인지 확인
  3. 불일치 시 `-` 부호 제거 또는 `pnlColor()` 내에서 절대값 처리

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **LOW** |
| **위험 요소** | API 컨벤션 확인 없이 수정 시 부호 반전 |
| **완화** | API 응답에서 `daily_loss` 값 실제 확인 후 수정 |
| **롤백** | 1줄 복원 |
| **소요 시간** | **30분** |

### 우선순위: **🔜 다음 진행 (High)**
빠르고 안전. B-4와 함께 즉시 진행 가능.

---

## B-8. 사이드바 아이콘 Lucide 통일

### 상세

- **목적**: 유니코드(◈₩⊞≡⊙) + 이모지(🔍⚙) → Lucide React SVG 아이콘
- **대상 파일**: `dashboard/frontend/src/components/Sidebar.jsx`
- **방법**:
  1. `npm install lucide-react`
  2. 아이콘 매핑: `LayoutDashboard`, `Wallet`, `BarChart3`, `ListOrdered`, `Settings`, `Search`, `SlidersHorizontal`

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **LOW** |
| **위험 요소** | 빌드 실패 (npm install 실패 시) |
| **완화** | fallback 유니코드 문자 유지 |
| **소요 시간** | **1일** |

### 우선순위: Low
디자인 개선. 기능 영향 없음.

---

## B-9. pyproject.toml + pre-commit 훅

### 상세

- **목적**: lint/format 도구 설정 표준화, 커밋 전 자동 검사
- **대상 파일**: `pyproject.toml` (신규 생성)
- **방법**:
  ```toml
  [tool.flake8]
  max-line-length = 88
  extend-ignore = ["E203", "W503"]
  
  [tool.pylint]
  max-line-length = 88
  
  [tool.ruff]
  line-length = 88
  
  [tool.pytest.ini_options]
  testpaths = ["tests"]
  ```

### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크 등급** | **ZERO** |
| **위험 요소** | 없음 — 설정 파일만 추가, 기존 코드 변경 없음 |
| **롤백** | 파일 삭제 |
| **소요 시간** | **1일** (도구 학습 포함) |

### 우선순위: **High**
다른 모든 개발 작업의 품질 기준. B-4/B-7 직후 진행.

---

## 실행 순서 권장

```
Phase B 실행 순서 (리스크 오름차순)
═══════════════════════════════════

B-4 (ZERO, 15분) ─────────────── 🔜 save_minute_bars Exception 축소
  ↓
B-7 (LOW, 30분) ───────────────── 🔜 pnlColor 부호 검증
  ↓
B-9 (ZERO, 1일) ───────────────── 🔜 pyproject.toml + pre-commit
  ↓
B-5 (LOW, 1시간) ──────────────── 🔜 동적 Tailwind → 정적 COLOR_MAP
  ↓
B-1 (LOW, 1일) ────────────────── 🔜 미체결 주문 재시도
  ↓
B-8 (LOW, 1일) ────────────────── 🔜 사이드바 아이콘 Lucide
  ↓
B-6 (MEDIUM, 2일) ─────────────── 🔜 ATR 기반 포지션 사이징
  ↓
B-2 (MEDIUM, 3일) ─────────────── 🔜 WebSocket 실시간 푸시
  ↓
B-3 (HIGH, 3~5일) ─────────────── 🔜 ML 피처 확장 (Phase C 이관 권장)

필요시 B-4+B-7+B-9만 먼저 진행 (리스크 0, 약 2일)
→ 나머지는 운영 상태 보며 결정
```

---

## 통합 리스크 매트릭스

| 순번 | 항목 | 리스크 | 시간 | 기술부채 | UX영향 | 운영영향 |
|------|------|--------|------|---------|--------|---------|
| **B-4** | Exception 축소 | ZERO | 15분 | ✅ 해소 | — | 🟢 |
| **B-7** | pnlColor 부호 | LOW | 30분 | ✅ 해소 | 🟢 | — |
| **B-9** | pyproject.toml | ZERO | 1일 | ✅ 해소 | — | 🟢 |
| **B-5** | Tailwind 정적화 | LOW | 1시간 | ✅ 해소 | 🟢 | — |
| **B-1** | 주문 재시도 | LOW | 1일 | — | — | 🟢🟢 |
| **B-8** | 아이콘 통일 | LOW | 1일 | — | 🟢 | — |
| **B-6** | ATR 사이징 | MEDIUM | 2일 | — | — | 🟢🟢 |
| **B-2** | WebSocket | MEDIUM | 3일 | ✅ 해소 | 🟢🟢 | — |
| **B-3** | ML 피처 | HIGH | 3~5일 | — | — | 🟢🟢🟢 |
