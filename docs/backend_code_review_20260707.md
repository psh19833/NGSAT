이제 수집된 모든 데이터를 바탕으로 NGSAT 전체 코드 리뷰 보고서를 작성하겠습니다.

---

## NGSAT 전체 코드 리뷰 — 시니어 백엔드 개발자 관점

**리뷰 일자:** 2026-07-07  
**HEAD:** `7e6556d`  
**분석 범위:** Python 백엔드 전체 (~40개 파일, 27개 테스트 파일, 329개 테스트 메서드)

---

## S — 강점 (Strengths)

### S1. 견고한 테스트 기반 (329개 테스트, 27개 파일)
총 329개 테스트 메서드가 27개 파일에 분산되어 있으며, `test_live/test_risk.py`(23개), `test_data/test_kis_mapper.py`(21개) 등 밀도 높은 커버리지를 보여줌. 각 테스트 클래스가 명확한 범위를 가지며, Mock/테스트 Fixture(`conftest` 없이 파일별 fixture)를 통해 독립적 실행이 가능.

### S2. 명시적 Import 격리 컨벤션
`live/orchestrator.py`(L4), `live/executor.py`(L4), `backtest/engine.py`(L4), `backtest/data_loader.py`(L4) — 모든 모듈 시작부에 **"MUST NOT import anything from backtest/live"** docstring이 명시되어 있음. 실제로 `live/` 디렉토리 내에서 `from backtest` import는 전혀 없으며 그 역도 성립. 이는 모듈 간 순환 의존성을 원천 봉쇄한 좋은 설계 결정.

### S3. Idempotency 패턴 적용
`live/executor.py` L91-92: `_submitted_orders: dict[tuple[str, str], str]` + `asyncio.Lock()`으로 동일 주문 중복 제출을 방지. P-54 수정으로 명시적으로 적용됨. 사이클 시작 시 `clear_idempotency()` 호출.

### S4. VI (변동성 완화) 대응 로직
`live/executor.py` L99-144: VI 발동 시 시장가→지정가 전환, VI 상태 미확인 시에도 안전하게 fallback. 금융 시스템의 장애 상황에 대한 pragmatic한 대응.

### S5. 안전한 자격 증명 관리
KIS API 키/시크릿은 `os.getenv()`를 통해서만 로드되며(`core/config.py` L231-232, `adapter.py` L115-116), 로그에 평문 노출되지 않음. `KisToken` 클래스에 `__repr__`에서 토큰 마스킹 적용됨 (`test_kis_token.py` L43에서 검증).

### S6. 합성 데이터 Guard
`data/repository.py` L59-61: 정규식 `^synthetic_\d+$`으로 합성 데이터가 실거래 DB에 저장되는 것을 방어. 실수로 합성 데이터가 트레이딩 결과로 기록되는 것을 차단.

---

## W — 약점 (Weaknesses) — 심각도별

### 🔴 CRITICAL

#### W1. Orchestrator 단일 책임 원칙(SRP) 심각 위반 (1110줄)
`live/orchestrator.py` (1110줄)는 다음 모든 책임을 하나의 클래스가 담당:
- 매매 사이클 `run_cycle` (핵심 비즈니스 로직)
- DB `sessionmaker` 직접 생성/관리 (L147-150)
- Raw SQLAlchemy 세션 사용 (L827-835, L1002-1019) — Repository 패턴 우회
- 레짐 평가 결과 저장 (L402-410)
- 포지션 관리 및 정리 (L774-810)
- 미체결 주문 조회/취소 (L1060-1110)
- 텔레그램 수동 주문 처리 (`_execute_manual_orders`)
- `_last_diagnosis` 딕셔너리 저장
- 사이클 요약 문자열 생성
- **`except Exception` 핸들러 14개** — 모든 예외를 잡아 로깅만 하고 진행

이로 인해 `run_cycle()` 하나의 메서드가 200줄 이상이며, `run_cycle()` 도중 어디서 실패하든 모두 같은 `except Exception`으로 빠짐.

