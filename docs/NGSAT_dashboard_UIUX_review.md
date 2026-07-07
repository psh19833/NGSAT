# NGSAT 대시보드 — 시니어 UI/UX 디자인 리뷰 보고서

> **리뷰 일자**: 2026-07-07  
> **리뷰 범위**: dashboard/frontend/src/ 전역 (24개 파일, ~3,500줄)  
> **분석 기준**: 정보 계층, 일관성, 접근성, 반응형, 상태 표시, 데이터 시각화, 사용자 피드백, 성능, TailwindCSS, React 패턴

---

## S — 강점 (Strengths)

### S1. 견고한 다크테마 디자인 시스템
`tailwind.config.js`에 10개의 ngsat-* 커스텀 색상, 2개의 폰트 패밀리(Inter + JetBrains Mono)가 일관되게 정의되어 있다. 모든 컴포넌트가 이 토큰을 사용하므로 색상 일관성이 매우 높다. `ngsat-card` 유틸리티 클래스(`bg-ngsat-card border border-ngsat-border rounded-xl`)로 모든 카드의 시각적 일관성이 확보되어 있다.

### S2. 한국어 UX 우선 설계
모든 레이블, 버튼 텍스트, 툴팁, 에러 메시지가 한글이다. `LABEL_MAP` 패턴(EvidenceBox), `TABS` 배열(Sidebar), `stateLabel`/`regimeLabel`(utils.js) 등 서버 상태를 한글로 매핑하는 체계가 잘 갖춰져 있다. 외국인 개발자가 아닌 **한국 사용자**를 대상으로 한 설계가 명확히 드러난다.

### S3. 우수한 에러 복원력
- `Promise.allSettled`로 7개 API 호출 중 일부 실패해도 나머지 데이터는 계속 표시
- 모든 API 에러를 `{ error, connected: false }`로 정규화 — 컴포넌트는 `connected === false` 하나만 검사하면 됨
- `ErrorBoundary`로 전체 앱 크래시 방지, 재시작 버튼 제공
- WebSocket 대신 Polling 기반이지만, 자동 재연결 로직(App.jsx handleRestart)이 30초 타임아웃으로 서버 복구 대기

### S4. 세심한 UX 디테일
- **ConfirmModal**: Escape 키 + 백드롭 클릭으로 닫기, 포커스 트랩(Tab/Shift+Tab), `aria-labelledby`로 스크린리더 지원
- **Toggle 스위치**: `role="switch"` + `aria-checked` — 접근성 모범 사례 준수
- **Toast**: `role="alert"` + `aria-live="polite"` — 동적 알림 접근성 지원
- **StrategyDirty 배너**(App.jsx 208-212): 저장하지 않은 변경사항 시각적 경고
- **SkeletonCard**: BacktestPanel, DiagnosisPanel에서 로딩 상태 표현 — `animate-pulse`로 부드러운 로딩 애니메이션

### S5. 레짐 가중치 자동 리밸런싱
`rebalanceWeights()` 함수(StrategyConfigPanel.jsx 242-291)는 사용자가 하나의 시장 점수 가중치를 변경하면 나머지 가중치를 비례적으로 재분배하여 합계를 항상 100으로 유지한다. 2-pass 알고리즘으로 반올림 오차까지 보정한다. 금융 도메인에서 매우 중요한 **합계 제약 조건**을 UI 레벨에서 자동 처리한 점은 높이 평가할 만하다.

### S6. 데이터 시각화 기본기는 충실
- `EquityChart`(Recharts): 자본금 추세를 라인+영역 그래프로 표현, 수익/손실에 따라 색상 변경
- `RegimeCard`: 스코어바(0-100)를 색상 코딩된 진행바로 시각화
- `ScoreBar`(DiagnosisPanel): 개별 지표를 0-100% 막대로 정규화하여 직관적 비교 가능
- `BacktestPanel`: 진행바로 백테스트 실행 상태 표시
- 지수 헤더 미니 스트립(App.jsx 176-192): 모든 화면에서 지수 등락률을 항상 확인 가능

