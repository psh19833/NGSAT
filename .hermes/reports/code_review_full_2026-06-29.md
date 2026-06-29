# NGSAT 전체 코드리뷰 결과보고서 (2026-06-29)

## 리뷰 개요

| 항목 | 내용 |
|------|------|
| 리뷰 대상 | NGSAT (New Generation Stock Auto Trader) — 활성 프로젝트 |
| 위치 | /home/psh19/NGSAT |
| 리뷰 일자 | 2026-06-29 |
| 리뷰 방식 | 3개 전문 관점 병렬 delegate + 정적 분석 + 직접 코드 검증 |
| 코드 규모 | 코어 Python ~12,000줄 / 프론트엔드 React+Tailwind ~2,000줄 / 테스트 27파일 |
| 정적 분석 | Bandit(High 1, Medium 0) / Radon(F등급 2건) / bare except 0건 |
| 테스트 | 301 passed / 7 failed (ML feature 20→27 확장 관련) |

### 이전 리뷰(A-1~A-8) 수정 완료 항목 (재지적 금지)

| ID | 이슈 | 수정 커밋 |
|----|------|-----------|
| A-1 | run_live() init_regime_config | aef29f6 |
| A-2 | session factory pattern | d0df11a |
| A-3 | DB engine integration | 823d339 |
| A-4 | backtest win rate FIFO | aef29f6 |
| A-5 | ConfirmModal | 823d339 |
| A-6 | 거래비용 검증 | d0df11a |
| A-7 | max_holdings GUI | aef29f6 |
| A-8 | backtest slippage | d0df11a |

---

## A. 시니어 백엔드 개발자 관점 (10년+)

### Strengths (강점)

1. **Adapter 패턴** — BrokerAdapter ABC + KIS 구현체, 비즈니스 로직에 KIS 필드명 누출 없음
2. **AST 강제 아키텍처 경계** — backtest/와 live/ 상호 import 금지, 파이프라인 원칙 준수
3. **의무적 의사결정 사유** — 모든 거래에 한국어 reason 필수, executor가 사유 없는 주문 거부
4. **토큰 보호** — KisToken.access_token field(repr=False), 평문 노출 차단
5. **모드별 리스크 회로차단** — 일일손실 halt, 포지션별 손절, max stop loss cap
6. **Repository 패턴** — 모든 DB 접근이 repository 클래스 경유
7. **bare except 0건, silent except 0건** — SAT3(177건) 대비 압도적 개선
8. **.env 보안** — gitignored, 환경변수로만 로드

### Weaknesses (약점)

#### [CRITICAL] BE-1: 주문 재시도에 멱등성 없음 — 중복 실거래 주문
- **파일**: live/executor.py:100-120
- **문제**: 3회 재시도 중 첫 제출이 KIS에 도달했으나 HTTP 응답만 타임아웃인 경우, 재시도가 동일 주문을 중복 제출
- **영향**: 실거래 중복 주문 → 포지션 2배, 실제 금전 손실
- **권고**: 주문 전 UUID 멱등키 생성, 재시도 전 KIS inquire_order로 기존 주문 확인

#### [CRITICAL] BE-2: 주문 체결 확인 부재 — 접수=체결로 가정
- **파일**: data/adapters/kis/adapter.py:268-317, live/executor.py:178-198
- **문제**: submit_order()가 ODNO(접수번호)를 반환하면 즉시 체결로 간주. order status inquiry 미구현. BrokerAdapter에 get_order_status() 없음
- **영향**: 시장가 주문 거절, 지정가 미체결, 부분체결 미감지 → 포지션 추적이 현실과 불일치 → 리스크 계산 오류, 없는 주식 매도 시도
- **권고**: get_order_status() 구현, submit 후 FILLED/REJECTED까지 폴링, 체결 후에만 DB 기록

#### [HIGH] BE-3: 매도 주문이 DB에 기록되지 않음
- **파일**: live/orchestrator.py:380-445
- **문제**: save_trade()가 매수에만 호출(line 350). 모든 매도 경로(손절/분봉청산/ML청산)는 sells_executed만 증가, trade_records 테이블에 기록 안 함
- **영향**: 매도 거래 이력 완전 유실, 대시보드 "거래내역"에 매수만 표시, DB 기반 손익 계산 불가, 포지션이 영원히 "open" 상태
- **권고**: 매도 성공 후 save_trade() + close_position() 호출

