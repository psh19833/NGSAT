# NGSAT (New Generation Stock Auto Trader)

> 한국 주식 자동매매 시스템 — 2026.07.01 기준, 53개 개선 항목 반영 완료

## 한 줄 요약

KIS API로 실시간 시세를 받아, 시장 상황(레짐)을 판단하고, 기술지표 + ML로 종목을 골라 자동으로 매매합니다. 스윙(며칠 보유)과 단타(당일치기)를 상황에 따라 자동 전환합니다.

---

## 핵심 특징

- **3단계 매매**: 시장 판단 → 종목 선별 → ML 예측 → 자동 매매
- **실거래/백테스트 완전 분리**: 서로 import 금지, 혼선 원천 차단
- **모든 거래에 근거 필수**: "왜 샀는지"를 항상 기록
- **KIS 증권사 연동**: 한국투자증권 OpenAPI
- **대시보드 + 텔레그램**: 웹 UI + 모바일 알림

---

## 적용된 개선 사항 (총 53건)

### Phase 1 — 핵심 버그 수정 (10건, 6/28)
모델 오류·백테스트 부정확성·보안 등 실거래 안전성 직결 항목

| 항목 | 내용 |
|------|------|
| ✅ auto_tune() 모델 복구 | dict 할당 버그 수정, `fit()` 정상 호출 |
| ✅ run_live() 설정 주입 | 레짐·스크리너·모드설정 실거래 반영 |
| ✅ 백테스트 거래 비용 | 수수료 0.015% + 농특세 0.23% 반영 |
| ✅ 백테스트 일일 손실 | 최대낙폭→전일대비 당일손실로 수정 |
| ✅ 백테스트 승률 | FIFO 큐 도입으로 재매수 시 오류 수정 |
| ✅ DB 세션 안전성 | sessionmaker 팩토리 패턴 적용 |
| ✅ refresh_prices() | 실시간 시세 갱신 루프 연결 |
| ✅ 합성 데이터 차단 | 2중 방어 (orchestrator + repository) |
| ✅ CORS 보안 | 환경변수 기반 제한 |
| ✅ 레짐 설정 동적 적용 | 대시보드 변경값 실거래 반영 |

### Phase A — 실거래 안전성 (5건)
| 항목 | 내용 |
|------|------|
| ✅ KIS API Rate Limit 대응 | refresh 10초→5분 주기로 조정 |
| ✅ auto_tune Scaler 일관성 | 표준화 파이프라인 유지 |
| ✅ .env 파일 보호 | 런타임 변조 제거 |
| ✅ _last_auc 초기화 | 재학습 조건 안정화 |
| ✅ 대시보드 폴링 개선 | `Promise.allSettled` 적용 |

### Phase B — 기능 개선 (8건)
| 항목 | 내용 |
|------|------|
| ✅ 미체결 주문 재시도 | 지수 백오프 3회 자동 재시도 |
| ✅ ATR 기반 포지션 사이징 | 변동성 높으면 축소, 낮으면 확대 |
| ✅ WebSocket 실시간 푸시 | 거래 체결·상태 변경 브로드캐스트 |
| ✅ pyproject.toml + pre-commit | 코드 품질 표준화 |
| ✅ Tailwind 정적 매핑 | 동적 클래스→COLOR_MAP |
| ✅ Lucide 아이콘 통일 | 유니코드→SVG 아이콘 |
| ✅ save_minute_bars 예외 처리 | `IntegrityError`만 캐치 |
| ✅ pnlColor 부호 검증 | 일관성 확인 완료 |

### Phase C — 사용자 경험 + 인프라 (10건)
| 항목 | 내용 |
|------|------|
| ✅ GitHub Actions (reviewdog) | PR마다 flake8+bandit+autoflake 자동 |
| ✅ Skeleton 로딩 | "불러오는 중..."→스켈레톤 UI |
| ✅ Evidence 시각화 | Raw JSON→키-값 리스트 |
| ✅ Pagination | 거래내역 페이지 이동 |
| ✅ Equity Curve 차트 | 자산 추이 sparkline (recharts) |
| ✅ 백테스트 슬리피지 | ±0.1~0.3% 체결 가격 변동 |
| ✅ 장애복구 | 재시작 시 포지션 동기화 |
| ✅ KIS WebSocket 실시간 시세 | REST polling 대체 (Rate Limit 해결) |
| ✅ ConfigService DB 구축 | .env 대체 영구 설정 저장 |
| ✅ ML 피처 확장 | 외국인/기관 수급 + PER/PBR/EPS |

### Phase D — 추가 버그 수정 및 개선 (12건, 6/30)