---

## W — 약점 (Weaknesses)

### 🔴 CRITICAL W1. 불필요한 5초 간격 전량 갱신 (성능 + 네트워크 낭비)
**파일**: `App.jsx` 69-73  
**문제**: `refreshAll()`이 5초마다 **7개 API를 전부 호출**한다. 사용자가 '진단 현황'이나 '백테스트' 탭에 있어도 운영 요약 데이터(계좌, 포지션, 레짐 등)를 계속 받아온다.  
**영향**:
- 백엔드에 불필요한 7건/5초 = 84건/분 API 호출 부하
- 모바일 환경에서 데이터 사용량 낭비
- 활성 탭과 무관한 상태 업데이트로 불필요한 리렌더링 유발  
**해결 방안**: 활성 탭에 따라 필요한 API만 선택적으로 Polling하거나, WebSocket으로 전환

### 🔴 CRITICAL W2. 제어 동작 에러 처리 누락
**파일**: `App.jsx` 75-85  
**문제**: `handleControl()`에서 `api.start()`, `api.stop()`, `api.forceHold()` 호출 결과를 전혀 확인하지 않는다. API 호출이 실패해도 사용자에게 알림이 가지 않는다.  
**영향**: "매매 시작" 버튼을 눌렀는데 실제로 시작되지 않았어도 사용자는 계속 "실행 중"으로 오인  
**증상 코드**:
```jsx
const handleControl = async (action, code) => {
  // ... confirm 처리
  if (action === 'start') await api.start()  // ← 실패해도 조용함
  // ...
  await refreshAll()  // 실패 후에도 refresh 실행
}
```

### 🟡 HIGH W3. 연결 상태 시각적 표시 미흡
**파일**: `App.jsx` 165-168  
**문제**: 백엔드 연결 상태를 헤더 우측에 `w-2 h-2`(8px × 8px) 점 하나와 "연결됨"/"미연결" 텍스트로만 표시한다. 트레이딩 시스템에서 **연결 끊김은 가장 치명적인 상태**인데, 시각적 계층에서 너무 낮은 우선순위를 갖는다.  
**영향**: 헤더가 복잡할 때(지수 미니 스트립 + 서버 시간 + 새로고침 버튼) 연결 상태를 알아차리기 어렵다. Sidebar에도 동일한 표시가 있지만 아래쪽에 위치(hidden on mobile sidebar)하여 더 눈에 띄지 않는다.  
**해결 방안**: 연결 끊김 시 상단에 고정 배너를 표시하거나, 상태 표시를 더 크게/색상 차별화

### 🟡 HIGH W4. 탭 기반 코드 스플리팅 미적용
**파일**: `main.jsx` (진입점)  
**문제**: 모든 컴포넌트가 하나의 번들로 로드된다. `React.lazy()` + `Suspense`가 전혀 사용되지 않았다.  
**영향**:
- 백테스트, 진단, 전략 설정 등 무거운 컴포넌트가 초기 로딩에 포함됨
- 초기 번들 크기 불필요하게 증가
- 실제로 사용하지 않는 기능(백테스트 등)의 코드도 항상 메모리에 상주

### 🟡 HIGH W5. focus 관리 — 탭 전환 시 메인 콘텐츠로 이동 없음
**파일**: `App.jsx`, `Sidebar.jsx`  
**문제**: 사이드바에서 탭을 전환해도 포커스가 메인 콘텐츠 영역으로 이동하지 않는다. 키보드 사용자는 탭 전환 후 다시 Tab을 10회 이상 눌러야 콘텐츠에 도달할 수 있다.  
**영향**: 키보드/스크린리더 사용자 경험 저하  
**Sidebar.jsx** 103: `aria-current={isActive ? 'page' : undefined}` — `'page'`가 아닌 `true` 또는 `"page"`(문자열)가 표준값. 하지만 버튼에는 `aria-current="page"`보다 `aria-current="true"`가 적절하다 (`aria-current`는 `a` 태그에 더 적합).

