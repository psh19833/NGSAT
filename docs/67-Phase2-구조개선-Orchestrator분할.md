# Phase 2: 구조 개선 실행 계획서 (Orchestrator 분할 중심)

> **작성일**: 2026-07-08  
> **기준 HEAD**: ddb7a4e  
> **근거**: 4종 코드리뷰 + P-67 실행 완료  
> **현재 Phase 1-2**: Rate Limiter 중앙화 완료 (`KisRateLimiter`)

---

## 1. 개요

Phase 1에서 **데이터 무결성(Rate Limiter)** 을 중앙화했습니다.
Phase 2는 **코드 구조(SRP)** 를 개선합니다.

**핵심 목표:**
- `live/orchestrator.py 1108줄` 단일 책임 원칙 위반 해소
- 3개 역할(Risk, Entry, Exit) 분리 → 각각 독립 파일
- 장중 안정성 유지 (분할 중단 없는 전환)

---

## 2. 실행 계획

### Task A: Orchestrator → 3개 모듈 분할 (4~6시간, ★ 핵심)

현재 orchestrator.py 1108줄에 들어있는 역할 3개:

| 역할 | 현재 라인 | 분할 대상 | 설명 |
|:----:|:---------:|:---------:|:-----:|
| **Risk** | `risk.py` ***이미 분리됨*** | ✅ 유지 | 이미 421줄 별도 클래스 |
| **Entry** | 428~680 | `live/entry_planner.py` **신규** | 종목 선정부터 매수 체결까지 |
| **Exit** | 680~888 | `live/exit_manager.py` **신규** | 포지션 모니터링 + 청산 판단 |
| **Orchestrator** | 85~1108→247줄 | `live/orchestrator.py` **축소** | 전체 흐름 제어만 (컨트롤러 역할) |

#### 상세 분할 구조

```
┌─────────────────────────────────────────┐
│          orchestrator.py (~247줄)         │  ← 순서만 조정
│  run_cycle() = 순서대로 호출              │
│   1. 계좌 조회 → RiskManager.check        │
│   2. Regime 평가 → EntryPlanner.evaluate  │
│   3. 스크리닝 → EntryPlanner.screen       │
│   4. 진입 → EntryPlanner.execute_buy      │
│   5. 청산 → ExitManager.check_exit        │
│   6. Stop Loss → RiskManager.check        │
│  force_sell(), close() → 각 모듈 위임     │
└─────────────────────────────────────────┘
         │                   │
         ▼                   ▼
┌────────────────────┐ ┌────────────────────┐
│  entry_planner.py   │ │   exit_manager.py   │
│  ~280줄             │ │   ~200줌            │
│                     │ │                     │
│ - evaluate_regime() │ │ - check_stops()     │
│ - select_mode()     │ │ - score_exit()      │
│ - screen_stocks()   │ │ - partial_tp()      │
│ - refine_entry()    │ │ - force_exit()      │
│ - execute_buy()     │ │ - trailing_stop()   │
│ - position_sizer()  │ │                     │
└────────────────────┘ └────────────────────┘
```

### Task B: Controller ↔ Orchestrator 역할 정리 (~1시간)

현재 `controller.py`는 start/stop만, orchestrator가 run_cycle 전부 수행.
→ **변경:** Controller가 주기(주사위)를 관리하고, Orchestrator는 단일 사이클만 실행
→ 테스트 용이성 증가 (단일 사이클 단위 검증 가능)

### Task C: 공통 타입/데이터클래스 통합 (~1시간)

현재 `CycleResult`(orchestrator.py), `RiskCheckResult`(risk.py), `ExecutionResult`(executor.py) 등
Result 타입이 3군데 분산.

→ **변경:** `live/models.py` 신규 생성, 사이클 관련 데이터클래스 통합
- `CycleContext`: 사이클 진입 시 공유 컨텍스트 (계좌, 레짐, 모드, 유니버스)
- `EntryDecision`, `ExitDecision`

---

## 3. 파일 변경 요약

