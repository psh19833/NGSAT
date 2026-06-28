# esbuild 해결 후 전체 실행계획

> 2026-06-28 기준, `npm run build` 정상화됨
> 그동안 빌드 문제로 보류됐던 프론트엔드 작업들 재개

---

## 즉시 가능 (프론트엔드 빌드 필요)

### 1. A-5 ConfirmModal — 위험 액션 확인창 (1시간)
**보류 사유**: esbuild 빌드 이슈 → **해결됨** ✅
**내용**: 종료·강제매도 등 위험한 버튼 누르면 "정말 실행하시겠습니까?" 확인창
**파일**: `ConfirmModal.jsx` (신규) + `App.jsx` (handleControl 수정)
**리스크**: LOW

### 2. Toast + ErrorBoundary 완전 활성화 (완료)
이미 코드 구현 완료. 새 빌드로 적용됨.

---

## 대시보드 신규 기능 (esbuild 해결로 가능해짐)

### 3. 백테스트 탭 — 8번째 탭 추가 (2일)
기획: `docs/20-백테스트-대시보드-기획.md`
- POST /api/backtest/run (백엔드)
- BacktestPanel.jsx (프론트)
- 진행률 표시 + 결과 차트 + 거래내역

### 4. A-3 DB 엔진 이중화 (0.5일)
- data/db.py와 orchestrator의 독자 DB 엔진 통합
- 백엔드 작업만 필요, 빌드와 무관
- MEDIUM 리스크

---

## 권장 실행 순서

```
1. A-5 ConfirmModal (1시간)    ← 빌드 필요, esbuild 해결됨
2. A-3 DB 엔진 통합 (0.5일)     ← 백엔드만, 빌드 불필요
3. 백테스트 탭 (2일)            ← 빌드 필요
```

1번(A-5)부터 진행할까요?
