# NGSAT Phase C 실행계획서 — 리스크 검토 포함

> 기준: psh19833/NGSAT main (`f8e4ebc`)
> Phase B 완료: 7개 항목 (B-3→Phase C 이관)

---

## Phase C 개요

**10개 항목, 예상 총 소요시간: 약 15~20일**
B-3(ML 피처 확장)을 Phase C로 이관하여 총 10개 항목.

---

## 실행 순서 (리스크 오름차순)

---
### C-9. GitHub Actions + reviewdog 자동화

#### 목적
PR 생성 시 flake8 + bandit + autoflake 자동 실행, 결과를 PR 코멘트로 표시

#### 방법
```yaml
# .github/workflows/reviewdog.yml
name: reviewdog
on: [pull_request]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install flake8 bandit autoflake
      - uses: reviewdog/action-flake8@v5
        with:
          flake8_args: --config=pyproject.toml
      - uses: reviewdog/action-bandit@v1
```

#### 대상 파일
- `.github/workflows/reviewdog.yml` (신규)

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **LOW** — CI 설정만 추가, 기존 코드 변경 없음 |
| **소요시간** | **1일** (GitHub Actions 학습·디버깅 포함) |
| **전제조건** | GitHub 저장소에 Actions 탭 활성화 |
| **롤백** | workflow 파일 삭제 |
| **확인** | PR 생성 후 Actions 탭에서 실행 확인 |

---
### C-7. Skeleton 로딩

#### 목적
"불러오는 중..." 텍스트 → 카드 형태 스켈레톤 플레이스홀더

#### 방법
```jsx
// SkeletonCard.jsx (신규)
export default function SkeletonCard({ lines = 3 }) {
  return (
    <div className="ngsat-card p-6 animate-pulse space-y-3">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="h-4 bg-ngsat-border rounded w-full" />
      ))}
    </div>
  )
}
```

#### 대상 파일
- `dashboard/frontend/src/components/SkeletonCard.jsx` (신규)
- `App.jsx`, `AccountCard.jsx`, `PositionsTable.jsx`, `TradesTable.jsx`

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **ZERO** — 신규 컴포넌트 추가, 기존 코드 변경 최소 |
| **소요시간** | **1일** (컴포넌트 설계 30분, 적용 2시간, QA 2시간) |
| **롤백** | import 제거 |
| **확인** | 네트워크 속도 제한(Chrome DevTools) 후 스켈레톤 표시 확인 |

---
### C-5. Evidence 시각화

#### 목적
Raw JSON(`JSON.stringify(t.evidence, null, 2)`) → 키-값 리스트 UI

#### 방법
```jsx
// EvidenceBox.jsx (신규)
export default function EvidenceBox({ evidence }) {
  if (!evidence || Object.keys(evidence).length === 0) return null
  return (
    <div className="grid grid-cols-2 gap-2 text-xs mt-2">
      {Object.entries(evidence).slice(0, 8).map(([k, v]) => (
        <div key={k} className="flex justify-between">
          <span className="text-ngsat-muted">{k}</span>
          <span className="text-ngsat-text font-mono">{typeof v === 'number' ? v.toFixed(2) : String(v)}</span>
        </div>
      ))}
    </div>
  )
}
```

#### 대상 파일
- `dashboard/frontend/src/components/EvidenceBox.jsx` (신규)
- `TradesTable.jsx`, `DiagnosisPanel.jsx`

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **ZERO** — 신규 컴포넌트, 기존 데이터 구조 변경 없음 |
| **소요시간** | **1일** |
| **롤백** | import 제거, JSX 복원 |
| **확인** | evidence 있는 거래내역 클릭하여 키-값 리스트 표시 확인 |

---
### C-6. Pagination / Infinite Scroll

#### 목적
거래내역 500행 이상에서 DOM 노드 폭증 방지

#### 방법
```jsx
// Pagination.jsx (신규)
export default function Pagination({ page, totalPages, onChange }) {
  return (
    <div className="flex items-center justify-center gap-2 mt-4">
      <button onClick={() => onChange(page - 1)} disabled={page <= 1}
        className="px-3 py-1 text-xs rounded bg-ngsat-border/50 text-ngsat-muted">←</button>
      <span className="text-sm text-ngsat-muted">{page} / {totalPages}</span>
      <button onClick={() => onChange(page + 1)} disabled={page >= totalPages}
        className="px-3 py-1 text-xs rounded bg-ngsat-border/50 text-ngsat-muted">→</button>
    </div>
  )
}
```

#### 대상 파일
- `dashboard/frontend/src/components/Pagination.jsx` (신규)
- `TradesTable.jsx`, `api.js` (limit+offset 파라미터 추가)

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **LOW** — 프론트+백엔드 양쪽 수정 |
| **소요시간** | **1일** |
| **전제조건** | 백엔드 `/api/trades?limit=N&offset=N` 파라미터 처리 (현재 limit만 있음) |
| **롤백** | Pagination.jsx 제거, api.js limit/offset 원복 |
| **확인** | 거래 건수 많은 계정에서 페이지 전환 테스트 |