#### W2. Rate Limit 분산 관리 — 중앙 Throttler 부재
`asyncio.sleep()`을 통한 Rate Limit 보호가 **15군데 이상**에 분산됨:
- `data/adapters/kis/client.py` L79: `sleep(0.05 - elapsed)` — 유일한 token bucket 시도
- `data/adapters/kis/adapter.py` L700, L728: 각각 `sleep(0.1)`
- `data/real_data_provider.py` L168, L187, L428, L470, L487: 5회
- `data/universe_manager.py` L388: 1회
- `live/orchestrator.py` L454: 1회
- `scripts/backfill_daily_data.py` L137, `scripts/collect_minute_data.py` L172
- `live/executor.py` L312, L408: 체결가 조회 전 sleep

KIS API는 초당 ~20회 호출 제한이 있으나, 어댑터 레벨에서 토큰 버킷(Token Bucket) 시도가 `client.py`에 한 곳만 있고, 나머지는 **임시방편 sleep**. 유니버스 교체 시 `swap_universe()`에서 N개 종목 각각 `sleep(0.1)` → 40종목이면 4초, 이 시간 동안 전체 사이클이 차단됨.

#### W3. 포괄 예외 처리 (Naked Except) — Silent Failure 위험
**측정 결과:**
| 파일 | `except Exception` 개수 | 심각한 Silent Pass |
|---|---|---|
| `live/orchestrator.py` | 14 | 사이클 메인 예외 포함 |
| `dashboard/backend/api.py` | 14 | 지수/프리셋/재학습 |
| `main.py` | 11 | 백테스트/초기화 실패 |
| `data/adapters/kis/adapter.py` | 5 | get_investor_data/get_financial_ratio |

대표적인 문제:
- `api.py` L408: `except Exception: pass` — KOSPI/KOSDAQ/US 지수 조회 실패 시 완전 무시. 대시보드에 빈 값만 표시되어 사용자 혼란.
- `api.py` L642: 프리셋 감지 실패 시 `except Exception: pass`
- `executor.py` L122, L302-303, L408: VI 조회/체결가 조회 실패 시 `except Exception: pass`
- `orchestrator.py`에서 `run_cycle`의 다양한 내부 단계가 모두 같은 수준의 `except Exception: logger.warning()`으로 처리되어, 장애 발생 시 원인 파악이 어려움.

#### W4. DB 세션 관리 — Repository 패턴 붕괴
`live/orchestrator.py` L147-150:
```python
Session = sessionmaker(bind=self._db_engine)
self._Session = Session  # sessionmaker factory
self._db_session = Session()  # ← 영구 세션 생성
```

Orchestrator가 직접 `sessionmaker`를 생성하고, 별도의 `_db_session` 인스턴스 변수를 유지. L827에서 `with self._Session() as session:`으로 raw 세션을 직접 사용하며 Repository를 우회. `main.py` L327에서도 `orchestrator._Session()`으로 외부에서 직접 세션 생성.

**결과:** `TradeRepository`가 있지만(Injected via constructor), orchestrator 내부에서는 직접 세션으로 조회/저장. 트랜잭션 경계가 모호해지고, 세션 라이프사이클 관리가 어려워짐.

---

### 🟡 HIGH

#### W5. asyncio 환경에서 동기 `time.time()` 사용
`time.time()` vs `time.monotonic()` 혼용:
- `live/orchestrator.py`: 2회 `time.time()` 사용 (L813 등)
- `main.py`: 1회 `time.time()` 사용
- `data/adapters/kis/adapter.py`: `time.monotonic()` 사용 (L94 등) — 일부 개선됨

`time.time()`은 시스템 시간 변경(NTP 등)에 영향을 받고, asyncio 이벤트 루프의 cooperative multitasking과 충돌 가능성. `asyncio.get_event_loop().time()` 또는 `time.monotonic()`으로 일관성 필요.

#### W6. Shutdown 시 WebSocket 정리 불완전
`main.py` L655-658:
```python
for task in tasks:
    task.cancel()
await asyncio.gather(*tasks, return_exceptions=True)
```

이후 L661-665에서 `data_provider.close()` → `ws.disconnect()`가 호출되지만, task 취소가 먼저 실행되면서 WebSocket listen 태스크가 `CancelledError`로 종료됨. listen 루프(`websocket_client.py` L122)가 CancelledError를 잡지 않으면 `disconnect()`가 호출되지 않을 수 있음.

**좀비 태스크 위험:** `_start_websocket`은 `asyncio.create_task()`로 생성(`real_data_provider.py` L220)되나, `main.py` L606의 `tasks` 리스트에 포함되지 않음. `await asyncio.gather(*tasks)`의 대상이 아님. `data_provider.close()`에서만 관리됨.