#### [HIGH] BE-4: ML 모델 무결성 해시 깨짐 + joblib RCE 위험
- **파일**: ml/training/trainer.py:377-430, 422
- **문제**: save()가 파일 덮고→해시 계산→해시를 파일에 다시 저장. load()는 해시가 포함된 파일의 해시를 계산 → 영원히 불일치. 주석으로 "정상 현상"으로 처리하고 검증 스킵. joblib.load는 pickle 기반 → 임의 코드 실행 가능
- **영향**: 변조된 모델 파일 = 원격 코드 실행(RCE), 무결성 검증 무력화
- **권고**: 별도 .sha256 사이드카 파일, 불일치 시 RuntimeError, skops.io 또는 ONNX 전환 검토

#### [HIGH] BE-5: 전역 가변 설정 상태 — 스레드 안전성
- **파일**: strategy/regime.py:50-57, screener.py:85-92, mode_selector.py:48-55
- **문제**: 3개 모듈이 module-level _strategy_config + global 패턴 사용. 대시보드 백테스트와 실거래가 동시 실행 시 서로의 설정 덮어쓰기
- **영향**: 백테스트 A 설정 → 백테스트 B 설정 덮어쓰기 → A가 B 설정으로 실행 → 잘못된 결과
- **권고**: StrategyConfig를 함수 파라미터로 전달, 전역 상태 제거

#### [HIGH] BE-6: 토큰 캐시 파일 평문 저장, 파일 권한 없음
- **파일**: data/adapters/kis/token_manager.py:70-82
- **문제**: access_token을 ~/.ngsat/kis_token_cache.json에 평문 JSON 저장, os.chmod(0o600) 없음
- **영향**: 시스템 모든 사용자가 KIS API 토큰 읽기 가능 → 거래 권한 탈취
- **권고**: 저장 후 os.chmod(0o600), 디렉토리 mode=0o700, keyring 라이브러리 검토

#### [HIGH] BE-7: CORS wildcard + credentials
- **파일**: dashboard/backend/api.py:134-140
- **문제**: allow_origins=["*"] + allow_credentials=True — 알려진 위험 패턴
- **권고**: 기본값을 localhost:5173/8000으로, 운영환경에서 명시적 NGSAT_CORS_ORIGINS 설정

#### [MEDIUM] BE-8: 매도 주문 취소 미구현
- **파일**: data/adapters/kis/adapter.py:319-325
- **문제**: cancel_order가 False 반환, KIS 취소 엔드포인트 미연결
- **권고**: ORD_GNO_BRNO 저장 후 KIS 취소 엔드포인트 구현

#### [MEDIUM] BE-9: 시장가 주문 amount=0 기록
- **파일**: live/executor.py:183, 254
- **문제**: price=None(시장가) 시 amount=0, DB에 0원으로 저장
- **권고**: 체결가 조회 후 실제 amount 계산

#### [MEDIUM] BE-10: 읽기 전용 DB 세션 미갱신
- **파일**: live/orchestrator.py:139-141
- **문제**: __init__에서 Session() 생성 후 영구 재사용, close/expire/refresh 없음
- **권고**: 요청별 컨텍스트 매니저 패턴 또는 scoped_session

#### [MEDIUM] BE-11: 대시보드 API 입력 검증 부재
- **파일**: dashboard/backend/api.py — control_forcesell, control_forcehold
- **문제**: code 필드에 임의 문자열 허용, 6자리 숫자 검증 없음
- **권고**: Pydantic Field(pattern=r"^\d{6}$") 추가

#### [MEDIUM] BE-12: 백테스트 전역 상태 락 없음
- **파일**: core/backtest_runner.py:22-30
- **문제**: _backtest_state check-then-set이 원자적이지 않음, 동시 백테스트 가능
- **권고**: asyncio.Lock 사용

#### [MEDIUM] BE-13: DB 엔진 전역 싱글톤 락 없음
- **파일**: data/db.py:29-49
- **문제**: get_engine()이 global _engine without lock, 다중 스레드 동시 호출 시 중복 엔진 가능
- **권고**: threading.Lock 또는 시작 시 1회 초기화

#### [LOW] BE-14: 토큰 매니저 silent except 3건
- **파일**: data/adapters/kis/token_manager.py:90, 106, 183
- **문제**: except Exception: pass — 로깅 없음
- **권고**: logger.warning 추가