---
### C-4. Equity Curve 차트

#### 목적
`AccountCard`에 시간별 자산 변화 sparkline 차트

#### 방법
```bash
npm install recharts
```
```jsx
// EquityChart.jsx (신규)
import { LineChart, Line, ResponsiveContainer } from 'recharts'

export default function EquityChart({ data }) {
  if (!data || data.length < 2) return null
  return (
    <ResponsiveContainer width="100%" height={60}>
      <LineChart data={data}>
        <Line type="monotone" dataKey="value" stroke="#3b82f6" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  )
}
```

#### 대상 파일
- `dashboard/frontend/src/components/EquityChart.jsx` (신규)
- `AccountCard.jsx`
- 백엔드 `/api/account`에 `equity_history` 필드 추가 (선택)

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **LOW** — sparkline만 추가, 매매 로직 영향 없음 |
| **소요시간** | **2일** (recharts 학습 1일 + 구현 1일) |
| **전제조건** | 백엔드에서 자산 이력 데이터 제공 필요 (현재 없음 — `_daily_capital` 유사 데이터) |
| **롤백** | 컴포넌트 제거, npm 패키지 유지 |
| **확인** | 계좌 페이지에 차트 렌더링 확인 |

---
### C-2. 백테스트 슬리피지 모델

#### 목적
백테스트 체결가 = 종가 ± 0.1~0.3% (급락 시 더 큰 슬리피지)

#### 방법
```python
# backtest/engine.py — _execute_sell 수정
import random
slippage_pct = 0.001 if ExitUrgency.NORMAL else 0.003  # 긴급도에 따라
slippage = price * slippage_pct * random.choice([-1, 1])
exec_price = price + slippage
```

#### 대상 파일
- `backtest/engine.py`

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **LOW** — 백테스트만 영향, 실거래 무관 |
| **소요시간** | **1일** |
| **유의사항** | 랜덤 슬리피지는 재현성 문제 → `seed` 고정 필요 |
| **롤백** | engine.py 복원 |
| **확인** | 기존 백테스트 대비 수익률 0.5~2% 하락 확인 (정상) |

---
### C-8. 장애복구 시나리오

#### 목적
프로세스 재시작 시 포지션 동기화 + 미체결 주문 복구

#### 방법
```python
# main.py run_live() 시작 부분에 추가
async def sync_positions(orchestrator):
    """재시작 시 KIS 포지션과 DB 포지션 동기화"""
    broker_positions = await orchestrator._fetch_positions()
    db_positions = orchestrator._trade_repo.get_recent_trades(limit=10)
    # 차이 발견 시 로깅 및 복구
```

#### 대상 파일
- `main.py`, `live/orchestrator.py`

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **MEDIUM** — 포지션 동기화 로직 오류 시 잘못된 포지션 추정 |
| **소요시간** | **2~3일** |
| **완화방안** | 동기화는 Read-Only로만 수행, 자동 복구는 Phase D로 이관 |
| **롤백** | 추가 코드 제거 |
| **확인** | KIS 계좌 포지션과 시스템 상태 일치 확인 |

---
### C-1. KIS WebSocket 실시간 시세

#### 목적
REST polling(5분) → WebSocket 실시간 시세로 대체, Rate Limit 회피

#### 방법
- KIS WebSocket(endpoint: `wss://openapi.koreainvestment.com:21000`)
- 접속 토큰 발급 → 실시간 시세 구독 → `refresh_prices()` 대체
- 참고: KIS WebSocket은 모의투자/실전투자 각각 다른 endpoint

#### 대상 파일
- `data/adapters/kis/websocket.py` (신규)
- `data/real_data_provider.py`
- `core/config.py`

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **MEDIUM** — KIS WebSocket 문서 의존, 장애 시 폴백 필요 |
| **소요시간** | **3~5일** (KIS 문서 분석 1일, 구현 2일, 테스트 2일) |
| **완화방안** | WebSocket 장애 시 REST polling 자동 폴백 |
| **롤백** | websocket.py 제거, `USE_WEBSOCKET=False` 환경변수 |
| **확인** | WebSocket 연결 후 실시간 가격 갱신 확인 |
| **참고** | KIS WebSocket API는 `mod_trd_cd`별 데이터 포맷이 다름. `H0UNCN0`(호가)와 `H0UCNT0`(체결) 두 가지 구독 필요 |

---
### C-3. ConfigService DB 구축

#### 목적
`.env` 파일 변조 → DB 기반 설정 저장소 + 인메모리 캐시

#### 방법
```python
# core/config_service.py (신규)
class ConfigService:
    def __init__(self, db_session):
        self._session = db_session
        self._cache: dict[str, Any] = {}

    def get(self, key: str, default=None):
        if key in self._cache:
            return self._cache[key]
        record = self._session.query(ConfigRecord)\
            .filter(ConfigRecord.key == key).first()
        value = record.value if record else default
        self._cache[key] = value
        return value

    def set(self, key: str, value: Any, persist: bool = True):
        self._cache[key] = value
        if persist:
            # DB upsert
            ...
```