#### W7. `datetime.now()` 시간대 혼용
Naive datetime과 Timezone-aware datetime이 혼용:
- `core/types.py`: `KST = timezone(timedelta(hours=9))` 정의
- `live/orchestrator.py`: `datetime.now()` (naive) 사용 — 주문 시각 비교 등
- `data/adapters/kis/adapter.py`: `datetime.now()` (naive) 사용
- `data/real_data_provider.py`: `datetime.now(KST)` 사용
- `dashboard/backend/api.py`: `datetime.now()` (naive) 사용

동일한 프로젝트 내에서 표준화 부재. 특히 orchestrator의 `cancel_unfilled_orders`(L1074)는 `datetime.now()` naive로 주문 시각을 비교 → 자정 넘김 처리(L1094: `age += 86400`)가 불완전.

#### W8. API 엔드포인트에서 orchestrator 내부 속성 직접 접근
`dashboard/backend/api.py`에서 `orch._broker`(L274), `orch._trade_repo`(L329), `orch._controller`(L250), `orch._current_mode`(L260), `orch._cycle_count`(L259), `orch._last_diagnosis`(L810), `orch._inference._model`(L577), `orch._preset_router`(L228) 등 **프라이빗 속성(`_` prefix)에 대해 getattr() 또는 직접 접근**을 10회 이상 사용.

캡슐화 완전히 붕괴. 새 리팩토링 시 모든 API 엔드포인트를 수정해야 하는 취약점.

#### W9. 메인 루프의 포괄 예외 처리로 인한 진단 불가
`main.py` `trading_loop()` 내부:
```python
try:
    # 전체 사이클 실행
except Exception as e:
    logger.warning(f"사이클 예외 (치명적 아님): {e}")
    await asyncio.sleep(backoff)
```

백오프(backoff)가 `min(backoff * 2, 60)`로 최대 60초까지 증가하지만, **예외가 계속 발생해도 감지되지 않음**. 리스크 한도 초과, API 자격증명 만료, 디스크 공간 부족 등 치명적 오류도 모두 같은 수준으로 처리되어 시스템이 조용히 비정상 상태로 운영될 수 있음.

#### W10. Live-Backtest: main.py를 통한 간접 결합
직접 import는 차단되었으나 `main.py`가 양쪽을 모두 import:
- `main.py` L65-67: `from backtest.data_loader import ...`, `from backtest.engine import BacktestEngine`
- `main.py` L671-672: `from data.real_data_provider import RealDataProvider`

`run_backtest()` 함수가 KIS 실데이터 로드 → 실패 시 합성 데이터 폴백 → 백테스트 엔진 실행. `main.py`가 live/backtest/data 세 계층을 모두 조정하는 God Object 역할. 새로운 모드(paper trading 등) 추가 시 main.py가 더 비대해질 위험.

---

### 🔵 MEDIUM

#### W11. ConfigService 세션 Leak
`dashboard/backend/api.py` L202:
```python
sess = sessionmaker(bind=engine)()
config_service = ConfigService(sess)
app.state.config_service = config_service
```

생성된 세션 `sess`가 close() 없이 app.state에 저장됨. FastAPI 앱 종료 시점에 세션이 정리되지 않으면 연결 풀 소진 가능성. `ConfigService.__init__`에서 세션을 받지만 close 메서드가 없음 (`core/config_service.py`).

#### W12. WebSocket URL 하드코딩
`data/adapters/kis/websocket_client.py` L31-32:
```python
WS_URL_REAL = "ws://ops.koreainvestment.com:21000"
WS_URL_DEMO = "ws://ops.koreainvestment.com:31000"
```

KIS WebSocket URL이 환경변수나 config에서 분리되지 않고 소스 코드에 하드코딩. API 엔드포인트는 `config.kis.base_url`에서 관리되나 WebSocket은 별도 관리. `ws://` (비암호화) 사용 — 프로덕션에서 wss:// 사용 권장.

#### W13. 세션 flush 후 예외 처리 부재
`data/repository.py` L77:
```python
self._session.add(record)
self._session.flush()  # ← 예외 발생 시 close/rollback 미흡
```