### 🟡 HIGH W6. TradesTable 이중 데이터 소스 패턴
**파일**: `TradesTable.jsx` 210-234  
**문제**: `propTrades`(부모로부터)와 `localData`(자체 api 호출) 두 가지 데이터 소스를 동시에 관리한다. 어느 쪽이 우선인지 추적하기 어렵고, `api` prop이 있을 때만 `fetch`하는 조건부 로직이 복잡성을 높인다.  
**영향**: 유지보수 어려움. 버그 발생 시 어느 소스의 데이터인지 디버깅이 까다롭다.

### 🟡 HIGH W7. StrategyConfigPanel — 메타 프롭스를 데이터에 혼합
**파일**: `StrategyConfigPanel.jsx` 792-799  
**문제**: `configWithMeta` 객체에 `_modelInfo`, `_retraining`, `_retrainMsg`, `_onRetrain`, `_adjustMsg` 등 UI 상태와 이벤트 핸들러를 config 데이터와 함께 전달한다. 언더스코어 접두사로 "메타"임을 표시했지만, 데이터와 UI 상태의 명확한 분리 원칙에 위배된다.  
**영향**:
- `_onRetrain`(함수)이 데이터 객체에 포함되어 직렬화 시 문제 가능성
- Component가 "이 props는 config의 일부인가?" 혼란
- 타입스크립트 도입 시 any 타입 양산

### 🔵 MEDIUM W8. 색상 대비 — muted 텍스트 가독성 우려
**파일**: `tailwind.config.js`  
**문제**: `ngsat-muted: '#8b8e98'`를 `ngsat-card: '#1a1d27'` 배경 위에 사용한다. (WebAIM 대비 계산기 기준 약 4.2:1 — WCAG AA(4.5:1)에 미달 가능성)  
**영향**: 작은 폰트(12-14px)로 표시되는 muted 텍스트(설명, 힌트, 부가 정보)를 읽기 어려울 수 있음  
**확인 필요**: 실제 디스플레이 캘리브레이션에 따라 다를 수 있으나, 안전하게 #a0a3ad 이상 밝기로 조정 권장

### 🔵 MEDIUM W9. 카드별 로딩 스켈레톤 부재
**파일**: `StatusCard.jsx`, `RegimeCard.jsx`, `AccountCard.jsx`, `IndicesCard.jsx` 등  
**문제**: 초기 로딩 시 `SkeletonCard` 대신 단순히 "—"(대시)만 표시한다. 데이터가 없는 것인지, 로딩 중인 것인지 구분이 안 된다.  
**영향**: 초기 진입 시 깜빡임(flash of unstyled content) 발생 가능  
**해결 방안**: 각 카드에 `loading` prop을 받아 `SkeletonCard`를 표시하는 패턴 도입

### 🔵 MEDIUM W10. index.css 하드코딩 색상
**파일**: `index.css`  
**문제**: 여러 CSS 규칙이 Tailwind 테마 변수 대신 하드코딩된 16진수 색상을 사용한다:
```css
background: #0f1117;   /* ngsat-bg */
background: #1a1d27;   /* ngsat-card */
background: #2a2d3a;   /* ngsat-border */
background: #6c63ff;   /* accent (≠ ngsat-accent #3b82f6!) */
```
`#6c63ff`는 `ngsat-accent: #3b82f6`와 **다른 값**이다. range slider의 accent 색상이 실제 accent와 불일치한다.  
**영향**: 테마 변경 시 index.css도 따로 수정해야 함. accent 색상 불일치.

