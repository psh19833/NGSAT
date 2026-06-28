# NGSAT reviewdog 자동 리뷰 실행계획서

> 기준: psh19833/NGSAT main (`da41867`, 2026-06-28)
> 도구: reviewdog v0.21.0 + flake8 + pylint
> 범위: live/ backtest/ strategy/ data/ ml/ dashboard/backend/ core/

---

## 요약

flake8 250+ 경고, pylint 점수 4.5~7.8/10. 대부분이 **whitespace 정리(W293)** 와 **미사용 import(F401)** 로, 기능적 결함보다 코드 품질 기준 정립이 필요한 상태입니다. 기능적 버그는 `main.py`의 `uvicorn` 변수 스코프(E0606) 1건이 유일합니다.

---

## Phase R1 — 즉시 정리 (5분 이하, 리스크 없음)

### R1-1. 미사용 import 일괄 제거 (15건)

| 파일 | 제거할 import | 건수 |
|------|--------------|------|
| `live/orchestrator.py` | `BrokerError`, `AccountSummary`, `TradingState`, `RiskCheckResult`, `ExitUrgency`, `CycleResult` 등 | 6 |
| `live/executor.py` | `OrderError`, `RiskLimitHit`, `DecisionReason`, `Position` | 4 |
| `backtest/data_loader.py` | `typing.Sequence` | 1 |
| `data/real_data_provider.py` | `typing.Optional` | 1 |
| `strategy/mode_selector.py` | `StrategyConfig` | 1 |
| `live/controller.py` | `StrategyConfig` (추정) | 1+ |

**방법**: `autoflake --in-place --remove-all-unused-imports <files>` 또는 수동 제거
**시간**: 3분 (autoflake) / 10분 (수동)
**리스크**: LOW — 제거 후 `ModuleNotFoundError`만 확인하면 안전

---

### R1-2. 트레일링 whitespace 일괄 정리 (200+건, W293)

**영향 파일**: `live/controller.py`(14건), `live/executor.py`(35건), `live/orchestrator.py`(25건), `backtest/data_loader.py`(40건+), `backtest/engine.py`, `data/real_data_provider.py`, `strategy/regime.py`, `ml/training/trainer.py` 등 전반

**방법**:
```bash
find . -name '*.py' -exec sed -i 's/[ \t]*$//' {} \;
```

**시간**: 1분 (스크립트)
**리스크**: LOW — diff만 확인하면 안전

---

### R1-3. E501 라인 길이 정리 (30건)

| 파일 | 라인 | 길이 | 내용 |
|------|------|------|------|
| `live/executor.py:200` | 88자 | f-string + 조건부 체이닝 |
| `backtest/data_loader.py:103` | 80자 | 타입힌트 복합 |
| `backtest/data_loader.py:138` | 89자 | OpenAI 임베딩 호출 |
| 다수 `live/orchestrator.py` | 80~95자 | 로그 메시지, 타입힌트 |

**방법**: 긴 줄을 `\` 백슬래시 또는 괄호로 분할. 타입힌트는 `TYPE_CHECKING` 블록 분리
**시간**: 10분
**리스크**: LOW — 가독성만 개선

---

### R1-4. E302 클래스/함수 앞 2줄 공백 정리

**영향**: `live/controller.py`, `live/executor.py` 등
**방법**: 누락된 빈 줄 추가

**시간**: 5분
**리스크**: LOW

---

## Phase R2 — 기능적 결함 수정 (1시간)

### R2-1. `main.py` uvicorn 변수 스코프 (E0606, 10분)

**현재**:
```python
if not args.no_dashboard:
    import uvicorn                    # ← if 블록 안
    dashboard_app = create_app(...)
    ...
if dashboard_app:
    config_uvicorn = uvicorn.Config(  # ← 여기서 'uvicorn' undefined 가능
        app=dashboard_app, ...
    )
    api_server = uvicorn.Server(config_uvicorn)
```

**수정**:
```python
import uvicorn  # ← 파일 최상단으로 이동

if not args.no_dashboard:
    dashboard_app = create_app(...)
    ...
if dashboard_app:
    config_uvicorn = uvicorn.Config(app=dashboard_app, ...)
```

**리스크**: LOW — `--no-dashboard` 플래그 없이 실행 시 항상 import되므로 기능 변화 없음

---

### R2-2. `live/orchestrator.py` — 불필요한 import 정리 + pylint 점수 7.2→8.5 (15분)

주요 개선 항목:
- 미사용 6개 import 제거 (R1-1)
- `except (NotImplementedError, Exception)` → `except Exception` (Exception이 상위 클래스)
- `Cycleresult`, `ExitPrediction` 냉동 import 확인 및 정리
- 긴 타입힌트 라인 분할 (E501)

---

### R2-3. `backtest/engine.py` — pylint 점수 5.9→7.0 (30분)

| 항목 | 내용 | 시간 |
|------|------|------|
| 미사용 변수 제거 | `daily_loss` 필드 중복, 불필요 지역변수 | 5분 |
| `try` 범위 최소화 | 과도한 try 블록을 특정 호출만 감싸도록 | 10분 |
| `_build_result()` 분할 | 순환 복잡도 12→6으로 분할 (sell 처리 분리) | 15분 |

---

### R2-4. `strategy/mode_selector.py` — config import 정리 (5분)

`StrategyConfig` import가 사용되지 않고 import만 되어 있음.

```python
# AS-IS
from core.config import StrategyConfig  # ← 사용 안 함

# TO-BE
# 제거
```

---

## Phase R3 — 설정 및 CI (여유 시)

| 항목 | 내용 | 예상 시간 |
|------|------|----------|
| **R3-1. lint 설정 파일 표준화** | `pyproject.toml`에 `[tool.pylint]`, `[tool.flake8]` 설정 추가 | 15분 |
| **R3-2. pre-commit hook** | 커밋 시 자동 flake8 + trailing whitespace 제거 | 15분 |
| **R3-3. GitHub Actions 연동** | PR마다 reviewdog 실행 → 자동 PR 코멘트 | 30분 |
| **R3-4. flake8 복잡도 제한 강화** | `--max-complexity=10` → 현재 통과하는지 확인 | 5분 |
| **R3-5. pylint 플러그인** | `pylint-pytest`, `pylint-sqlalchemy` 등 도입 | 10분 |

---

## 실행 로드맵

```
Phase R1 (즉시, 20분)
├── R1-1. autoflake로 미사용 import 일괄 제거   [3분]
├── R1-2. sed로 trailing whitespace 전파일 정리  [1분]
├── R1-3. E501 라인 길이 30건 분할             [10분]
└── R1-4. E302 클래스 간격 15건 정리           [5분]

Phase R2 (기능보강, 1시간)
├── R2-1. main.py uvicorn import 파일 최상단으로  [10분]
├── R2-2. orchestrator.py import+예외 정리      [15분]
├── R2-3. engine.py pylint 5.9→7.0             [30분]
└── R2-4. mode_selector.py 불필요 import 제거   [5분]

Phase R3 (CI/품질 인프라, 여유 시)
├── R3-1. pyproject.toml lint 규칙 표준화       [15분]
├── R3-2. pre-commit hook                       [15분]
├── R3-3. GitHub Actions + reviewdog            [30분]
├── R3-4. 복잡도 게이트 확인                     [5분]
└── R3-5. pylint 플러그인 추가                  [10분]
```