`save_trade()` 호출 후 예외가 발생하면 `self._session`이 dirty 상태로 유지될 수 있음. 트랜잭션 경계가 명확하지 않음.

#### W14. 테스트: 통합 테스트 부족
- 단위 테스트 27개 파일 / 329개 메서드 — 양호
- **통합 테스트 1개 파일** (`test_integration.py`, 14개 메서드) — 실 데이터를 사용한 엔드투엔드 테스트 부족
- **Rate Limit 테스트 없음** — Throttler 부재로 ad-hoc sleep에 의존하나, sleep 간격이나 토큰 버킷 동작 검증 없음
- **Shutdown 테스트** — controller.shutdown() 테스트는 있으나, `signal_handler` + task.cancel() + WebSocket cleanup의 전체 시퀀스 테스트 없음
- **Race condition 테스트 없음** — `asyncio.Lock`이 사용되는 executor의 `_submit_with_retry`나 adapter의 `_cached_balance`에 대한 동시성 테스트 부재

#### W15. `os.getenv()` 분산 호출
`core/config.py`와 `data/adapters/kis/adapter.py`에서 각각 `os.getenv()` 호출. 중앙 설정 로드 → DI로 전파하는 패턴이 완전하지 않음. `KisAdapter.from_env()`(adapter.py L104)은 static factory이지만 config 시스템 우회.

---

### 🟢 LOW

#### W16. 시스템 종료 시 텔레그램 알림 실패 무시
`main.py` L651:
```python
except Exception:
    pass
```

종료 알림 전송 실패를 완전 무시. 사용자가 종료 알림을 못 받아도 알 수 없음.

#### W17. `f-string` 로깅 vs `%`-formatting 혼용
일부는 `logger.info(f"...")`, 일부는 `logger.info("...", extra)`. 성능 영향은 미미하나 일관성 부족.

#### W18. Docstring 과잉 — 유지보수 부담
많은 메서드에 10줄 이상의 docstring + Args/Returns/Raises 섹션. 좋은 컨벤션이나 리팩토링 시 docstring 업데이트 누락 위험. 일부 docstring이 실제 파라미터와 불일치.

---

## O — 기회 (Opportunities)

### O1. 중앙 집중형 Rate Limiter 도입
`data/adapters/kis/client.py` L79에 token bucket 기반의 rudimentary rate limiter가 이미 존재. 이를 확장하여 모든 KIS API 호출을 통과하는 전역 `KisRateLimiter`를 adapter 레벨에 도입하면 15군데 분산된 sleep을 제거하고 정확한 Rate Limit 준수 가능. asyncio.Semaphore + Token Bucket 조합으로 초당 처리량 정밀 제어.

### O2. Orchestrator 분할 — 관심사별 분리
1110줄의 orchestrator를 다음과 같이 분할 가능:
- `CyclicScheduler` — 사이클 타이밍, tick 관리
- `TradePipeline` — run_cycle의 각 단계(레짐 평가 → 스크리닝 → ML 추론 → 포지션 관리 → 주문 실행)
- `PositionManager` — 포지션 추적, P&L 계산
- 기존 `RiskManager`, `OrderExecutor`, `TradingController`와 조합

### O3. FastAPI Lifespan 이벤트 활용
현재 `main.py`에서 수동으로 App 상태를 관리. FastAPI의 `lifespan` 컨텍스트 매니저(startup/shutdown)를 사용하면 `app.state` 초기화와 정리가 체계화되고, signal handler + task 정리도 일관되게 처리 가능.

### O4. Repository 패턴 강화
`TradeRepository`가 있으나 orchestrator가 우회 사용. 모든 DB 액세스를 Repository로 통일하고, Unit of Work 패턴을 도입하면 트랜잭션 경계가 명확해짐. SQLAlchemy `session.begin()`을 명시적으로 사용.

### O5. 정식 통합 테스트 파이프라인
Mock 기반 단위 테스트(329개)는 잘 갖춰져 있음. 하나의 통합 테스트(`test_integration.py`)를 mock KIS API 응답을 사용한 실제 실행 시나리오로 확장 가능. `docker-compose` + test PostgreSQL로 격리된 테스트 환경 구축.

### O6. WebSocket 보안 강화
`ws://` → `wss://` 전환 검토. KIS의 WebSocket wss:// 지원 여부 확인 필요. config 환경변수화.