### 🔵 MEDIUM W11. StrategyConfigPanel — setTimeout 기반 재시작 경쟁 상태
**파일**: `StrategyConfigPanel.jsx` 746-751  
**문제**: 설정 저장 후 `restart_required` 플래그가 true면 500ms 후 `api.restart()`를 호출한다. 하지만 사용자가 저장 직후 다른 작업을 하면 restart 호출이 예상치 못한 시점에 발생할 수 있다.  
```jsx
setTimeout(async () => {
  await api.restart()
  setMessage(m => m + ' 완료')
}, 500)
```
**영향**:
- 500ms는 서버가 저장을 완료했는지 보장하지 않음
- 컴포넌트가 언마운트되어도 setTimeout 실행 (메모리 누출 + 고아 콜백)
- `setMessage(m => ...)`는 함수형 업데이트지만, 컴포넌트 언마운트 후 경고 발생

### 🔵 MEDIUM W12. formatNumber 정밀도 손실
**파일**: `utils.js` 3-6  
**문제**: `Math.round(n)`으로 숫자를 반올림하여 주식 가격(소수점 2자리) 정밀도가 손실된다. 특히 코스피/KOSDAQ 지수는 소수점 한 자리까지 의미가 있다.  
```jsx
export function formatNumber(n) {
  return new Intl.NumberFormat('ko-KR').format(Math.round(n))
}
```
**영향**: 지수 가격, 주식 가격 등 소수점 정보가 사라짐  
**해결 방안**: `maximumFractionDigits: 1` 옵션 추가

### 🔵 MEDIUM W13. 헤더 지수 미니스트립 — 모바일 오버플로우 위험
**파일**: `App.jsx` 176-192  
**문제**: 헤더 우측에 KOSPI, S&P, NASDAQ 3개 지수를 `text-[11px]`로 표시한다. 모바일 화면(375px)에서 서버 시간 + 연결 상태 + 새로고침 버튼과 함께 배치되면 텍스트가 겹치거나 잘릴 위험이 있다.  
**영향**: 모바일에서 중요한 지수 정보를 읽을 수 없게 될 수 있음

### 🔵 MEDIUM W14. window.confirm/alert 사용
**파일**: `StrategyConfigPanel.jsx` 409, 432, 436, 484, 759  
**문제**: 시스템 alert/confirm은 일관된 UI를 제공하지 못하고, 사용자 경험을 해친다. 특히 '프리셋 적용' 성공 메시지를 alert로 표시하는 것은 데이터가 많은 경우 UX가 매우 나쁘다.  
```jsx
alert(`✅ "${name}" 적용 완료\n변경: ${data.applied}개 항목\n...`)
```
**영향**:
- 브라우저 기본 UI로 테마/디자인 불일치
- alert는 사용자 액션을 강제로 차단(modal)하지만, Toast로 충분한 정보까지 alert 사용
- 모바일에서 alert UX 매우 나쁨

### 🔵 MEDIUM W15. EvidenceBox 10개 항목 제한
**파일**: `EvidenceBox.jsx` 24  
**문제**: 근거 항목이 10개를 초과하면 나머지는 숨기고 "외 N개 항목"만 표시한다. 사용자가 더 많은 항목을 볼 방법이 없다.  
**영향**: 중요한 근거 정보를 사용자가 확인하지 못할 가능성  
**해결 방안**: "더 보기" 버튼을 추가하거나, `slice` 제한을 없애고 스크롤로 처리

### 🟢 LOW W16. Pagination — aria-current 누락
**파일**: `Pagination.jsx` 34-46  
**문제**: 현재 페이지 버튼에 `aria-current="page"`가 없다. 스크린리더 사용자가 현재 위치를 인식하기 어렵다.  
**해결 방안**: 현재 페이지 버튼에 `aria-current="page"` 속성 추가

### 🟢 LOW W17. 불필요한 React import
**파일**: `EvidenceBox.jsx` 외 다수  
**문제**: React 18+에서는 JSX 변환에 `import React`가 필요하지 않지만, `EvidenceBox.jsx`에서 `React`가 import되어 있지 않음 (불필요한 import가 없다는 의미로 양호). 다만 일부 파일에서 사용하지 않는 import 가능성.

