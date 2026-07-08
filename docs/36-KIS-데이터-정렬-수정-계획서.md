# 36-KIS-데이터-정렬-수정-계획서.md
> 작성일: 2026-07-08 11:40
> 상태: 초안 (서버 재시작 필요 — 장 마감 후 적용 권장)

---

## 1. 리스크 검토 (Risk Assessment)

### 1.1 현재 운영 리스크: 🔴 심각 (P0)

**KIS 일봉 데이터가 내림차순(최신→과거)으로 반환되나,  
모든 기술지표/스크리너/점수체계가 오름차순(과거→현재)을 가정하고 있습니다.**

| 항목 | 상태 | 영향 |
|------|------|------|
| 스크리너 정상 동작 여부 | ❌ **비정상** | 지표값이 엉터리 — 0종목 또는 무작위 종목 통과 |
| 레짐 평가 | ⚠️ 부분 정상 | 지수는 별도 조회로 비교적 정상이나, 장중보정(correction) 영향 있음 |
| 일일매매 판단 | ❌ **신뢰 불가** | 현재가·MA·RSI 등 모든 지표가 실제 시장과 다른 데이터 기준 |
| 운용 손실 위험 | 🟡 중간 | position=0으로 노출 없으나, 조건 충족 시 잘못된 매수 진입 가능성 |
| 과거 실거래 영향 | 🟡 중간 | 이전 수익/손실 중 데이터 오류 영향분과 정상분 구분 불가 |

### 1.2 수정 시 리스크

| 리스크 | 등급 | 설명 | 완화방안 |
|--------|------|------|----------|
| 기존 ML 모델 예측 괴리 | 🟡 중간 | 데이터 정렬 변경으로 지표값 전면 변화 → 기존학습 모델의 feature 분포 변화 | 장 마감 후 수정 + 모델 재학습 |
| 데이터 일관성 깨짐 | 🟢 낮음 | parse_price_history만 수정 시 DB/캐시 기존 데이터는 역순 상태 | refresh에서도 변환 적용 |
| refresh 시 이중 역전환 | 🟢 낮음 | refresh가 old+new bars 병합 시 순서 불일치 | refresh 로직도 동시 수정 |
| 장중 수정 시 | 🔴 심각 | 재시작 필요 (5~10초 downtime) | 장 마감 후 배포 권장 |

### 1.3 미수정 시 리스크 (계속 방치)

| 리스크 | 등급 | 설명 |
|--------|------|------|
| 무의미한 매매 신호 | 🔴 심각 | 시스템이 정상 작동 중으로 착각하고 잘못된 매수 진입 |
| 누적 오염 | 🟡 중간 | refresh 버그로 하루가 지날수록 데이터 배열 오염 심화 |
| 진단현황 신뢰도 하락 | 🟡 중간 | 스크리너 0종목 지속 → 사용자 혼란 |

---

## 2. 분석 보고

### 2.1 문제: KIS 일봉 데이터 역순 (근본 원인)

**KIS API `inquire_daily_chart`**는 100개의 일봉 데이터를 **최신순(내림차순)**으로 반환합니다.

```
KIS API 응답 (output2):
  [0]  = 2026-07-08  (오늘)
  [1]  = 2026-07-07
  [2]  = 2026-07-04
  ...
  [99] = 2026-02-09  (가장 과거)
```

그러나 모든 기술지표 함수(`current_rsi`, `sma`, `macd`, `stochastic`, `adx`, `obv`, `mfi` 등)는 **오름차순(과거→현재)**을 가정합니다:

```python
# 오름차순 가정: closes[0]=가장 과거, closes[-1]=오늘
rsi = current_rsi(closes, 14)      # closes[-14:] = 가장 오래된 14일
ma5 = sma(closes, 5)[-1]           # 가장 오래된 5일 평균
current_price = closes[-1]         # 2월9일 가격
```

**실제 영향 (삼성전자 005930):**
| 지표 | 정상(오름차순) | 현재(내림차순) |
|------|---------------|----------------|
| 현재가 | 286,000원 | **166,400원** (2월9일 종가) |
| MA5 | 299,100원 | **171,960원** |
| RSI(14) | 43.4 | **37.8** |
| ATR | 정상 변동성 | **왜곡** |

→ `current_price(166,400) < ma5(171,960)` → 잘못된 **역배열 신호**  
→ `RSI 37.8` → 실제보다 낮은 RSI → 의사 신호  
→ 장기적으로는 확률에 의해 일부 종목 통과하나 **의미 없는 값**

### 2.2 연쇄문제: refresh_prices() 바 업데이트 버그 (P1)

`real_data_provider.py:refresh_prices()` (10초마다 실행):

```python
new_bars = await adapter.get_price_history(code, start, now)  # 내림차순 반환
if new_bars:
    latest = new_bars[-1]    # ❌ [-1] = 가장 오래된 데이터 (4~5일 전)
    prices[-1] = latest       # 같은 날짜면 교체 (되어도 오래된 값)
    # 또는
    prices.append(latest)     # 새 거래일 추가 (되어도 오래된 값)
```