### Phase E — 추가 기능 및 안정화 (8건, 7/1)
| 항목 | 내용 |
|------|------|
| ✅ 코드리뷰 7건 수정 | falsy 함정, raise None, circuit breaker, dotenv 경고, 중복 import, KST 통일, 벌크 삽입 |
| ✅ 텔레그램 장 시작/종료 알림 | MarketSessionTracker로 상태 변화 감지 → 자동 알림 |
| ✅ 텔레그램 일일 보고서 | 장 마감 후 TradeRepository 조회 → 자동 전송 |
| ✅ 재학습 embargo 제거 | 1시간 제한 삭제, 버튼 누르면 즉시 실행 |
| ✅ AUC 실제 값 출력 | single/multi_model_retrain이 실제 계산값 반환 |
| ✅ xgboost + lightgbm 설치 완료 | 5개 모델 전부 정상 동작 확인 |
| ✅ 설정 프리셋 8종 | config/presets.json 파일 기반, API + 프론트 동적 로드 |
| ✅ 포트 단일화 | Vite build → dist → port 8000만 사용 |

### Phase D — 추가 버그 수정 및 개선 (12건, 6/30)
| 항목 | 내용 |
|------|------|
| ✅ 레짐 평가 장시간 버그 | `is_market_hours()` 체크 추가, `regime_skipped` 상태 |
| ✅ KOSPI 일봉 TR_ID 오류 | FHPUP02110000→FHKUP03500100 (합성지수 버그 해결) |
| ✅ 장중 레짐 보정 (B안) | KOSPI 등락률로 ±5점 보정 |
| ✅ KOSPI 현재가 파싱 수정 | `bstp_nmix_*` 지수 필드 fallback 추가 |
| ✅ WebSocket ping_interval 제거 | KIS 서버 ping/pong 불일치 해결 |
| ✅ 캐시 키 타입 충돌 수정 | get_positions()→AccountSummary 반환 버그 롤백 |
| ✅ 포지션 수익률 직접 계산 | KIS 미제공 필드를 profit_loss/buy_amount로 계산 |
| ✅ 잔고조회 캐시 TTL 1.5→5초 | Rate limit (EGW00215) 방지 |
| ✅ API 호출 간격 50→100ms | KIS 공식 샘플과 동일 (smart_sleep) |
| ✅ KOSPI 지수 데이터 오름차순 정렬 | parse_index_history() 정렬 추가 |
| ✅ 백테스트 에러 핸들링 | api.py try/except + 프론트 error 분기 |
| ✅ 레짐 가중치 합계 정상화 | VOL 15→10 (합계 100) |

### 추가 도구 설치
- reviewdog · autoflake · bandit · radon · pip-audit · pre-commit

---

## 실거래 현황

- **KIS 연결**: 정상
- **ML 모델**: Random Forest, 27개 피처, KIS 실데이터 학습 완료
- **자동 재학습**: 활성화
- **대시보드**: 포트 8000

---

## 시작하기

```bash
# 1. 환경 설정
cp .env.example .env  # KIS API 키, 텔레그램 토큰 입력

# 2. 실행
python main.py                     # 실거래 시작 (포트 8000)
python main.py --train             # ML 모델 학습
python main.py --tick-interval=10  # 10초 주기 매매
```

---

## 문서

| 문서 | 내용 |
|------|------|
| [기획서](docs/00-기획서.md) | 프로젝트 개요 |
| [기술 스택](docs/01-기술스택선정.md) | 기술 선정 사유 |
| [패키지 구조](docs/02-패키지구조설계.md) | 코드 구조 설계 |
| [KIS API 연동](docs/03-KIS_API_연동테스트결과.md) | 증권사 API 테스트 |
| [하이브리드 매매](docs/04-하이브리드매매-설계.md) | 스윙/단타 자동 전환 설계 |
| [실행계획서](docs/09-전체코드리뷰-실행계획서.md) | 3개 관점 리뷰 기반 계획 |
| [리스크 진단](docs/10-리스크진단-수정보고서.md) | 보안·복잡도·의존성 진단 |
| [Phase A~C 계획](docs/11-종합리스크-실행계획서.md) | 단계별 리스크 검토 |

---

## 기술 스택

- **언어**: Python 3.12+
- **백엔드**: FastAPI + SQLAlchemy
- **DB**: SQLite (로컬) / PostgreSQL (확장 시)
- **프론트엔드**: React + Vite + TailwindCSS (다크 테마)
- **ML**: scikit-learn → XGBoost/LightGBM (Optuna 튜닝)
- **실시간**: KIS WebSocket + Dashboard WebSocket
- **알림**: Telegram Bot
- **패키지**: uv
- **CI**: GitHub Actions (reviewdog)
- **품질**: flake8 · pylint · bandit · radon · pre-commit

## 라이선스

Private