### 🟢 LOW W18. EquityChart 빈 데이터(vs 최소 데이터 포인트)
**파일**: `EquityChart.jsx` 4  
**문제**: `data.length < 2`면 아무것도 렌더링하지 않는다(return null). 자산 1개 포인트만 있어도 차트 영역이 사라져 UI 레이아웃이 깨질 수 있다.  
**영향**: AccountCard에서 equity_history가 1개 항목만 있으면 차트 영역이 빈 공간으로 표시됨

### 🟢 LOW W19. mjs 확장자 혼용
**파일**: `vite.config.js`  
**문제**: `tailwind.config.js`는 CommonJS(ESM `export default` 사용)와 ESM을 혼용(`/** @type {import('tailwindcss').Config} */` JSDoc 주석). `vite.config.js`는 순수 ESM. 일관성이 부족하지만 기능상 문제는 없음.

---

## O — 개선 기회 (Opportunities)

### O1. WebSocket 기반 실시간 데이터로 전환
현재 5초 Polling 방식은 불필요한 트래픽이 많고 실시간성이 떨어진다. Vite proxy에 이미 `/ws` WebSocket 프록시가 설정되어 있으므로(vite.config.js 13-16), 백엔드 WebSocket 엔드포인트와 연계하여 실시간 푸시 기반으로 전환하면:
- API 서버 부하 90% 이상 감소
- 더 빠른 상태 업데이트
- 배터리/데이터 절약 (모바일)

### O2. 레이아웃 — 대시보드 정보 계층 재설계
현재 overview 탭은 StatusCard + RegimeCard + ControlPanel이 3열 그리드로 동등한 비중이다. 트레이딩 대시보드의 정보 계층을 재설계하면:
- **1순위(항상 상단)**: 연결 상태 + 계좌 요약(총 자산, 평가손익)
- **2순위**: 서버 상태(운영 중/중단) + 시장 레짐
- **3순위**: 포지션, 전략 요약, 제어
- 현재는 ControlPanel이 운영 요약 최상단에 있어 정보 계층이 다소 평평함

### O3. 코드 스플리팅 + 레이지 로딩
```
React.lazy(() => import('./components/BacktestPanel.jsx'))
React.lazy(() => import('./components/DiagnosisPanel.jsx'))
React.lazy(() => import('./components/StrategyConfigPanel.jsx'))
```
초기 번들 크기를 30-40% 줄일 수 있음. 특히 StrategyConfigPanel(885줄, 38KB)이 가장 큰 대상.

### O4. React.memo + useMemo 최적화
PositionTable, TradesTable 등 자주 갱신되는 리스트 컴포넌트에 `React.memo` 적용. 현재는 모든 5초 갱신마다 전체 트리 리렌더링 발생.

### O5. Storybook / 컴포넌트 카탈로그
24개 컴포넌트 중 상당수(StatusCard, RegimeCard, Toast 등)는 상태에 따른 다양한 변형이 존재. Storybook을 도입하면 시각적 회귀 테스트와 디자인 시스템 문서화에 유리함.

### O6. 접근성 종합 감사
- `main` 영역에 `role="main"` 또는 `<main>` 태그 적절히 사용
- Skip-to-content 링크 추가
- Tab 순서 검증 (사이드바 → 헤더 → 메인 콘텐츠)
- 모든 인터랙티브 요소에 포커스 스타일 검증 (현재 outline-none만 있고 대체 스타일 확인 필요)

### O7. StrategyConfigPanel 리팩터링
- 메타 프롭스를 별도 props 객체로 분리
- 재시작 타이머를 `useRef`로 관리하고 useEffect cleanup 추가
- 크기가 큰 SECTIONS 선언을 별도 파일로 분리
- 프리셋 버튼의 `confirm()`/`alert()`를 Toast + ConfirmModal로 대체

---

## T — 위협 (Threats)

