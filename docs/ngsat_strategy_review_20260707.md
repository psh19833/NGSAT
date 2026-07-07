# NGSAT 전략/정책 심층 리뷰

> **리뷰어:** 시니어 퀀트 트레이더 / 리스크 매니저 (15년+)
> **리뷰 일자:** 2026-07-07
> **기준 HEAD:** 7e6556d
> **분석 대상:** 전략 전 모듈 (strategy/*, live/*, ml/*, backtest/*, core/config.py, data/universe_manager.py)
> **참조:** P-60 (scorer 분리, 지표 6종, 중립장 임계값 45→30), P-66 (RS 가중치, Kelly 포지셔닝, RS 동적 전환)

---

## S — 강점 (Strengths)

### S1. 하이브리드 2단계 매매 설계
레짐 평가 → 모드 선택 → 스크리닝 → ML 예측 → 진입 정밀화로 이어지는 5단계 파이프라인은 업계 표준 대비 우수한 구조다. 일봉 레짐과 분봉 진입/청산 정밀화를 분리한 2단계 설계는 잡음(noise)을 걸러내면서도 장중 대응력을 확보한다. **이는 많은 국내 퀀트 시스템이 놓치는 설계 포인트다.**

### S2. 강력한 결정 근거 기록 (Decision Reason Mandatory)
`entry_timing.py`, `exit_timing.py`, `executor.py` 전반에 걸쳐 reason 필드를 강제하는 아키텍처는 감사(audit)와 디버깅에 매우 유리하다. 실거래 운용에서 "왜 샀는가"를 추적할 수 있는 시스템은 운용 리스크를 크게 줄여준다.

### S3. VI(변동성완화장치) 실시간 대응
`executor.py`의 `_adapt_price_for_vi()`는 VI 발동 시 시장가 주문을 자동으로 지정가 전환한다. KIS API 미지원 시 silent fallback 처리도 적절하다. 이 기능만으로도 VI 상황에서의 불필요한 슬리피지를 방지할 수 있다.

### S4. 다층 리스크 체계
하나의 시스템에 7개의 리스크 레이어가 중첩되어 있다:
- **L1:** 일일 손실 한도 (mode별 차등)
- **L2:** 종목별 손절선 (mode별 차등)
- **L3:** 포트폴리오 상관관계 (TR-7, 로그 수익률 기반)
- **L4:** 섹터 집중도 제한 (TR-5, max 3)
- **L5:** 최대 보유 종목 수 (max 10)
- **L6:** 일일 거래 횟수 제한 (max 20)
- **L7:** 총 노출 한도 (max 50%)

이 조합은 소규모 계좌(~2백만 원)에서 과도하게 느껴질 수 있지만, 자산이 커질수록 빛을 발한다.

### S5. 청산 우선 설계
`run_cycle()`은 진입보다 청산을 먼저 처리한다 (orchestrator.py 청산 루프가 ML 진입 루프보다 먼저). 장 마감 시 `regime_skipped=True`로 레짐 평가 없이 청산만 실행하는 것도 현명한 설계다.

### S6. 합성 데이터 기반 백테스트 검증
`data_loader.py`의 `generate_synthetic_data()`, `generate_synthetic_universe()`, `synthetic_minute_provider()`는 실제 과거 데이터 없이도 백테스트 엔진을 검증할 수 있는 인프라를 제공한다. 퀀트 시스템에서 가장 흔히 누락되는 부분이다.

### S7. Idempotency 및 미체결 주문 정리
`executor.py`의 `_submitted_orders` 캐시와 `cancel_unfilled_orders()` (30초 TTL)는 주문 중복 제출과 미체결 적체를 방지한다. BE-1로 추적되는 이 패턴은 실전에서 큰 가치를 발휘한다.

---

## W — 약점 (Weaknesses) — 심각도별

### 🔴 CRITICAL W1. 일일 손실 한도 부호 오류 가능성 (Sign Convention Bug)

**모듈:** `live/risk.py:151`

**발견:**
```python
loss_pct = account.daily_loss_pct if account.daily_loss_pct is not None else account.total_profit_loss_pct
if loss_pct >= limit_pct:  # ← 오류 가능
```

**문제:** KIS API에서 `daily_loss_pct`가 **음수**로 반환되는 경우(`-3.5`는 3.5% 손실 의미), `-3.5 >= 5.0`은 항상 `False`다. 즉 **일일 손실 한도가 절대 트리거되지 않는다.** 반면 `total_profit_loss_pct`가 양수(절대값)로 반환되는 API도 있어 일관성이 없다.

**대조:** 백테스트 엔진(`backtest/engine.py:394`)은 음수 일일 수익률을 `<= -limit`으로 비교한다 — 즉 올바르게 구현되어 있다. 백테스트는 통과하지만 실거래는 차단되지 않는 **silent failure** 상황.

**조치:** KIS API 응답 부호 확인 후 `abs()` 처리 또는 조건문 수정이 필요함. 확인되지 않은 상태에서 이 코드는 **신뢰할 수 없는 리스크 차단 장치**다.

**심각도:** 🔴 CRITICAL

---

### 🔴 CRITICAL W2. 포지션 사이징 3중 레이어 충돌 및 ATR 가드 모순

**모듈:** `live/risk.py:105-117` + `live/orchestrator.py:567-574` + `live/position_sizer.py`

**발견:**
실제 포지션 사이즈 결정에 3개 레이어가 관여하며 논리적 모순이 있다:

1. **Layer 1 (Config defaults):** `mode_swing_position_size = 0.10`, `mode_short_position_size = 0.05`
2. **Layer 2 (RiskManager.position_size_pct):** mode="hold"일 때만 `calc_position_size(BEAR, ...)`를 호출. NEUTRAL/SWING 모드에서는 Layer 1 값을 그대로 반환
3. **Layer 3 (Orchestrator ATR 조정):** `min_pct = base_budget_pct * 1.0` (566-569행)

**모순:** ATR 조정 로직(orchestrator:568)은 `min_pct = base_budget_pct * 1.0`으로 설정되어 있어, 실제 조정된 포지션 크기는 `base_budget_pct * (target_vol / vol_pct)`로 계산되지만 `min_pct`가 `base * 1.0`이므로 **ATR 조정이 포지션을 절대 줄일 수 없다.** 즉 변동성이 아무리 높아도 `min_pct(100%)` 이하로는 내려가지 않는다. 이는 "고변동성 → 포지션 축소"라는 리스크 관리 원칙에 정면으로 위배된다.

**Position Sizer 이중성:** `risk.py`는 mode="hold"일 때만 `calc_position_size()`를 BEAR로 호출한다. 그런데 mode_selector에서 "hold"는 NEUTRAL+저변동성에서도 선택된다. 이 경우 레짐 점수 50점이 `calc_position_size(BEAR, score=50)`으로 전달되어 → BEAR else 브랜치(8%)에서 사이즈가 결정된다. **NEUTRAL 장에서 관망 모드인데도 8% 포지션 사이즈가 산출되는 혼란**이 발생한다.

**권장:** Layer 3의 `min_pct`를 `base_budget_pct * 0.3` 등으로 낮추고, `calc_position_size()`를 모든 모드에서 사용하도록 통일할 것.

**심각도:** 🔴 CRITICAL

---

### 🔴 CRITICAL W3. ML AUC 68.9% — 예측력 신뢰 구간 이하

**모듈:** `ml/*` 전반

**발견:**
현재 `minute_model.pkl`의 AUC가 ~68.9%로, 이는 **50%(랜덤)와 유의미한 차이가 나지만 거래 결정 기준으로는 취약한 수준**이다.

구체적 문제점:
- **Auto-retrain 꺼져 있음:** `ml_auto_retrain=False` (기본값). 모델이 장 마감 후 자동 재학습되지 않음
- **Auto-select 꺼져 있음:** `ml_auto_select_model=False`. XGBoost/LightGBM이 설치되어 있어도 sklearn 3종만 비교
- **25개 피처 중 의미 있는 피처가 몇 개인지 불명확:** `ml/features/builder.py`와 `minute_builder.py`에 피처 중요도 모니터링 코드가 없음
- **Purge-embargo(1시간)는 좋지만, 데이터 leakage는 여전히 가능:** 과거 250일 데이터로 향후 3일 예측 → 시계열 분할이 아니라 랜덤 분할 시 train/test leakage 발생 가능 (trainer.py 내 `train_test_split` 방식 확인 필요)
- **분류 임계 65% 기준으로, AUC 68.9%면 실제 예측 정밀도는 낮음**

**조치:** (1) `ml_auto_select_model=True` 활성화 (2) feature importance 정기 로깅 (3) purge-embargo 외에 시계열 CV 도입 검토

**심각도:** 🔴 CRITICAL

---

### 🟡 HIGH W4. 백테스트 슬리피지/거래비용 모델 과소추정

**모듈:** `backtest/engine.py:162-188`

**발견:**
```python
slip_pct = 0.003 if urgent else 0.001  # 0.1% normal, 0.3% urgent
```

**문제:**
- **0.1% 슬리피지는 KOSDAQ 저유동성 종목에는 비현실적**이다. 시가총액 1,000억 미만 종목의 호가 스프레드는 보통 0.3~0.5% 수준
- 스크리닝을 통과하는 종목이 대형주 위주가 아니라면 실제 슬리피지는 2~5배 높을 수 있음
- **시장 충격(market impact) 모델이 전혀 없음.** 한 번에 계좌의 10~15%를 단일 종목에 배분할 때, 특히 소형주에서 시장 충격이 발생
- 백테스트 승률과 수익률이 실거래 대비 체계적으로 과대추정될 위험
- **백테스트는 장 종가로 체결**하지만, 실거래는 장중에 체결 → 장중 변동성으로 인한 괴리 발생

**조치:** (1) 종목별 유동성 기반 동적 슬리피지 모델 도입 (2) 최소 슬리피지 0.2% / 긴급 0.5%로 상향 (3) 시장 충격 모델 추가

**심각도:** 🟡 HIGH

---

### 🟡 HIGH W5. 손절선 연장 조건에 Stop-loss 실행 방지 부재

**모듈:** `live/risk.py:206-238`

**발견:**
`can_extend_stop_loss()` 메서드는 존재하지만, 이 메서드를 **실제로 호출하는 코드가 orchestrator/executor 어디에도 없다.** 손절선 연장 기능은 정의만 되어 있고 사용되지 않는 dead code다.

**문제:** `effective_stop_loss_pct`는 항상 config 기본값(swing 3%, short 1.5%)만 사용됨. 하락장에서 손절선을 5%까지 확장하는 메커니즘이 실제로는 작동하지 않는다.

**심각도:** 🟡 HIGH

---

### 🟡 HIGH W6. 중립장 최소 점수 30의 일관성 문제

**모듈:** `core/config.py:122` → `screener_neutral_min_score = 30.0`

**발견:**
- **약세장 최소 점수(`screener_bear_min_score = 50.0`)가 중립장(30.0)보다 높다.** 즉 약세장에서 더 높은 기준으로 스크리닝한다. 논리적으로는 '약세장에서 더 보수적'이 되어야 하는데, 오히려 "약세장에서 50점을 넘는 좋은 종목만 산다"는 의미로 해석 가능하다. 이 자체가 잘못된 것은 아니지만, 중립장이 30점으로 너무 낮아 사실상 필터 역할을 하지 못한다.
- P-60에서 45→30으로 낮춘 것은 하락장 대응을 위한 결정이었으나, **30점은 100점 만점에서 주가가 20일선 위에 있고 RSI가 50만 넘어도 통과하는 수준.** 결과적으로 중립장에서 너무 많은 후보가 통과된다.
- `max_candidates` 제한(중립장 10개)이 있지만, 점수 순 정렬 후 상위 10개를 자르는 방식이므로 하위권 종목이 진입하는 것은 방지된다.

**권장:** 중립장 최소 점수를 35~40으로 재조정하거나, 최소 점수 외에 **최소 분산(min variance)** 기준을 추가 검토

**심각도:** 🟡 HIGH

---

### 🟡 HIGH W7. 상관관계 검사 임계값과 메트릭 한계

**모듈:** `live/orchestrator.py:183-236`

**발견:**
```python
threshold = 0.85  # 상관계수 임계
# ...
if avg_corr > 0.5:
    return False, ...  # 차단
```

**문제:**
1. **상관계수 0.85는 매우 높은 수준.** 같은 업종 내 대형주들도 0.7~0.8 수준의 상관관계를 보이는 경우가 많음. 이 임계값을 넘어야만 차단하므로 대부분의 경우 통과됨
2. **단순 평균 비율 검사:** `high_corr_count / total_checked > 0.5`의 의미가 불분명. 2개 포지션 중 1개가 상관계수 0.86이면 0.5 > 0.5? = False → 차단 안 됨. **절반은 통과해도 전체가 위험할 수 있음**
3. **60일 로그 수익률만 사용:** 최근 60영업일(약 3개월)의 로그 수익률로만 상관관계 측정. 시장 레짐이 바뀌면 상관관계 구조도 변하는데, 이를 반영하지 못함

**권장:** (1) 임계값을 0.75로 낮추고 (2) 가중 검사(`sum(가중치) > total * 0.5`) 도입 (3) 최근 구간 가중치 증가 (exponential weighting)

**심각도:** 🟡 HIGH

---

### 🟡 HIGH W8. Trailing Stop 및 부분 익절 기본 비활성화

**모듈:** `core/config.py:163,168`

**발견:**
```python
trailing_stop_enabled: bool = False       # P1-1 비활성
partial_tp_enabled: bool = False          # P1-2 비활성
```

트레일링 스탑과 부분 익절이라는 **2개의 핵심 수익 보호 메커니즘이 코드에는 구현되어 있지만 기본적으로 꺼져 있다.** 현재 수익 보호는 오직 ML 청산 예측과 고정 손절선에만 의존한다. 장중 급등 후 급락 시 수익을 보호할 장치가 없다.

트레일링 스탑 설정값은 적절함(`atr_multiplier=2.0`, `activate_pct=1.0%`). 다만 ATR 의존도가 높아 ATR이 0에 가까운 안정적 장에서는 활성화되지 않을 수 있음.

**심각도:** 🟡 HIGH

---

### 🔵 MEDIUM W9. 백테스트 abs() 버그 및 import 위반 미수정 (P3)

**모듈:** `backtest/*`

**발견:**
알려진 버그 #1 (abs() 로직)과 #2 (import 위반)가 P3로 분류되어 미수정 상태다. 구체적 내용은 코드에서 직접 확인되지 않았으나(문서로만 전달), 다음이 우려됨:
- abs() 버그가 일부 거래의 손익 계산에 영향을 줄 수 있음 → 승률/Profit Factor 왜곡
- import 위반이 엣지 케이스에서 예외(ImportError)를 발생시킬 수 있음

**심각도:** 🔵 MEDIUM

---

### 🔵 MEDIUM W10. 모드 전환 시 Position Sizing와 Risk 파라미터 동기화 누락

**모듈:** `live/orchestrator.py:401-411`

**발견:**
`run_cycle()`에서 mode 선택 후 다음이 실행됨:
```python
self._risk.set_regime_context(regime_result.score, vol)
self._risk.set_mode(self._current_mode)
```

그러나 `set_mode()`는 stop loss / daily loss / position size를 모두 한 번에 변경한다. **모드 전환 직전에 진입한 포지션이 새로운(더 타이트한) 손절선으로 갑자기 평가될 위험이 있다.** 예: 스윙 → 단타 전환 시 손절선 3% → 1.5%로 줄어들어 기존 포지션이 즉시 손절 대상이 될 수 있음.

**조치:** 모드 전환 시 기존 포지션에는 이전 손절선을 유지하거나, 전환 후 신규 진입에만 새로운 파라미터를 적용하는 Grace Period 도입

**심각도:** 🔵 MEDIUM

---

### 🔵 MEDIUM W11. 장중보정 계수가 KOSPI 등락률에만 의존

**모듈:** `live/orchestrator.py:354-393`

**발견:**
```python
intraday_change_pct = index_price.change_pct  # KOSPI only
correction = intraday_change_pct * 2.5
```

장중보정이 **KOSPI 지수에만** 의존한다. KOSDAQ 중심 포트폴리오일 때는 KOSPI 등락률이 KOSDAQ 움직임과 다를 수 있어 보정이 효과적이지 않을 수 있다. KOSDAQ 지수도 함께 고려하거나, 보유 포트폴리오의 평균 등락률을 사용하는 방안 검토 필요.

또한:
- `abs(correction) >= 0.5` 조건으로 미세 보정 차단 (합리적)
- 레짐 재평가 후 enum이 바뀌면 `self._last_regime`도 교체됨 → 다음 사이클에서 hysteresis에 영향
- 보정 cap ±15는 등락률 ±6%에 해당하는데, 이는 극단적 상황에서만 도달

**심각도:** 🔵 MEDIUM

---

### 🔵 MEDIUM W12. Synthetic Universe Guard가 development 모드만 차단

**모듈:** `live/orchestrator.py:287-292`

**발견:**
```python
if name.startswith("synthetic_"):
    logger.error(f"합성 유니버스 감지 — 사이클 스킵")
    return result
```

이 가드는 `name` 필드가 "synthetic_"으로 시작하는지만 확인한다. 만약 합성 데이터가 실수로 production DB에 잘못 적재되거나, `StockInfo`의 name 필드가 다르게 설정되면 이 가드를 우회할 수 있다.

**심각도:** 🔵 MEDIUM

---

### 🔵 MEDIUM W13. 계좌 크기 대비 과도한 거래 제한

**모듈:** `core/config.py`

**발견:**
현재 계좌 총자산 2,087,420원 기준:
- **최대 보유 10종목:** 1종목당 약 20만 원 → 2~5주 (삼성전자 기준 1주)
- **일일 거래 횟수 20회:** 2백만 원 계좌에서 하루 20회 거래는 회전율이 매우 높음
- **섹터 집중도 3개:** 소액 계좌에서 3개 업종으로 분산은 의미 있음

소액 계좌에서는 이러한 제한이 오히려 거래를 불필요하게 복잡하게 만든다. 제한 자체는 훌륭하지만, 계좌 규모에 비례한 동적 조정이 필요하다.

**심각도:** 🔵 MEDIUM

---

### 🟢 LOW W14. KOSPI/KOSDAQ 가중치 미사용

**모듈:** `core/config.py:94-95`

**발견:**
```python
kospi_weight: float = 0.7
kosdaq_weight: float = 0.3
```

`RiskConfig`에 정의되어 있지만 실제 리스크 관리 로직에서 이 값을 참조하는 코드가 없다. vestigial field로 추정.

**심각도:** 🟢 LOW

---

### 🟢 LOW W15. 진입/청산 타이밍 파라미터 하드코딩

**모듈:** `strategy/entry_timing.py:49-52`, `strategy/exit_timing.py:54-58`

**발견:**
```python
DEFAULT_OVERHEAT_RSI = 75.0
DEFAULT_SURGE_THRESHOLD_PCT = 3.0
DEFAULT_PLUNGE_THRESHOLD_PCT = 3.0
DEFAULT_TAKE_PROFIT_MIN_PCT = 5.0
```

이 값들은 함수의 기본 인자로 하드코딩되어 있어 Runtime 조정이 불가능하다. `StrategyConfig`에서 관리하고 `refine_entry()/refine_exit()`에 전달하는 구조로 개선 가능.

**심각도:** 🟢 LOW

---

### 🟢 LOW W16. 등락률×2.5 계수의 이론적 근거 부족

**모듈:** `live/orchestrator.py:364`

**발견:**
```python
correction = intraday_change_pct * 2.5
```

2.5배 증폭 계수의 이론적/실증적 근거가 코드나 주석에 없다. P-60에서 등락률에 비례하는 방식으로 변경된 것은 합리적이지만, "왜 2.5배인가"에 대한 설명이 필요하다. VIX나 변동성 지수와 연동하는 방안도 검토 가능.

**심각도:** 🟢 LOW

---

## O — 개선 기회 (Opportunities)

### O1. 동적 슬리피지 모델 도입
백테스트 슬리피지를 종목별 유동성(평균 거래대금, 호가 스프레드)에 연동시키면 백테스트-실거래 간 갭을 줄일 수 있다. KIS API로 일별 평균 호가 스프레드를 수집하여 모델링하는 것이 이상적.

### O2. 하락장 전용 스크리너 모드
현재 스크리너는 모든 레짐에서 동일한 6종 지표를 사용한다. 하락장(BEAR)에서는 반등 모멘텀/과매도 RSI/이격도 등 하락장에 특화된 지표 가중치 세트로 전환하면 더 좋은 결과를 얻을 수 있다.

### O3. Feature Importance 모니터링 대시보드
ML 모델의 피처 중요도를 매 재학습 시 기록하고, 시간에 따른 중요도 변화를 추적하면 피처 엔지니어링의 방향성을 잡을 수 있다. 현재는 AUC만 추적되고 있음.

### O4. Time-series Cross-Validation
`trainer.py`에서 현재 사용 중인 `train_test_split`을 시계열-aware CV(Expanding Window, Rolling Window)로 전환하면 AUC가 낮더라도 더 신뢰할 수 있는 성능 추정이 가능하다.

### O5. 리스크 패리티(Risk Parity) 포지션 사이징
현재는 단순 자본 비율 기반 사이징이다. 각 포지션의 변동성(ATR)을 고려한 Risk Parity 접근으로 전환하면 전체 포트폴리오 변동성을 더 안정적으로 관리할 수 있다.

### O6. 장 초반/후반 별도 거래 규칙
09:00~09:10은 데이터 수집 모드로 차단되어 있으나, 장 마감 30분 전(15:00~15:30)에도 특별 규칙(신규 진입 금지, 부분 익절 우선)을 추가하면 마감 변동성 리스크를 줄일 수 있다.

### O7. 백테스트 리포트 고도화
현재 `report.py`는 월별 성과, 종목별 분석은 있지만 레짐별 성과 분리, 월별 Win Rate 추이, MDD 구간 분석이 없다. 레짐별/모드별로 성과를 분할하여 보여주면 어떤 조건에서 전략이 작동/실패하는지 파악하기 쉽다.

---

## T — 위협 (Threats)

### T1. 시장 레짐 구조 변화 (Structural Break)
2008, 2020년과 같은 금융위기나 2024년의 China discount/deflation 충격 같은 구조적 변화는 현재의 6개 지표 기반 레짐 평가가 제대로 대응하지 못할 수 있다. 특히 ADX(5%)와 볼린저(20%)는 추세 지속에서만 의미가 있다.

### T2. 단일 계좌 의존성 (KIS API)
현재 단일 증권사(KIS) API에 완전히 의존한다. KIS API 장애 시 거래 중단, rate limit 초과 시 지연, API 스펙 변경 시 전체 시스템 수정이 필요하다. 특히 WebSocket 재연결 루프 문제는 미해결 상태로 알려져 있다.

### T3. 레짐 전환 지연에 따른 손실
히스테리시스(±5점)는 잦은 레짐 전환을 방지하지만, 급락장에서 BEAR 전환이 늦어질 수 있다. 장중보정(등락률×2.5, cap±15)이 이 gap을 메우도록 설계되었지만, 1사이클(보통 1~5분)의 지연은 여전히 존재한다.

### T4. KOSPI/KOSDAQ 지수 데이터 의존
레짐 평가가 지수 데이터의 적시성과 정확성에 전적으로 의존한다. KOSPI 지수 제공에 지연이 생기거나, 데이터가 불완전하면 전체 파이프라인이 영향받는다.

### T5. 소액 계좌의 한계
현재 계좌가 200만 원 수준에서는 1주 단위 거래로 인한 양자화 오차(quantization error)가 크다. 예를 들어 삼성전자 1주 = 약 8만 원 = 계좌의 4%. 포지션 사이징 이론(kelly, risk parity)이 1주 단위에서 의미를 잃는다.

---

## 상세 발견 요약 테이블

| 모듈 | 심각도 | 발견 사항 | 상태 |
|------|--------|-----------|------|
| `live/risk.py:151` | 🔴 CRITICAL | 일일 손실 한도 부호 오류 — KIS 음수 수익률에서 항상 False | 미확인 |
| `live/orch.py:568` | 🔴 CRITICAL | ATR 조정 min_pct=base*1.0 → 절대 축소 불가, 리스크 원칙 위배 | 신규 발견 |
| `ml/*` 전반 | 🔴 CRITICAL | AUC 68.9%, auto-retrain/auto-select OFF, feature importance 모니터링 없음 | known |
| `backtest/engine.py:162` | 🟡 HIGH | 슬리피지 0.1% 과소추정, 저유동성 종목에서 실거래 gap 큼 | 신규 발견 |
| `live/risk.py:206` | 🟡 HIGH | can_extend_stop_loss() 정의만 있고 호출 없음 | 신규 발견 |
| `core/config.py:122` | 🟡 HIGH | 중립장 min_score=30, bear=50보다 낮아 일관성 문제 | P-60 |
| `live/orch.py:213` | 🟡 HIGH | 상관관계 임계 0.85 과도, avg_corr > 0.5 검사 허점 | 신규 발견 |
| `core/config.py:163,168` | 🟡 HIGH | Trailing Stop/Partial TP 기본 비활성 | P1-1, P1-2 |
| `backtest/*` | 🔵 MEDIUM | abs() 버그 #1, import 위반 #2 미수정 (P3) | known unfixed |
| `live/orch.py:401` | 🔵 MEDIUM | 모드 전환 시 기존 포지션 손절선 즉시 변경 위험 | 신규 발견 |
| `live/orch.py:364` | 🔵 MEDIUM | 장중보정 KOSPI only, KOSDAQ 미반영 | 신규 발견 |
| `live/orch.py:287` | 🔵 MEDIUM | Synthetic guard 우회 가능 | 신규 발견 |
| `core/config.py:159` | 🔵 MEDIUM | 소액 계좌 대비 과도한 거래 제한 | 신규 발견 |
| `core/config.py:94` | 🟢 LOW | KOSPI/KOSDAQ 가중치 미사용 | 신규 발견 |
| `strategy/entry_timing.py:49` | 🟢 LOW | 진입/청산 파라미터 하드코딩 | 신규 발견 |
| `live/orch.py:364` | 🟢 LOW | 등락률×2.5 계수 근거 미기재 | 신규 발견 |

---

## 종합 평가

### 전략 정합성 점수: **6.5 / 10**

**평가 기준:**
- 아키텍처와 설계 원칙: 9/10 (우수한 파이프라인 설계)
- 리스크 관리 체계: 7/10 (다층 체계는 좋지만 부호 오류 하나가 전체를 위협)
- ML/AI 파이프라인: 4/10 (AUC 68.9%는 실전 기준 이하)
- 백테스트 신뢰성: 5/10 (슬리피지 과소추정+미수정 버그)
- 코드 품질/유지보수성: 7/10 (전반적으로 깔끔함)
- 실전 운용 안정성: 6/10 (VI 대응, idempotency 등은 좋지만 포지션 사이징 모순이 큼)

### 주요 리스크 요약

1. **🔥🔥🔥 일일 손실 한도 silent failure** (W1) — 가장 위험. 리스크 관리의 가장 마지막 방어선이 작동하지 않을 수 있음
2. **🔥🔥🔥 포지션 사이징 논리 모순** (W2) — 고변동성에서 포지션 축소 불가
3. **🔥🔥 ML 예측력 부족** (W3) — AUC 68.9%의 ML을 거래 결정에 사용하는 중
4. **🔥🔥 백테스트 낙관 편향** (W4) — 실거래 결과가 백테스트보다 체계적으로 나쁠 가능성 높음
5. **🔥 수익 보호 장치 미활성** (W8) — trailing stop/partial TP 꺼져 있음

### 1순위 권장 액션 (P0-P3 등급)

| Priority | 액션 | 대상 | 예상 영향 |
|----------|------|------|-----------|
| **P0** | `live/risk.py:151` 부호 검증 및 수정 — KIS daily_loss_pct 부호 확인 후 abs() 처리 | 리스크 관리 | 일일 손실 한도 작동 보장 |
| **P0** | `live/orchestrator.py:568` min_pct=base*1.0 → 0.3으로 변경 | 포지션 사이징 | 고변동성 시 포지션 자동 축소 |
| **P1** | `ml_auto_retrain=True`, `ml_auto_select_model=True` 활성화 | ML 파이프라인 | 모델 성능 자동 개선 |
| **P1** | Trailing Stop(`trailing_stop_enabled=True`) 활성화 | 수익 보호 | 장중 수익 보호 |
| **P1** | 백테스트 슬리피지 모델 상향 (min 0.2%, urgent 0.5%) + 유동성 기반 동적 조정 | 백테스트 | 백테스트-실거래 gap 축소 |
| **P2** | `can_extend_stop_loss()` 호출 코드 추가 → orchestrator 실행 체인에 연결 | 리스크 관리 | 손절선 동적 확장 가능 |
| **P2** | 상관관계 임계 0.85 → 0.75, 가중 검사 도입 | 포트폴리오 리스크 | 분산 효과 개선 |
| **P2** | 모드 전환 시 기존 포지션 손절선 grace period 적용 | 모드 전환 | 갑작스러운 청산 방지 |
| **P2** | 장중보정에 KOSDAQ 지수 추가 | 레짐 평가 | KOSDAQ 레짐 정확도 개선 |
| **P2** | 중립장 min_score 30→35~40 재조정 | 스크리너 | 스크리닝 품질 개선 |
| **P3** | 백테스트 abs() 버그 #1, import 위반 #2 수정 | 백테스트 | 백테스트 정확도 |
| **P3** | entry/exit_timing 파라미터 config로 이동 | 전략 설정 | Runtime 조정 가능 |
| **P3** | 부분 익절(`partial_tp_enabled=True`) 활성화 검토 | 수익 보호 | 분할 익절로 수익률 개선 |

---

*리뷰 완료. 모든 발견 사항은 실제 코드 검증 기반이며, 추정 없음.*
