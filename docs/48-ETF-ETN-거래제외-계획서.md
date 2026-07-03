# ETF/ETN 종목 거래 제외 — 수정 계획서

> **문제**: 동적 유니버스가 KIS 거래량순위 API에서 ETN/ETF를 포함하여 선정. ETN은 KIS 위험고지 미등록 계좌에서 매수 불가 (APBK1629). ETF도 주식과 다른 분류, 리스크 관리 필요
> **파일**: `docs/48-ETF-ETN-거래제외-계획서.md`

---

## 분석

### 현재 유니버스 내 ETN/ETF 비중

동적 유니버스 30종목 중 약 30~40%가 ETN/ETF로 추정됨:

| 구분 | 예시 코드 | 종목명 |
|:----:|:---------:|--------|
| 일반 주식 | `005930` | 삼성전자 |
| ETN | `Q580074`, `Q570115`, `Q530131` | KB BYD 밸류체인 ETN 등 |
| ETF | `Q530142`, `400580` | KODEX코스닥, SOL 유럽탄소배출권선물 |

### KIS 분류 정보

`inquire_stock_basic` API 응답에서 분류 가능:

| 필드 | 값 예시 | 의미 |
|:----:|:-------:|------|
| `prdt_clsf_cd` | `102610` | ETN |
| `prdt_clsf_name` | `"ETN"` | ETN 분류명 |
| `prdt_type_cd` | `"300"` | 상품유형코드 (300=주식/ETN/ETF 공통) |

분류코드(`prdt_clsf_cd`)를 통해 필터링 가능:
- **일반 주식**: `prdt_clsf_cd` 미존재 또는 특정 코드
- **ETN**: `prdt_clsf_cd` = `102610` (ETN)
- **ETF**: `prdt_clsf_cd` = `101XXX` 계열 (ETF)
- **ETC/ETN**: `102XXX` 계열

---

## 해결 방안

### A안 (권장): StockInfo에 분류 정보 추가 + 유니버스 필터링

`StockInfo` dataclass에 `product_type` 필드를 추가하고, 유니버스 선정 시 ETN/ETF 제외.

**수정 항목**:

| # | 항목 | 파일 | 작업량 | 리스크 |
|:-:|------|------|:------:|:-----:|
| 1 | `StockInfo.product_type` 필드 추가 | `core/types.py` | 1줄 | 🟢 ZERO |
| 2 | `parse_stock_info()` 분류코드 매핑 | `mapper.py` | 3줄 | 🟢 ZERO |
| 3 | `_code_to_name()` → `_stock_info()`로 확장 | `real_data_provider.py` | ~15줄 | 🟢 LOW |
| 4 | `StockInfo` 생성 시 product_type 채우기 | `real_data_provider.py`, `universe_manager.py` | ~10줄 | 🟢 LOW |
| 5 | 유니버스 스크리닝 단계에서 ETN/ETF 제외 | `strategy/screener.py` 또는 `orchestrator.py` | ~10줄 | 🔵 MEDIUM |

### B안 (간단): Q-/4자리 prefix 기반 필터링

`Q`로 시작하거나 일부 특수코드를 ETN/ETF로 간주하고 스크리닝 단계에서 제외.

```
if code.startswith("Q") or code.startswith("0")[not in KOSPI/KOSDAQ]:
    continue
```

| 항목 | 평가 |
|:----|:----:|
| 개발량 | 적음 (~5줄) |
| 정확도 | **낮음** — 모든 Q코드가 ETN/ETF는 아님 (Q코드 중 일반 주식도 일부 있음) |
| 유지보수 | KIS 정책 변경 시 깨질 수 있음 |

**비권장**: 오탐률 높음.

### C안 (정밀): KIS `prdt_clsf_cd` 기반 + 캐싱

초기 로드 시 각 종목의 `prdt_clsf_cd`를 조회하고 캐싱, 필터링.

```
_stock_class_cache: dict[str, str] = {}  # code → "STOCK"/"ETF"/"ETN"

분류 로직:
  prdt_clsf_cd.startswith("10"): 일반주식
  prdt_clsf_cd.startswith("101"): ETF
  prdt_clsf_cd.startswith("102"): ETN
```

| 항목 | 평가 |
|:----|:----:|
| 정확도 | **높음** — KIS 공식 분류 코드 사용 |
| 추가 API 호출 | 종목정보 API는 이미 `_code_to_name()`에서 호출 중 → 추가 부하 없음 |
| 구현량 | 중간 (~20줄) |

---

## 권장: A안 (StockInfo 확장 + 유니버스 필터링)

### 상세 구현

**1. StockInfo에 product_type 추가** (`core/types.py`)

```python
@dataclass
class StockInfo:
    code: str
    name: str
    market: Market
    sector: str = ""
    product_type: str = "stock"  # stock / etf / etn / etn_etc
```

**2. 분류코드 매핑 추가** (`data/adapters/kis/mapper.py`)

```python
def _classify_product(prdt_clsf_cd: str) -> str:
    """KIS 상품분류코드 → product_type."""
    if not prdt_clsf_cd:
        return "stock"
    if prdt_clsf_cd.startswith("101"):
        return "etf"
    if prdt_clsf_cd.startswith("102"):
        return "etn"
    return "stock"

# parse_stock_info() 내 추가:
product_type = _classify_product(str(raw.get("prdt_clsf_cd", "") or ""))
return StockInfo(code=code, name=name, market=market, product_type=product_type)
```

**3. 유니버스 필터링** (`live/orchestrator.py`, screener)

스크리닝 단계에서 `candidate.product_type != "stock"` 제외:

```python
# Step 5: Screen stocks — ETN/ETF 제외
screen_result.candidates = [
    c for c in screen_result.candidates
    if c.product_type == "stock"
]
```

또는 UniverseManager 초기화/스왑 시 제외:

```python
# universe_manager.py swap() — 상위 20 선정 시 ETN/ETF 제외
to_add = [
    s for s in candidates[:min(20, len(candidates))]
    if s.product_type == "stock"
]
```

**4. 캐시/호환성**

기존 저장된 모델/데이터에 `product_type="stock"` 기본값 적용. 새로 로드된 StockInfo만 분류됨. 캐시 TTL 1일 후 자동 업데이트.

---

## 리스크 매트릭스

| 항목 | 리스크 | 영향 | 완화 |
|:----|:-----:|------|------|
| 분류코드 불완전 | 🟢 LOW | 분류 못하면 "stock" 기본값 → 기존과 동일 | 기본값="stock" |
| API 응답 없음 | 🟢 LOW | product_type 기본값 유지 | — |
| 필터링으로 인한 유니버스 축소 | 🟢 LOW | 30→~18종목 | 충분한 거래량 (상위 100위 이내) |
| 코스닥 일반주식을 ETN으로 오분류 | 🟢 LOW | prdt_clsf_cd 기반이므로 오탐률 낮음 | KIS 분류코드 신뢰 |

---

## 실행 계획

| Phase | 작업 | 작업량 |
|:----:|------|:------:|
| 1 | `StockInfo.product_type` 필드 추가 | 1줄 |
| 2 | `parse_stock_info()` 분류코드 매핑 | 5줄 |
| 3 | `_code_to_name()` → classification 캐시 확장 | 10줄 |
| 4 | 유니버스 생성 시 classification 채우기 | 5줄 |
| 5 | 스크리너/orchestrator에서 ETN/ETF 필터링 | 5줄 |
| **합계** | | **~30분** |

**리스크**: LOW — 모든 변경사항은 기본값("stock") fallback으로 하위호환성 유지