#### [LOW] BE-15: trainer.py 776 LOC — 책임 과다
- **파일**: ml/training/trainer.py
- **문제**: 모델 생성/학습/평가/저장/자동튜닝/재학습/비교가 단일 클래스
- **권고**: ModelFactory, ModelTrainer, ModelPersistence, ModelEvaluator 분할

---

## B. 시니어 UI/UX 디자이너 관점 (10년+)

### Strengths (강점)

1. **StrategyConfigPanel 한국어 힌트 시스템** — 모든 필드에 "높을수록 신중하게 삽니다" 등 직관적 힌트
2. **프리셋 시스템** — 안정형/균형형/공격형 1클릭 적용
3. **PnL 색상 체계** — text-ngsat-green/red, utils.js 함수로 일관성 강제
4. **스켈레톤 로딩** — SkeletonCard/SkeletonTable/SkeletonChart
5. **Promise.allSettled** — 부분 실패 시 stale data 유지
6. **WebSocket + 5초 폴링 이중층** — 실시간 + 폴백
7. **ConfirmModal** — 위험 동작 확인, 한국어 결과 설명
8. **Lucide 아이콘** — 텍스트 라벨 보조, 일관된 크기
9. **EquityChart 스파크라인** — 색상 적응형, 컴팩트
10. **한국어 숫자 포맷** — Intl.NumberFormat('ko-KR'), tabular-nums

### Weaknesses (약점)

#### [CRITICAL] UI-1: 반응형 디자인 부재 — 모바일 사용 불가
- **파일**: App.jsx, Sidebar.jsx (w-60 고정)
- **문제**: 사이드바 240px 고정, 모바일 토글 없음, 768px 이하에서 레이아웃 붕괴
- **영향**: 대표님이 스마트폰에서 대시보드 확인 불가
- **권고**: 모바일 토글 + hamburger 버튼, md: 반응형 클래스 추가

#### [CRITICAL] UI-2: 접근성(ARIA) 0건 — 포커스 관리 없음
- **파일**: 전체 22개 파일 — aria-label, role 속성 0건
- **문제**: 토글 스위치에 role="switch" 없음, ConfirmModal에 포커스 트랩 없음, 아이콘 전용 버튼에 aria-label 없음
- **영향**: 시각 장애 대표님 사용 불가, 키보드 사용자 모달 이탈
- **권고**: role="switch" aria-checked, role="dialog" aria-modal, 포커스 트랩, aria-label 추가

#### [CRITICAL] UI-3: StrategyConfigPanel 저장 경고 없음
- **파일**: StrategyConfigPanel.jsx
- **문제**: 20+ 파라미터 수정 후 탭 이동 시 변경사항 소실, dirty state 추적 없음
- **영향**: 대표님이 5분간 조정한 설정이 탭 클릭 한 번에 사라짐
- **권고**: dirty state 추적, 탭 이동 시 확인 대화상자, "변경사항 있음" 배너

#### [HIGH] UI-4: WebSocket 재연결 로직 없음
- **파일**: App.jsx (ws.onclose)
- **문제**: 연결 종료 시 재연결 시도 없음, 5초 폴링은 유지되나 실시간 푸시 영구 중단
- **권고**: 재연결 exponential backoff (3s→6s→12s→max 60s), "재연결 중" 표시

#### [MEDIUM] UI-5: 백테스트 탭 헤더 제목 누락
- **파일**: App.jsx — h1 조건부 렌더링
- **문제**: backtest 탭에 대한 h1 조건 없음 → 빈 헤더
- **권고**: {activeTab === 'backtest' && '백테스트'} 추가

#### [MEDIUM] UI-6: 당일 손실 라벨/값 혼란
- **파일**: AccountCard.jsx
- **문제**: pnlColor(-account.daily_loss) — 손실 값에 부호 반전, "당일 손실: -500,000원" 이중 부호
- **권고**: 절대값 표시 또는 "당일 손익" 라벨 변경

#### [MEDIUM] UI-7: ConfirmModal ESC키 + 포커스 트랩 없음
- **파일**: ConfirmModal.jsx
- **문제**: ESC 키 핸들러 없음, 배경 요소로 포커스 이탈
- **권고**: ESC 핸들러, 포커스 트랩, role="dialog"

