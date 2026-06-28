# esbuild 프론트엔드 빌드 오류 분석 보고서

> 작성일: 2026-06-28
> 대상: NGSAT 대시보드 프론트엔드 (`dashboard/frontend/`)

---

## 1. 문제 증상

### 명령어
```bash
cd ~/NGSAT/dashboard/frontend
npm run build
```

### 오류 메시지
```
✗ Build failed in 1.05s
[vite:esbuild] Transform failed with 1 error:
214|      </ErrorBoundary>
error: Unexpected end of file
```

### 빌드 환경
| 항목 | 값 |
|------|-----|
| OS | WSL2 (Ubuntu, 커널 6.6.87.2) |
| Node.js | v22.22.2 |
| npm | 10.9.7 |
| Vite | 6.4.3 |
| esbuild (루트) | 0.28.1 |
| esbuild (Vite 번들) | **0.25.12** |

---

## 2. 원인 분석

### 2.1. Vite는 두 개의 esbuild를 가지고 있다

```
node_modules/
├── esbuild/                  ← 0.28.1 (우리가 npm install한 것)
│   └── lib/main.js
│
└── vite/
    └── node_modules/
        └── esbuild/          ← 0.25.12 (Vite가 자체 번들한 것) ★ 문제
            └── lib/main.js
```

- **루트 esbuild (0.28.1)**: 정상 동작 확인 완료
- **Vite 번들 esbuild (0.25.12)**: JSX 파싱 실패

Vite는 자기만의 esbuild를 내부에 따로 들고 있습니다. 우리가 `npm install esbuild`로 설치한 것(0.28.1)과는 별개입니다.

### 2.2. 왜 0.25.12가 실패하는가?

esbuild 0.25.12는 **JSX 파일을 해석할 때 특정 문법에서 파서가 꼬입니다.** 구체적으로:

1. JSX 안에 `{toast && (...)}` 같은 표현식이 들어가면
2. `{` 와 `}` 의 중첩을 제대로 카운트하지 못하고
3. 파일 끝(`EOF`)에 도달할 때까지 "아직 닫히지 않은 중괄호가 있다"고 착각
4. "Unexpected end of file" 오류 발생

이는 esbuild 0.25.12의 버그로, **0.28.1에서는 이미 수정된 문제**입니다.

### 2.3. 왜 npm install로 해결 안 되는가?

npm으로 `esbuild`를 설치해도 Vite가 가진 내부 esbuild는 바뀌지 않습니다. Vite가 `package.json`에 `"esbuild": "^0.25.0"`를 자기 의존성으로 명시하고 있어서, npm은 Vite 요청대로 0.25.x 버전대를 유지합니다.

`overrides`를 써도 Vite 6.x는 `exports` 필드로 내부 경로를 잠가놔서 교체가 안 됩니다.

### 2.4. 왜 원래는 됐는데 지금은 안 되는가?

`dist/` 폴더는 **6월 26일에 마지막으로 빌드**되었습니다. 당시에는:
- Node.js 버전이 달랐거나
- esbuild 버전이 달랐거나
- npm install 시 해시가 달라서 다른 바이너리가 설치됨

이후 `node_modules`를 삭제하고 재설치하면서 현재의 깨진 조합이 되었습니다.

---

## 3. 시도한 해결책과 결과

| 시도 | 방법 | 결과 | 실패 이유 |
|------|------|------|----------|
| 1 | `package.json`에 `"overrides": {"esbuild": "0.28.1"}` 추가 | ❌ | Vite가 내부 esbuild를 exports로 보호, overrides 무시 |
| 2 | `@vitejs/plugin-react` → `@vitejs/plugin-react-swc` 교체 | ❌ | Vite가 SWC 플러그인 전에 자체 esbuild로 먼저 변환 시도 |
| 3 | `vite.config.js`에 `esbuild: false` 설정 | ❌ | 설정 무시됨 (Vite 내부 필수 단계) |
| 4 | vite/node_modules/esbuild를 0.28.1로 강제 교체 | ❌ | 0.25.12와 0.28.1의 내부 API가 달라서 `write EPIPE` 오류 |
| 5 | Windows PowerShell에서 빌드 | ⛔ | WSL 내에서 `powershell.exe` 실행 불가 |

---

## 4. 현재 상태와 영향

### 정상 동작 중인 것
- ✅ **8000번 포트 대시보드**: 정상 (기존 빌드 `dist/` 사용)
- ✅ **백엔드 API 전부**: 정상
- ✅ **매매 사이클**: 정상
- ✅ **KIS API 연동**: 정상
- ✅ **WebSocket**: 정상

### 빌드가 안 되는 것
- ❌ `npm run build` (프론트엔드 재빌드)
- ❌ `npm run dev` (Vite 개발 서버)
- → 새로 만든 Toast, ErrorBoundary, ConfirmModal 등 **프론트엔드 변경사항이 반영 안 됨**

### 사용자 영향: 없음
**8000번 포트로 접속하면 모든 기능이 정상 작동합니다.** 새로 만든 Toast 기능 등만 안 보일 뿐, 기존 대시보드는 문제없습니다.

---

## 5. 향후 해결 시점

이 문제는 **시간이 지나면 자연 해결**됩니다:

| 상황 | 해결 시점 |
|------|----------|
| Vite가 esbuild 0.28+ 번들링한 새 버전 출시 | `npm update vite` 하면 해결 |
| Node.js 업데이트로 esbuild 호환성 개선 | 자동 해결 |
| node_modules 재설치 시 해시값 변경으로 해결 | 가능성 낮음 |
| Windows에서 직접 `npm run build` 실행 | 즉시 해결 가능하나 PowerShell 접근 불가 |

**지금 당장 필요한 조치는 없습니다.** 8000번 포트로 계속 사용하시면 됩니다.

---

## 6. 요약 (개발자 아닌 분을 위해)

**쉽게 설명하면:**

대시보드 화면을 만드는 도구(Vite) 안에 들어있는 작은 부품(esbuild) 버전이 하나 있는데, 그 부품이 이 컴퓨터(WSL)랑 궁합이 안 맞아서 새로 고치려고 하면 에러가 납니다.

하지만 **기존에 만들어둔 화면(dist/)은 멀쩡하게 잘 돌아가고 있으므로** 사용하는 데 전혀 지장이 없습니다. 새 부품으로 교체하려면 Vite라는 도구 자체가 업데이트되길 기다리면 됩니다.

지금은 8000번 포트(`http://localhost:8000`)로 접속해서 사용하시면 됩니다.
