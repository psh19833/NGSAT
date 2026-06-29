# BE-15: trainer.py 분할 계획서 + 리스크검토

> 일자: 2026-06-29
> 기준 커밋: d7bf883
> 소요 예상: ~60분

---

## 1. 요약

`ml/training/trainer.py` (795줄)를 기능별 모듈로 분할. PriceRiseModel 클래스의 3개 책임을 분리:

1. **모델 저장/로드** → `ml/training/persistence.py` (save/load/hash)
2. **모델 선택/튜닝** → `ml/training/model_selection.py` (auto_tune, _single/multi model retrain)
3. **trainer.py** → 핵심: PriceRiseModel (train, predict, auto_retrain) + train_from_* 함수만 유지

---

## 2. 분할 상세

### 현재 구조 (795줄 1개 파일)

```
ml/training/trainer.py
├── TrainingResult (dataclass, 30줄)
├── PriceRiseModel (600줄)
│   ├── __init__ / properties (30줄)
│   ├── train() — 학습 로직 (200줄)
│   ├── predict_proba() — 예측 (30줄)
│   ├── save() — joblib 저장 (40줄)
│   ├── load() — joblib 로드 + 해시 검증 (50줄)
│   ├── auto_tune() — Optuna 하이퍼파라미터 (100줄)
│   ├── auto_retrain() — 자동 재학습 (80줄)
│   ├── _single_model_retrain() (30줄)
│   └── _multi_model_retrain() (40줄)
├── train_from_price_data() — 외부 진입점 (30줄)
└── train_from_minute_data() — 분봉 학습 (30줄)
```

### 분할 후 구조

```
ml/training/
├── __init__.py
├── trainer.py          ← PriceRiseModel.train/predict/__init__ + train_from_* (350줄)
├── persistence.py      ← save/load/_integrity_hash (80줄, 신규)
└── model_selection.py  ← auto_tune/_single_model_retrain/_multi_model_retrain (180줄, 신규)
```

### 2.1 `ml/training/persistence.py` (신규, ~80줄)

PriceRiseModel에서 save/load 관련 메서드 추출:

```python
def save_model(model: PriceRiseModel, path: str | Path) -> Path:
    """joblib.dump + sha256 사이드카 파일 생성."""

def load_model(path: str | Path) -> PriceRiseModel:
    """joblib.load + sha256 검증."""

def verify_integrity(model_path: Path) -> bool:
    """.pkl 파일 sha256 계산 → .sha256 비교."""
```

### 2.2 `ml/training/model_selection.py` (신규, ~180줄)

```python
def auto_tune(model: PriceRiseModel, X, y, n_trials=50, timeout=300):
    """Optuna 하이퍼파라미터 튜닝."""

def single_model_retrain(model: PriceRiseModel, X, y):
    """단일 모델 재학습 후 AUC 비교."""

def multi_model_retrain(model: PriceRiseModel, X, y):
    """5개 모델 전부 학습 후 최고 AUC 모델 선택."""
```

### 2.3 변경되는 호출부

| 호출자 | import 변경 |
|--------|-------------|
| `ml/inference.py` | `from ml.training.trainer import PriceRiseModel` — **변경 없음** |
| `main.py` | `from ml.training.trainer import PriceRiseModel, train_from_price_data` — **변경 없음** |
| `core/backtest_runner.py` | `from ml.training.trainer import train_from_price_data` — **변경 없음** |

---

## 3. 리스크검토

### 리스크 평가 기준

| 등급 | 기준 |
|------|------|
| ZERO | 거래 로직 영향 없음, import 경로만 변경 |
| LOW | 단일 파일, 롤백 용이 |
| MEDIUM | 다중 파일 변경, 테스트 필요 |

### 항목별 리스크

| 항목 | 리스크 | 상세 | 대응 |
|------|--------|------|------|
| 모듈 분할 | **LOW** | 기존 클래스명(`PriceRiseModel`)과 함수명(`train_from_price_data`) 유지 | 호출부 import 경로 불변 |
| save/load 추출 | **LOW** | 기존 인스턴스 메서드 → 모듈 함수로 변경 | PriceRiseModel.save/load를 wrapper로 유지 |
| model_selection 추출 | **LOW** | `self` → 명시적 파라미터 전달 | TypeError 방지를 위한 타입 체크 |
| pytest | **LOW** | 7개 테스트가 PriceRiseModel 직접 사용 | 테스트 import 경로 불변 |
| **전체 롤백** | **즉시** | `git checkout d7bf883 -- ml/training/` | 1분 |

### 종합 리스크: **LOW**

- 실거래 로직 변경 없음 (train.py는 학습 전용, inference는 별도 파일)
- import 경로 유지 → 호출부 수정 제로
- save/load wrapper 유지 → 외부 동일 인터페이스
- 실패 시 `git checkout` 단일 명령어로 전체 복원

---

## 4. 실행 순서

1. `persistence.py` 생성 + save/load/verify 함수 구현
2. `model_selection.py` 생성 + auto_tune/single/multi 함수 구현
3. `trainer.py`에서 save/load 메서드 → persistence.py 호출 wrapper로 변경
4. `trainer.py`에서 auto_tune/retrain → model_selection.py 호출로 변경
5. pytest 실행 (308 passed 확인)
6. batch commit + push

---

## 5. 실행 판단

**LOW 리스크 — 즉시 진행 가능.** import 경로 불변, 실거래 영향 없음, 1분 롤백.