#### [MEDIUM] UI-8: Date picker 다크테마 미적용
- **파일**: StrategyConfigPanel.jsx
- **문제**: 네이티브 date picker 팝업이 밝은 테마, 캘린더 아이콘 다크 배경에서 거의 안 보임
- **권고**: color-scheme: dark, filter: invert(0.7)

#### [MEDIUM] UI-9: Toast 에러 타입 없음 + 3초 소멸
- **파일**: Toast.jsx, App.jsx
- **문제**: success/info만 지원, error 타입 없음, 3초 후 자동 소멸(에러 메시지 읽을 시간 부족)
- **권고**: error/warning 타입 추가, 에러 시 6초 연장

#### [MEDIUM] UI-10: DiagnosisPanel data.message 시 영구 스켈레톤
- **파일**: DiagnosisPanel.jsx
- **문제**: backend가 message 반환 시 SkeletonCard 영구 표시, loading=false인데도 스켈레톤 지속
- **권고**: data.message 시 메시지 카드 표시

#### [MEDIUM] UI-11: EvidenceBox 영문 키 표시
- **파일**: EvidenceBox.jsx
- **문제**: rsi_14 → "rsi 14", volume_ratio → "volume ratio" 등 영문 그대로
- **권고**: EVIDENCE_LABELS 한국어 맵 추가

#### [LOW] UI-12: 슬라이더 시각적 채우기 없음
- **파일**: StrategyConfigPanel.jsx
- **문제**: accent-color만 적용, 트랙 채우기 없음
- **권고**: linear-gradient 백그라운드 또는 스타일된 슬라이더

#### [LOW] UI-13: 백테스트 빈 상태 없음
- **파일**: BacktestPanel.jsx
- **문제**: 첫 실행 전 안내 없음
- **권고**: "백테스트를 실행하면..." 빈 상태 카드

---

## C. 한국 주식 자동매매 전문가 관점 (10년+)

### Strengths (강점)

1. **3단계 파이프라인** — Regime→Screener→ML 명확 분리
2. **모드별 리스크** — SWING/SHORT_TERM/HOLD 자동 전환, 모드별 손절/일일한도/포지션크기
3. **의사결정 사유 필수** — 모든 거래에 한국어 사유 + 정량 근거
4. **진입/청산 타이밍 가드** — surge guard(+3%/5bar), RSI 과열(>75), 급락 감지(-3%/5bar)
5. **ML 재학습 AUC 게이트** — 신규 AUC > 기존일 때만 교체, 성능 퇴보 방지
6. **백테스트 비용 현실성** — 0.26% 왕복 비용, 슬리피지 차등(0.1%/0.3%)
7. **주문 실행 복원력** — 지수 백오프 3회 재시도, 사유 검증, 리스크 halt 확인
8. **BEAR 모드 HOLD** — 약세장 신규 진입 금지, 보유 포지션 관리만

### Weaknesses (약점)

#### [CRITICAL] TR-1: ATR 포지션 사이징 100배 단위 오류
- **파일**: live/orchestrator.py:325
- **문제**: estimate_volatility_from_prices()가 std/mean*100으로 이미 백분율(예: 1.5=1.5%) 반환. orchestrator가 다시 *100 곱함 → vol_pct=150. 주석 "vol은 0~1 범위"가 오해
- **재정 위험**: vol=1.5% → vol_pct=150 → adjusted_pct = 0.10*(1.5/150) = 0.001 → min 0.3%로 clamp. 의도한 10% 투자가 0.3%로 축소. 10종목 × 0.3% = 3%만 투자 → 수익 잠재력 97% 손실. ATR 동적 사이징 완전 무력화
- **권고**: `vol_pct = max(vol, 0.5)`로 수정 (이미 백분율). 수정 후 포지션 크기 급증하므로 충분한 테스트 필요

#### [HIGH] TR-2: 레짐 가중치 합 105점 (100점이어야 함)
- **파일**: strategy/regime.py:60-65
- **문제**: ADX 추가(+5) 시 MA 35→30(-5), Volume 10→15(+5). 순변화 +5. 가중치 합 = 30+20+20+15+15+5 = 105
- **재정 위험**: 최대 점수 105 → 약 5% 인플레이션. 62/100점이 65/105점이 되어 BULL 진입. SWING 모드(공격적)로 전환 → 횡보장에서 과매수
- **권고**: Volume 가중치 15→10 복귀 (30+20+20+15+10+5=100), assert sum==100 추가