### O7. Pyright/Pyflakes 정적 분석 도입
현재 AST 기반 lint만 있음(예: `ruff`). 타입 힌트가 광범위하게 사용(`from __future__ import annotations`)되고 있으나 실제 타입 검증은 부재. `mypy` 또는 `pyright` strict mode 도입으로 `_` 속성 접근 등 숨은 버그 조기 발견.

---

## T — 위협 (Threats)

### T1. KIS API 변경 시 광범위한 영향
모든 KIS API 호출이 `data/adapters/kis/adapter.py` 한 곳에 집중되어 있으나, 응답 파싱(`mapper.py`), 엔드포인트 정의(`endpoints.py`), 인증(`token_manager.py`), WebSocket(`websocket_client.py`)이 분산됨. KIS API 필드명(`stck_prpr`, `acml_vol` 등)이 mapper를 통해 추상화되나, 유지보수 담당자가 KIS 문서를 정기적으로 확인해야 함.

### T2. 단일 장애점: orchestrator
W1에서 지적한 orchestrator 과부하. orchestrator의 inner loop에서 복구 불가능한 예외 발생 시 전체 매매 중단. 사이클은 10초 tick으로 도나, 한 번의 실패가 사이클 전체를 무효화. Phase-structured retry 도입 필요 (레짐 평가 실패 = 재시도, 주문 실패 = skip).

### T3. WebSocket 불안정 — Rate Limit 증폭
WebSocket 연결이 끊어지면 REST polling으로 fallback(`real_data_provider.py` L381). 40종목의 실시간 가격을 REST로 폴링하면 초당 40회 호출 → KIS Rate Limit(초당 ~20회) 초과. fallback 시 **cooldown**이 필요하지만 현재 없음.

### T4. DB 트랜잭션 격리 문제
`live/orchestrator.py`에서 직접 세션 생성(L148)이 main.py(L327)와 별도로 이루어짐. 동일 프로세스 내에서 여러 세션이 동시에 DB에 접근할 때 **격리 수준(Isolation Level) 불일치**로 인한 dirty read 가능성. 기본 SQLAlchemy 세션은 `REPEATABLE READ`가 아닌 `READ COMMITTED`.

### T5. 증권사 정책 변경 — API 사용료
KIS가 무료 API 정책을 변경하거나, 트래픽 과금 도입 시 운영 비용 급증. 특히 WebSocket + REST 병행 사용으로 인한 API 호출량이 큼. Throttler 없이 ad-hoc sleep으로는 정밀 제어 불가.

### T6. `Task.cancel()`로 인한 asyncio.CancelledError 전파 누락
`main.py` L657: `task.cancel()` 후 `await asyncio.gather(*tasks, return_exceptions=True)`로 CancelledError를 수집하나, 일부 태스크(WebSocket listen 등)가 CancelledError를 적절히 처리하지 못하면 리소스 누수 발생.

---

## 상세 파일별 발견 요약 테이블

| 파일 | 라인 | 심각도 | 발견 사항 |
|---|---|---|---|
| `live/orchestrator.py` | 1110 | 🔴 CRITICAL | SRP 위반 — 14개 except Exception, DB 직접 세션, 모든 책임 집중 |
| `data/adapters/kis/adapter.py` | 734 | 🔴 CRITICAL | 15+ 분산 sleep, 중앙 Rate Limiter 부재 |
| `live/executor.py` | 464 | 🔴 CRITICAL | 4회 `except Exception: pass` — 체결가/VI 조회 silent failure |
| `dashboard/backend/api.py` | 905 | 🔴 CRITICAL | 14회 except Exception, orchestrator 프라이빗 속성 직접 접근(10+), ConfigService 세션 누수 |
| `main.py` | 724 | 🟡 HIGH | 11회 except Exception, L655 WebSocket task 취소 순서 불완전, signal handler 동기 |
| `live/orchestrator.py` | L147-150 | 🔴 CRITICAL | sessionmaker 직접 생성 + Repository 패턴 우회 |
| `data/repository.py` | L77 | 🔵 MEDIUM | flush() 후 예외 시 rollback 부재 |
| `data/adapters/kis/websocket_client.py` | L31-32 | 🔵 MEDIUM | WebSocket URL 하드코딩, ws:// 사용 |
| `core/config.py` | L231-232 | 🟢 LOW | os.getenv() 분산 호출 — KisAdapter.from_env()에서 우회 |
| `live/controller.py` | 90 | 🟢 LOW | shutdown() 동기 메서드 — cleanup 로직 없음 |
| `live/position_sizer.py` | - | 🔵 MEDIUM | (확인 필요) Kelly 기반 position size |
| `data/real_data_provider.py` | L220 | 🟡 HIGH | WebSocket task가 main.py tasks 리스트에 미포함 (좀비 위험) |
| `data/real_data_provider.py` | L335-346 | 🟡 HIGH | close()에서 ws.disconnect() 호출 순서 — task.cancel() 후에도 동작하나 race condition 가능성 |
| `data/universe_manager.py` | 391 | 🔵 MEDIUM | 1회 asyncio.sleep(0.1) — bulk API 호출 후 |
| `strategy/regime.py` | 385 | 🟢 LOW | — |
| `strategy/screener.py` | 391 | 🟢 LOW | — |
| `tests/` (27 files) | 329 tests | 🟡 HIGH | 통합 테스트 1개, 동시성 테스트 0개, shutdown 시퀀스 테스트 0개 |