### T1. 확장성 — 885줄 StrategyConfigPanel
현재 단일 파일 885줄이다. 전략 옵션이 추가될 때마다 이 파일이 더 비대해진다. SECTIONS 선언, FieldRow, CollapsibleSection, PresetButtons, main panel 로직이 모두 한 파일에 있다. 1,200줄을 넘으면 유지보수가 급격히 어려워진다.

### T2. Tailwind JIT — 탐지되지 않는 동적 클래스
현재 코드베이스에서 `bg-${color}` 패턴의 **완전한 동적 클래스(template literal 내 변수)** 는 `COLOR_MAP`(ControlPanel)으로 우회되어 안전하다. 그러나 향후 리팩터링에서 누군가 `bg-${isPositive ? 'ngsat-green' : 'ngsat-red'}` 패턴을 사용하면 safelist에 추가하지 않는 한 프로덕션 빌드에서 클래스가 누락된다.

### T3. API 스키마 변경에 대한 취약성
모든 컴포넌트가 `connected` 플래그와 특정 필드 구조(예: `account.total_asset`, `trades[].side`)에 직접 의존한다. 백엔드 API 변경 시 프론트엔드 모든 컴포넌트를 일일이 수정해야 한다. **TypeScript 도입** 또는 API 응답 타입 정의가 시급하다.

### T4. React.StrictMode 이중 렌더링
`main.jsx`에서 `<React.StrictMode>`로 감싸고 있다. 개발 모드에서 useEffect가 2번 실행되어:
- BacktestPanel: 백테스트가 2번 실행될 수 있음 (pollRef 중복)
- 모든 Polling interval이 2개씩 생성될 위험
StrictMode 호환성을 위한 cleanup이 필요하나, BacktestPanel의 runBacktest는 cleanup이 불완전하다.

### T5. 모바일 확장성
반응형은 기본 수준으로 구현되어 있지만(사이드바 hamburger, 그리드 반응), 태블릿(768-1024px) 환경에서 가독성과 조작성이 떨어진다. 특히 StrategyConfigPanel의 복잡한 폼 컨트롤은 모바일에서 거의 사용 불가능하다.

---

## 주요 발견 요약 테이블