#### [HIGH] TR-3: ADX 가중치 환경설정 미반영 (하드코딩)
- **파일**: strategy/regime.py:157
- **문제**: 다른 가중치는 cfg.regime_weight_* 사용, ADX만 _WEIGHT_ADX 하드코딩. NGSAT_REGIME_WEIGHT_ADX env var 로드되나 사용 안 됨
- **영향**: 운영자가 ADX 가중치 조정해도 효과 없음
- **권고**: _WEIGHT_ADX → cfg.regime_weight_adx로 변경

#### [HIGH] TR-4: VI(변동성완화장치) 대응 부재
- **파일**: live/ 전체 — VI, 변동성완화, 회로차단 검색 0건
- **문제**: KOSPI ±5%, KOSDAQ ±10% 시 5분 거래 중단. 시스템이 VI 중 단 시장가 주문 제출 → 거절. 보유 포지션 손절 시 VI로 실행 불가 → 재개 후 훨씬 더 나쁜 가격
- **재정 위험**: VI 중 손절 불가 = 한국 알고리즘 매매 1위 손실 원인. -3% 손절이 -7%+에서 체결 가능
- **권고**: 주문 전 VI 상태 확인, VI 시 지정가 전환, Position에 vi_active 플래그

#### [HIGH] TR-5: 섹터 집중도 제한 부재
- **파일**: 전체 — sector/업종/섹터 검색 0건
- **문제**: max_holdings=10이나 같은 섹터 10종목 가능(반도체 5종목 등). 스크리너가 섹터 독립적으로 평가
- **재정 위험**: 섹터 전면 매도 시 10포지션 동시 손절 → 일일손실한도 초과
- **권고**: max_sector_concentration=3 추가, StockInfo에 sector 필드, 주문 전 섹터 카운트 확인

#### [HIGH] TR-6: 장 운영 시간 검증 부재
- **파일**: live/orchestrator.py — 09:00/15:30/market_hours 검색 없음
- **문제**: 장 외 시간에 주문 시도 → KIS 거절 → 재시도 낭비. KIS가 익일 접수로 처리 시 실제 없는 포지션으로 착각
- **권고**: is_market_hours() 유틸리티, _execute_buy/sell 게이트

#### [MEDIUM] TR-7: 포트폴리오 상관관소/분산 검사 없음
- **문제**: 삼성전자+SK하이닉스(상관 0.95) 동시 매수 가능. 분산 효과 없음
- **권고**: 매수 전 20일 수익률 상관관계 계산, max 0.7 초과 시 거부

#### [MEDIUM] TR-8: KOSPI/KOSDAQ 포트폴리오 비중 설정 미사용
- **파일**: core/config.py:88-89 — kospi_weight=0.7/kosdaq_weight=0.3 정의되나 live/strategy에서 참조 안 함
- **문제**: 10포지션 전부 KOSDAQ 가능. +5 KOSPI 보너스로 70/30 강제 불가
- **권고**: 포트폴리오 수준 할당 검사 추가

#### [MEDIUM] TR-9: ML 학습 label leakage — purge-embargo 없음
- **파일**: ml/training/trainer.py:~230-250
- **문제**: 80/20 분할 시 forward_days=3만큼 label이 test set과 겹침. AUC 0.684가 인플레이션 가능
- **영향**: 실제 out-of-sample AUC는 0.60-0.65일 수 있음 → 0.26% 비용 후 수익성 미진
- **권고**: embargo gap=forward_days, TimeSeriesSplit gap 설정

#### [MEDIUM] TR-10: 레짐 히스테리시스 없음 — 모드 플래핑
- **파일**: strategy/regime.py:160-169
- **문제**: 매 사이클 새로 점수 계산, hard threshold(≥65 BULL). 64↔66 오실레이션 시 NEUTRAL↔BULL 일일 전환
- **재정 위험**: 모드 전환마다 포지션 크기/손절 변경 → 잦은 재밸런싱 → 0.26% 비용 누적
- **권고**: 히스테리시스(BULL→NEUTRAL 60, BEAR→NEUTRAL 40), 최소 3일 연속 확인