---

## 종합 평가

NGSAT은 **견고한 도메인 모델링(레짐/리스크/스크리너 분리)**과 **좋은 단위 테스트 커버리지(329개)**를 갖춘 프로젝트입니다. 명시적인 Import 격리 컨벤션, Idempotency 패턴, VI 대응 로직 등 금융 거래 시스템으로서 필요한 많은 모범 사례가 적용되어 있습니다.

그러나 **코드베이스의 성숙도와 확장성에 심각한 리스크**가 있습니다:

1. **Orchestrator (1110줄)가 단일 장애점** — 모든 비즈니스 로직이 한 클래스에 집중되어 있으며, 14개의 포괄 예외 처리로 인해 장애 탐지가 어렵습니다.

2. **Rate Limit 관리가 체계적이지 않음** — 15군데 이상 분산된 ad-hoc `asyncio.sleep()`은 KIS API 제한에 대한 대응이 아닌 증상 완화(workaround)에 가깝습니다.

3. **DB 세션 관리가 Repository 패턴을 우회** — `TradeRepository`는 존재하나 orchestrator 내부에서는 직접 raw 세션으로 접근하여 데이터 액세스 일관성이 깨집니다.

4. **Graceful shutdown이 불완전** — WebSocket task 정리, 리소스 해제 순서, 좀비 태스크 위험이 있습니다.

5. **API 계층의 캡슐화 붕괴** — 대시보드 API가 orchestrator의 프라이빗 속성에 `_` prefix를 무시하고 직접 접근합니다.

### 1순위 권장 액션

| 순위 | 액션 | 영향도 | 예상 공수 |
|---|---|---|---|
| **1** | Orchestrator 리팩토링: `CyclicScheduler` + `TradePipeline` + `PositionManager` 분할 | `live/orchestrator.py` 1110L → 400L 이하, `run_cycle()` 책임 분산 | 2-3주 |
| **2** | 중앙 집중형 `KisRateLimiter` 도입 (Token Bucket + asyncio.Semaphore) | 15+ 분산 sleep 제거, KIS Rate Limit 완전 준수 | 3-5일 |
| **3** | 모든 DB 접근을 Repository + UoW로 통일 (orchestrator 내 raw session 제거) | 트랜잭션 일관성, 테스트 용이성 | 1-2주 |
| **4** | FastAPI `lifespan` 패턴 도입 + WebSocket task 정리 로직 안정화 | 좀비 태스크 제거, shutdown 신뢰성 | 3-5일 |
| **5** | API 계층 DTO/Interface 분리 — orchestrator 프라이빗 속성 직접 접근 금지 | 캡슐화 회복, 리팩토링 내성 | 1주 |
| **6** | 일일 통합 테스트 + Rate Limit 테스트 + Fault Injection 테스트 추가 | 329개 테스트 + 통합 20개 목표 | 1주 |

### 시급도 요약

```
지금 당장: Rate Limiter 도입 (KIS API 차단 위험)
이번 주:   Orchestrator 분할 설계
이번 달:   DB Repository 통일 + API 캡슐화
다음 달:   통합 테스트 + Shutdown 안정화
```