| 파일 | 심각도 | 발견 사항 |
|------|--------|-----------|
| `App.jsx` 69-73 | 🔴 CRITICAL | 5초마다 7개 API 전량 호출 — 활성 탭과 무관 |
| `App.jsx` 75-85 | 🔴 CRITICAL | handleControl API 실패 조용히 무시 |
| `App.jsx` 165-168 | 🟡 HIGH | 서버 연결 상태 w-2 h-2 점 하나로만 표시 |
| `main.jsx` | 🟡 HIGH | React.lazy/Suspense 미사용 — 초기 번들 비대 |
| `Sidebar.jsx` 103 | 🟡 HIGH | 탭 전환 시 포커스 메인 콘텐츠로 이동 안 됨 |
| `TradesTable.jsx` 210-234 | 🟡 HIGH | 이중 데이터 소스(propTrades + localData) 혼선 |
| `StrategyConfigPanel.jsx` 792 | 🟡 HIGH | 메타 프롭스를 config 데이터에 혼합 |
| `tailwind.config.js` | 🔵 MEDIUM | ngsat-muted(#8b8e98) 대비 4.2:1 — WCAG AA 미달 가능 |
| `StatusCard.jsx` 외 | 🔵 MEDIUM | 초기 로딩 시 대시("—")만 표시, 스켈레톤 없음 |
| `index.css` | 🔵 MEDIUM | range slider accent(#6c63ff) ≠ ngsat-accent(#3b82f6) 색상 불일치 |
| `StrategyConfigPanel.jsx` 746 | 🔵 MEDIUM | setTimeout 기반 재시작 — cleanup 없음, 경쟁 상태 |
| `utils.js` 3-6 | 🔵 MEDIUM | Math.round로 소수점 정보 손실 |
| `App.jsx` 176-192 | 🔵 MEDIUM | 모바일 헤더 지수 미니스트립 오버플로우 위험 |
| `StrategyConfigPanel.jsx` 409 | 🔵 MEDIUM | window.alert 사용 — UX 저하 |
| `EvidenceBox.jsx` 24 | 🔵 MEDIUM | 10개 항목 제한 — 더 보기 불가 |
| `Pagination.jsx` | 🟢 LOW | aria-current="page" 누락 |
| `EquityChart.jsx` 4 | 🟢 LOW | 데이터 1개만 있어도 차트 영역 사라짐 |
| `RegimeCard.jsx` 50 | ✅ OK | reason line-clamp-2 적절 |
| `ConfirmModal.jsx` | ✅ OK | 포커스 트랩 + Escape + backdrop 닫기 완비 |
| `Toast.jsx` | ✅ OK | role="alert" + aria-live="polite" |
| `api.js` | ✅ OK | fetchJSON으로 에러 정규화 일관됨 |
| `tailwind.config.js` safelist | ✅ OK | 동적 클래스 safelist 사전 대응 완료 |

---

## 종합 평가

**종합 점수: 7.0 / 10** (트레이딩 대시보드 기준)

### 평가 요약

NGSAT 대시보드는 **한국어 트레이딩 UI**로서 기본적인 UX 원칙을 충실히 따르고 있다. 다크테마 디자인 시스템이 일관되게 적용되어 있고, 접근성 기본 요소(ARIA, 키보드 내비게이션, 포커스 트랩)가 갖춰져 있으며, 에러 복원력이 우수하다. `rebalanceWeights`와 같은 금융 도메인 특화 로직을 UI 레벨에서 우아하게 처리한 점은 인상적이다.

**가장 시급한 개선 영역 (CRITICAL)**:
1. **불필요한 Polling** — 모든 API를 5초마다 전량 호출하는 것은 성능, 네트워크, 배터리 모든 측면에서 낭비. 활성 탭 기반 Polling 또는 WebSocket 전환 필수
2. **제어 동작 에러 처리** — 사용자가 버튼을 눌렀는데 동작이 실패해도 알림이 없음. 자동매매 시스템에서 이는 자산 손실로 이어질 수 있음

**권장 우선순위**:
1. 🔴 WebSocket 도입 또는 탭 기반 Polling 최적화
2. 🔴 handleControl 에러 처리 + Toast 연동
3. 🟡 React.lazy 코드 스플리팅 (BacktestPanel, StrategyConfigPanel, DiagnosisPanel)
4. 🟡 연결 상태 시각적 강화 (고정 상단 배너)
5. 🟡 TradesTable 단일 데이터 소스로 통일
6. 🔵 StrategyConfigPanel 리팩터링 (메타 프롭스 분리, 모바일 대응)
7. 🔵 모든 카드에 SkeletonCard 패턴 적용
8. 🔵 formatNumber 정밀도 수정
9. 🟢 Pagination aria-current 추가
10. 🟢 index.css 하드코딩 색상을 CSS 변수로 대체

**칭찬할 점**: 인디케이터 영역(지수 미니스트립), ConfirmModal의 완성도, StrategyConfigPanel의 사용자 친화적 힌트/설명 구조, 일관된 한국어 UX는 다른 트레이딩 대시보드에서 보기 드문 높은 완성도이다. 특히 StrategyConfigPanel의 각 섹션 설명(`desc`)과 필드별 `hint`는 금융 지식이 적은 사용자도 이해할 수 있도록 풀어쓴 점이 돋보인다.

> **결론**: 기능적 완성도는 높지만, 성능 최적화와 에러 처리에서 트레이딩 시스템이 요구하는 안정성 기준에 조금 미치지 못한다. 위 CRITICAL 2건을 먼저 해결하고, HIGH 항목을 단계적으로 개선하면 프로덕션 품질의 대시보드로 발전할 수 있다.