#### [MEDIUM] TR-11: 백테스트 슬리피지 대칭 (유리한 방향 가능)
- **파일**: backtest/engine.py:174
- **문제**: slippage = price * slip_pct * (rng*2-1) — ±방향. 매수 시 더 싸게 체결 가능(현실과 반대)
- **영향**: ~50% 유리한 슬리피지 → 100거래당 ~5% 환상 수익
- **권고**: 매수는 항상 양수, 매도는 항상 음수 슬리피지

#### [MEDIUM] TR-12: 백테스트 포지션 사이징이 실거래와 불일치
- **파일**: backtest/engine.py:399-400
- **문제**: 백테스트는 고정 5%/10%, 실거래는 ATR 동적(현재 깨져있으나 의도는 동적)
- **영향**: 백테스트 결과가 실거래 수익률 과대평가
- **권고**: 백테스트에도 ATR 사이징 로직 적용

#### [LOW] TR-13: 일일 거래 횟수 제한 없음
- **문제**: 회전매매 시 0.26% 비용 누적, 10회/일 = 2.6% 비용
- **권고**: max_daily_trades=5 추가

#### [LOW] TR-14: 총 포지션 노출 한도 없음
- **문제**: 10×10%=100% 투자, 현금 버퍼 없음
- **권고**: max_total_exposure=0.80 (20% 현금 버퍼)

---

## D. 정적 분석 결과

### Bandit (보안 스캔)
| 심각도 | 건수 | 주요 항목 |
|--------|------|-----------|
| High | 1 | MD5 해시 (backtest/engine.py:171 — random seed용, 보안 아님) |
| Medium | 0 | — |
| Low | 8 | assert/binding 등 |

### Radon (복잡도)
| 등급 | 함수 | 비고 |
|------|------|------|
| F(57) | TradingOrchestrator.run_cycle | 개선 필요 |
| F(67) | BacktestEngine.run | 개선 필요 |
| D(24) | _calculate_metrics | — |
| D(30) | build_minute_features | — |
| D(21) | PriceRiseModel.train | — |
| E(32) | _evaluate_single_stock | — |
| C | 다수 | 양호 범위 |

### 예외 처리
- bare except: 0건
- silent except: 0건
- broad except: 0건 (단, orchestrator에 except Exception 8건 — NotImplementedError 분기 포함)

### 테스트
- 301 passed / 7 failed
- 실패 7건: ML feature count 20→27 확장 관련 테스트 미갱신 (5건), orchestrator Korean reason (1건), integration (1건)

---

## E. 우선순위별 요약

### CRITICAL (즉시 수정 필요)
| ID | 이슈 | 위험 |
|----|------|------|
| BE-1 | 주문 재시도 멱등성 없음 | 중복 실거래 주문 |
| BE-2 | 주문 체결 확인 부재 | 팬텀 포지션, 손익 불일치 |
| TR-1 | ATR 사이징 100배 오류 | 포지션 0.3%→의도 10%, 수익 잠재력 97% 손실 |
| UI-1 | 반응형 디자인 부재 | 모바일 사용 불가 |
| UI-2 | 접근성 ARIA 0건 | 시각장애 사용 불가 |
| UI-3 | StrategyConfig 저장 경고 없음 | 설정 변경사항 탭 이동 시 소실 |

### HIGH (운영 전 수정 권고)
| ID | 이슈 | 위험 |
|----|------|------|
| BE-3 | 매도 주문 DB 미기록 | 매도 이력 완전 유실 |
| BE-4 | ML 무결성 해시 깨짐 + RCE | 변조 시 원격 코드 실행 |
| BE-5 | 전역 가변 설정 상태 | 동시 실행 시 설정 덮어쓰기 |
| BE-6 | 토큰 캐시 평문 + 권한 없음 | 토큰 탈취 |
| BE-7 | CORS wildcard + credentials | 크로스오리진 공격 |
| TR-2 | 레짐 가중치 합 105 | BULL 편향 → 과매수 |
| TR-3 | ADX 가중치 하드코딩 | env 설정 무효 |
| TR-4 | VI 대응 부재 | 손절 불가 → 비제어 손실 |
| TR-5 | 섹터 집중도 제한 없음 | 섹터 쇼크 시 다중 손절 |
| TR-6 | 장시간 검증 없음 | 장외 주문 시도 |
| UI-4 | WebSocket 재연결 없음 | 실시간 푸시 영구 중단 |