10초마다 **4~5일 전 데이터를 최신 바 위치에 덮어쓰기** → 데이터 배열 끝이 계속 오염

### 2.3 부차문제: screener_neutral_min_score env 기본값 (P2)

| 위치 | 값 |
|------|-----|
| `StrategyConfig` class default (line 122) | **35.0** |
| `load_config()` os.getenv default (line 262) | **"30.0"** |

env가 미설정 시 class default 35가 아닌 30 적용.  
(다만 P0 해결 시 오히려 더 많은 후보 통과시키는 방향이므로 영향도 낮음)

---

## 3. 수정 계획

### 3.1 P0: `parse_price_history()` 데이터 정렬 추가 🎯 [최우선]

**파일:** `data/adapters/kis/mapper.py` (line 171 이후)

**수정내용:** 반환 전 timestamp 기준 오름차순 정렬

```python
def parse_price_history(raw: dict[str, Any], code: str = "") -> list[PriceData]:
    ...
    result.append(...)

    # KIS는 내림차순(최신순) 반환 — 모든 지표가 오름차순 가정하므로 정렬
    result.sort(key=lambda x: x.timestamp)
    return result
```

또는 `adapter.py:get_price_history()`에서 변환:

```python
history = parse_price_history(resp.raw, code)
history.reverse()  # KIS 내림차순 → 오름차순
return history
```

**영향 범위:**
- ✅ 모든 지표 함수 정상화 (RSI, MA, MACD, ADX, 스토캐스틱, OBV, MFI)
- ✅ 스크리너 정상화 (현재가, 시가, 패턴감지)
- ✅ refresh_prices()에서 `new_bars[-1]`이 올바른 최신값으로 동작
- ✅ 레짐 평가 장중보정 정상화

**검증:** 수정 후 load 데이터:
```python
prices[0].timestamp  # 가장 과거 (정상)
prices[-1].timestamp # 오늘 (정상)
```

### 3.2 P1: `refresh_prices()` latest 선택 로직 보강 [동시 수정]

**파일:** `data/real_data_provider.py` (line 473)

`parse_price_history`에 정렬이 추가되면 `new_bars[0]`이 가장 과거, `new_bars[-1]`이 최신이 되므로  
**기존 코드 `new_bars[-1]`이 정상 동작하게 됨** → 별도 수정 불필요! ✅

다만 안전장치로 timestamp 비교 로그 추가:
```python
if new_bars:
    latest = new_bars[-1]  # P0 수정 후: new_bars[-1] == 최신 (정상)
```

### 3.3 P2: screener_neutral_min_score env 기본값 통일 [저위험]

**파일:** `core/config.py` (line 262)

```python
# 30.0 → 35.0 (class default와 통일)
s.screener_neutral_min_score = float(os.getenv("NGSAT_SCREENER_NEUTRAL_MIN_SCORE", "35.0"))
```

### 3.4 수정 순서

```
Step 1: mapper.py — parse_price_history 정렬 추가  (P0)
Step 2: config.py — env default 통일                   (P2)
Step 3: pytest 실행 (전체 227개 통과 확인)
Step 4: 서버 재시작 (장 마감 후)
Step 5: 오픈 후 스크리너 0종목 해소 확인
Step 6: (선택) 분봉ML 모델 재학습 (데이터 분포 변화 대응)
```

---

## 4. 실행 일정 제안

| 단계 | 시기 | 소요시간 | 작업자 |
|------|------|----------|--------|
| 코드 수정 (P0+P2) | **장 마감 직후** | 10분 | 개발 |
| pytest 검증 | 수정 직후 | 2분 | 개발 |
| 서버 재시작 | 수정 완료 후 | 30초 | 개발 |
| 익일 오픈 모니터링 | 다음날 09:00~09:30 | 30분 | 운영 |

---

## 5. 다음 진행할 내용

**오늘 장 마감 후 바로 수정 → 재시작 → 익일 확인**합니다.

1. **P0 코드 수정** (mapper.py) — 1줄 추가
2. **P2 코드 수정** (config.py) — 1줄 변경
3. **pytest 전체 통과 확인**
4. **서버 재시작** (장 마감 후)
5. **익일 09:10 이후 진단현황 스크리너 8+ 종목 통과 확인**

---

## 6. 부록: 데이터 정렬 검증 결과

```
P0 미수정 상태 (현재): prices[0]=오늘, prices[-1]=2월9일
  → RSI=37.8, MA5=171,960, 현재가=166,400 (모두 왜곡)

P0 수정 후 예상: prices[0]=2월9일, prices[-1]=오늘
  → RSI=43.4, MA5=299,100, 현재가=286,000 (정상)

결론: 단 1줄(result.sort)로 모든 지표 정상화 가능
```