#### 대상 파일
- `core/config_service.py` (신규)
- `data/models.py` (`ConfigRecord` 테이블 추가)
- `dashboard/backend/api.py`
- `main.py`

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **MEDIUM** — DB 마이그레이션 필요, 기존 .env 설정과의 호환성 |
| **소요시간** | **2~3일** |
| **완화방안** | .env → DB 단계적 전환: Phase 1=읽기만 DB, Phase 2=쓰기도 DB |
| **롤백** | ConfigService 제거, `.env` 직접 읽기로 원복 |
| **확인** | 설정 변경→재시작→값 유지 확인 |

---
### B-3. ML 피처 확장 (수급 + 재무) — Phase C 이관

#### 목적
기술적 지표(RSI, 볼린저, MA)에 외국인/기관 순매수 + 재무 데이터 추가

#### 방법
```python
# ml/features/builder.py — 추가 피처
features["foreign_net_buy_5d"] = foreign_net_buy_5d   # 외국인 순매수
features["foreign_holding_pct"] = foreign_holding_pct   # 외국인 보유율
features["per"] = per_value                            # PER
features["pbr"] = pbr_value                            # PBR
features["revenue_growth_q"] = revenue_growth_q        # 매출성장률
```

#### 리스크 검토

| 항목 | 평가 |
|------|------|
| **리스크** | **HIGH** — 모델 재학습 필요, 피처 차원 변경으로 기존 모델 호환성 깨짐 |
| **소요시간** | **3~5일** (KIS 외국인 API 1일, 재무 데이터 1일, 피처 파이프라인 1일, 재학습+검증 1~3일) |
| **완화방안** | 새 피처 Optional(`fillna(0)`) 처리로 기존 모델 호환 유지 |
| **Phase** | 최종 Phase로 배치 |

---

## 통합 실행 순서 권장

```
Phase C 실행 순서 (리스크 오름차순)
═══════════════════════════════════

Day 1-2: C-9 + C-7 + C-5 (LOW, 병행 가능)
  GitHub Actions + Skeleton + Evidence 시각화

Day 3-4: C-6 (LOW)
  Pagination / Infinite Scroll

Day 5-6: C-4 (LOW)
  Equity Curve 차트 (recharts)

Day 7:   C-2 (LOW)
  백테스트 슬리피지 모델

Day 8-10: C-8 (MEDIUM)
  장애복구 시나리오

Day 11-15: C-1 (MEDIUM)
  KIS WebSocket 실시간 시세

Day 16-18: C-3 (MEDIUM)
  ConfigService DB 구축

Day 19-23: B-3 (HIGH, 기존 Phase C 이관)
  ML 피처 확장 (수급+재무)
```

---

## 통합 리스크 매트릭스

| 순서 | 항목 | 리스크 | 시간 | 기술부채 | UX영향 | 운영영향 | 병행가능 |
|------|------|--------|------|---------|--------|---------|---------|
| **1** | C-9 GitHub Actions | LOW | 1일 | ✅ 해소 | — | 🟢 | ✅ |
| **2** | C-7 Skeleton | ZERO | 1일 | — | 🟢🟢 | — | ✅ |
| **3** | C-5 Evidence | ZERO | 1일 | ✅ 해소 | 🟢 | — | ✅ |
| **4** | C-6 Pagination | LOW | 1일 | — | 🟢🟢 | — | — |
| **5** | C-4 Equity 차트 | LOW | 2일 | — | 🟢🟢🟢 | — | — |
| **6** | C-2 슬리피지 | LOW | 1일 | — | — | 🟢 | — |
| **7** | C-8 장애복구 | MEDIUM | 2~3일 | ✅ 해소 | — | 🟢🟢🟢 | — |
| **8** | C-1 KIS WebSocket | MEDIUM | 3~5일 | ✅ 해소 | — | 🟢🟢🟢🟢 | — |
| **9** | C-3 ConfigService | MEDIUM | 2~3일 | ✅ 해소 | — | 🟢🟢 | — |
| **10** | B-3 ML 피처 | HIGH | 3~5일 | — | — | 🟢🟢🟢 | — |

---

## 결론

**즉시 가능 (리스크 LOW, 3일):** C-9(GitHub Actions) + C-7(Skeleton) + C-5(Evidence 시각화) — 셋 다 병행 가능, 기존 코드 변경 최소화

**1주차 (6일):** 위 3개 + C-6(Pagination) + C-4(Equity 차트) + C-2(슬리피지)

**2~3주차:** C-8(장애복구) → C-1(KIS WebSocket) → C-3(ConfigService) → B-3(ML 피처)

**첫 3개(C-9+C-7+C-5)부터 진행하시겠습니까?**