### MEDIUM (계획적 수정)
| ID | 이슈 | 위험 |
|----|------|------|
| BE-8 | 매도 주문 취소 미구현 | 대기 주문 취소 불가 |
| BE-9 | 시장가 amount=0 | 거래 기록 부정확 |
| BE-10 | 읽기 전용 세션 미갱신 | stale data |
| BE-11 | API 입력 검증 부재 | 잘못된 입력 |
| BE-12 | 백테스트 상태 락 없음 | 동시 실행 |
| BE-13 | DB 엔진 락 없음 | 중복 엔진 |
| TR-7 | 상관관계 검사 없음 | 의사 분산 |
| TR-8 | KOSPI/KOSDAQ 비중 미사용 | KOSDAQ 과노출 |
| TR-9 | ML label leakage | AUC 인플레이션 |
| TR-10 | 레짐 히스테리시스 없음 | 모드 플래핑 |
| TR-11 | 슬리피지 대칭 | 환상 수익 |
| TR-12 | 백테스트-실거래 사이징 불일치 | 과대평가 |
| UI-5 | 백테스트 헤더 누락 | 빈 헤더 |
| UI-6 | 당일 손실 라벨 혼란 | 이중 부호 |
| UI-7 | ConfirmModal ESC 없음 | 포커스 이탈 |
| UI-8 | Date picker 다크테마 | 밝은 팝업 |
| UI-9 | Toast 에러 타입 없음 | 에러 3초 소멸 |
| UI-10 | DiagnosisPanel 영구 스켈레톤 | 빈 페이지 |
| UI-11 | EvidenceBox 영문 키 | 기술 용어 노출 |

### LOW (개선 권고)
| ID | 이슈 | 위험 |
|----|------|------|
| BE-14 | 토큰 매니저 silent except | 로깅 부재 |
| BE-15 | trainer.py 776 LOC | 책임 과다 |
| TR-13 | 일일 거래 횟수 제한 없음 | 비용 누적 |
| TR-14 | 총 노출 한도 없음 | 100% 투자 |
| UI-12 | 슬라이더 시각적 채우기 없음 | 직관성 저하 |
| UI-13 | 백테스트 빈 상태 없음 | 안내 부족 |

---

## F. SAT3 대비 개선 사항 (긍정적 변화)

| 항목 | SAT3 | NGSAT |
|------|------|-------|
| bare except | 177건 | 0건 |
| async blocking I/O | 41/43 라우트 | 0건 (전부 async/await) |
| 영문 코드 잔존 | 20건 | 0건 |
| PnL CSS 미정의 | positive/negative 클래스 없음 | text-ngsat-green/red 정의 |
| 새로고침 주기 | 10초 (요구 5초 위반) | 5초 + WebSocket |
| Global mutable state | _runtime_* 다수 | main.py에 없음 |
| God module | main.py 2,255 LOC | 최대 776 LOC (trainer) |
| 백테스트 비용 | 미구현 | 0.26% 왕복 |
| ML 모델 | 없음 | 5종 비교, AUC 게이트 재학습 |
| 모드별 리스크 | 없음 | SWING/SHORT_TERM/HOLD |

---

## G. Opportunities (개선 기회)

1. **주문 체결 확인** — get_order_status() 구현으로 신뢰성 확보
2. **ONNX/skops 전환** — pickle RCE 위험 제거
3. **섹터 분산** — 한국 시장 섹터 동조화 대응
4. **VI 대응** — 한국 시장 특화 회로차단 처리
5. **ML label embargo** — 진정한 out-of-sample 성능 측정
6. **레짐 히스테리시스** — 모드 플래핑 방지

## H. Threats (외부 위협)

1. **ML AUC 0.684의 한계** — 0.26% 비용 후 마진 얇음, rolling AUC 모니터링 필요
2. **KIS API 레이트 리미트** — 폭락 시 다수 동시 호출 → throttling
3. **KOSDAQ 유동성** — 스트레스 시 스프레드 확대
4. **세금 변동** — 증권거래세 인하 만료 시 비용 구조 변화

---

*본 리뷰는 3개 전문 관점(백엔드/디자이너/트레이더) 병렬 delegate + 정적 분석 + 직접 코드 검증으로 수행되었습니다. 이전 리뷰 A-1~A-8 수정 완료 항목은 재지적하지 않았습니다.*