| 파일 | 상태 | 변경 내용 | 예상 라인수 |
|:----|:----:|:---------|:----------:|
| `live/entry_planner.py` | **신규** | Entry 역할 (스크리닝·진입·포지션사이징) | ~280 |
| `live/exit_manager.py` | **신규** | Exit 역할 (청산·트레일링스탑·부분익절) | ~200 |
| `live/models.py` | **신규** | 공통 데이터클래스 통합 | ~60 |
| `live/orchestrator.py` | **수정** | 1108→~247줄 (흐름 제어만) | -861 |
| `live/controller.py` | **수정** | start/stop에서 주기관리 책임 명확화 | ~20 |
| `live/risk.py` | **수정 없음** | 이미 분리 완료, Phase 1에서 개선 완료 | - |

---

## 4. 리스크 검토

### 🔴 리스크 A: 장중 수정의 거래 영향도

| 구분 | 내용 |
|:----:|:------|
| **위험** | 분할 중 orchestrator의 기존 변수 참조 누락 → `AttributeError` 또는 `NoneType` 에러 → HOLD 모드 전환 |
| **영향** | **P0 — 거래 중단 가능** |
| **확률** | 중간 (의존성 많음) |
| **대책** | **① 장마감 후 배포 필수** ② 배포 전 `pytest test_orchestrator` 전면 통과 ③ 배포 후 10분간 모니터링 (cycle_count 증가 확인) |

### 🟡 리스크 B: CycleContext 데이터 누락

| 구분 | 내용 |
|:----:|:------|
| **위험** | 기존 `self._xxx` 변수 30+개를 새 Context 객체로 이전 중 누락 |
| **영향** | **P1 — 부분 기능 장애** (포지션 사이징 0, 진입 불가 등) |
| **확률** | 낮음 (컴파일 타임 에러로 발견 가능) |
| **대책** | Python `mypy --strict` 사전 검증 + 단위 테스트 강화 |

### 🟢 리스크 C: PR 규모 과다

| 구분 | 내용 |
|:----:|:------|
| **위험** | 1회 PR에 +540/-861줄 → 코드리뷰 부담, 버그 은닉 |
| **영향** | P2 — 리뷰 품질 저하 |
| **확률** | 중간 |
| **대책** | **3단계 PR 분할**: Task A(분할) → Task B(Controller) → Task C(모델 통합)로 3회 PR |

---

## 5. 실행 순서 (추천)

| 순서 | 작업 | 예상 시간 | 리스크 |
|:---:|:-----|:---------:|:------:|
| **1** | `live/models.py` — CycleContext + 공통 타입 신규 생성 | 30분 | 🟢 |
| **2** | `live/entry_planner.py` — 스크리닝·진입·포지션사이징 추출 | 2시간 | 🟡 |
| **3** | `live/exit_manager.py` — 청산·트레일링스탑 추출 | 1시간 | 🟡 |
| **4** | `live/orchestrator.py` — run_cycle 흐름만 남기고 위임 | 1시간 | 🔴 |
| **5** | `live/controller.py` — 주기관리 책임 명확화 | 1시간 | 🟢 |
| **6** | 전체 테스트 + mypy + 배포 | 1시간 | 🟢 |

**총 예상 시간: 6~7시간** (장마감 후 1일 작업)

---

## 6. 검증 기준

| 항목 | 기준 |
|:-----|:------|
| 기존 테스트 | `test_orchestrator.py` 13/13 ✅ |
| 신규 테스트 | EntryPlanner 최소 5개 + ExitManager 최소 3개 |
| 마이그레이션 | 기존 `self._xxx` 변수 30+개 전부 이전 확인 (스크립트 검증) |
| 모니터링 | 배포 후 20사이클 정상 실행 (cycle_count 증가 + 레짐/모드 정상) |

---

## 7. 결정 사항

1. **Phase 2를 진행하시겠습니까?** (추천: 예 — Rate Limiter 완료 직후가 구조 개선 최적기)
2. **3단계 PR 분할 vs 1회 PR?** (추천: 3단계 분할 — 리스크 최소화)
3. **장마감 후 바로 진행 vs 다음주?** (추천: 오늘 장마감 후 바로 — 컨텍스트 유지)
