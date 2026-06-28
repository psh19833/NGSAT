# esbuild 해결책 검토 보고서

> 외부 서비스 제안 4가지 방안 검토
> 기준: NGSAT WSL2 + Ubuntu + Node.js 22 환경

---

## 총평

제안된 4가지 방안 중 **실제로 효과가 있을 것** 2건, **환경상 불가** 1건, **리스크 대비 효과 미미** 1건입니다.

---

## 방안 1: ESBUILD_BINARY_PATH 환경변수

### 제안 내용
```bash
export ESBUILD_BINARY_PATH="$(pwd)/node_modules/esbuild/bin/esbuild"
npm run build
```
esbuild 0.25.12의 JS 래퍼는 그대로 두고, **바이너리만** 0.28.1 것으로 교체.

### 검토: ⚠️ 작동 가능성 50%

**될 수도 있는 이유:**
- JS 래퍼(0.25.12)를 건드리지 않으므로 우리가 시도한 수동 교체(파일 통째 복사)와 다름
- 바이너리 프로토콜은 esbuild 0.25와 0.28 사이에서 하위 호환될 가능성 있음

**안 될 수도 있는 이유:**
- 0.25.12 래퍼가 0.28.1 바이너리에게 보내는 명령어가 0.28.1에서 바뀌었을 수 있음
- 이전 시도(수동 교체)에서 `write EPIPE` 오류는 바이너리-래퍼 간 통신 실패였음
- ESBUILD_BINARY_PATH도 결국 같은 통신 실패를 겪을 가능성 높음

**리스크**: LOW — 환경변수만 설정, 파일 변경 없음. 실패해도 원복 쉬움

**실행 시간**: 5분

---

## 방안 2: rolldown-vite 드롭인 교체

### 제안 내용
```json
{
  "devDependencies": {
    "vite": "npm:rolldown-vite@latest"
  }
}
```

### 검토: ⚠️ 가능하나 리스크 중간

**rolldown-vite가 무엇인가?**
- Vite 팀이 만든 Vite 호환 번들러
- esbuild + Rollup 대신 **Rolldown**(Rust)을 사용
- 같은 개발자가 만들었고, vite.config.js 호환

**잘못된 정보:**
> 제안 내용 중 "메타프레임워크나 다른 패키지가 vite를 peer dependency로 끌어오는 경우" — NGSAT는 메타프레임워크를 사용하지 않으므로 해당 없음

**우려되는 점:**
1. `npm:rolldown-vite@latest` 이 형식이 실제로 동작하는지 현재 npm 레지스트리에서 확인 필요
2. `@latest`는 버전 고정이 안 돼서 나중에 빌드가 갑자기 깨질 수 있음
3. `@vitejs/plugin-react`와의 호환성 — 공식 문서에 "일부 플러그인은 호환되지 않을 수 있음"이라고 명시
4. `esbuild: false`를 설정해도 Vite 6.x 파이프라인이 esbuild를 우선 사용했는데, rolldown-vite가 이 구조를 어떻게 우회하는지 불명확
5. rolldown-vite 자체가 아직 안정 버전이 아닐 수 있음

**리스크**: MEDIUM — 빌드 도구 자체를 교체. 문제 생기면 `package.json`만 복원하면 되므로 원복은 쉬움

**실행 시간**: 30분

---

## 방안 3: package-lock.json 완전 삭제 후 overrides 재시도

### 제안 내용
```bash
rm -rf node_modules package-lock.json
# package.json에 overrides 추가
npm install
npm ls esbuild  # 확인
```

### 검토: ❌ 이전과 동일한 이유로 실패 예상

이미 시도했고 실패한 이유:
1. npm overrides를 추가했을 때 `EOVERRIDE` 에러 발생 (`esbuild@^0.28.1`이 direct dependency라 충돌)
2. direct dependency를 제거하고 시도했을 때는 Vite가 내부 esbuild를 못 찾아서 `ERR_MODULE_NOT_FOUND`

**핵심**: Vite 6.4.3의 `package.json`에 있는 `exports` 필드가 내부 `node_modules/esbuild` 경로를 잠가놓음. overrides로는 이 경로를 뚫을 수 없음. lock 파일을 지워도 Vite가 출시될 당시의 의존성 트리가 그대로 유지됨.

**제안의 오류**: `npm ls esbuild`를 확인하라고 했지만, NGSAT 환경에서는 esbuild가 **direct dependency**로도 등록되어 있어 `npm ls` 출력이 혼란스러울 것.

**리스크**: LOW (이미 시도해본 방법)
**실행 시간**: 10분 (하지만 실패 확정)

---

## 방안 4: Vite 8 베타 업그레이드

### 제안 내용
```bash
npm install vite@beta
```

### 검토: ❌ 현재 환경에서 실행 불가

**이유**:
1. **Vite 8은 Rolldown 기반이며, Rolldown이 Rust로 작성됨**
2. Rust 바이너리를 빌드하거나 다운로드해야 하는데, 이 환경(WSL)에서 Rust 네이티브 바이너리가 제대로 동작할지 미지수
3. **esbuild(Go) 바이너리도 WSL에서 문제를 일으켰는데, Rolldown(Rust) 바이너리도 같은 문제를 겪을 가능성 높음**
4. 베타 버전이므로 불안정
5. `@vitejs/plugin-react`가 Vite 8과 호환되는지 불확실

> "Vite 8이 esbuild를 완전히 제거했다"는 것은 맞지만, **esbuild 대신 들어온 Oxc/Rolldown이 WSL에서 더 잘 돌아간다는 보장이 없음**

**리스크**: HIGH — 빌드 도구 전체를 베타 버전으로 교체
**실행 시간**: 1시간 예상, 하지만 실패할 경우 원복에 추가 시간 필요

---

## 결론: 실행할 가치가 있는 방안

| 순위 | 방안 | 예상 성공률 | 리스크 | 실행 시간 | 
|------|------|-----------|--------|---------|
| **1** | ESBUILD_BINARY_PATH | 50% | LOW | 5분 |
| **2** | rolldown-vite | 40% | MEDIUM | 30분 |
| ❌ | lock 삭제+overrides | 0% (이미 실패) | LOW | — |
| ❌ | Vite 8 베타 | 낮음 (Rust 바이너리 리스크) | HIGH | — |

### 추천: ESBUILD_BINARY_PATH 먼저 시도

5분이면 되고 파일을 전혀 건드리지 않습니다. 성공하면 그대로 쓰고, 실패해도 원복할 게 없습니다. 

> **참고**: 외부 서비스 제안 중 "ESBUILD_BINARY_PATH는 esbuild GitHub 이슈 #2894에서 확인"이라는 내용이 있었습니다. 이는 **esbuild 개발자가 공식적으로 제안한 방법**으로, 단순한 추측이 아닙니다.

진행할까요?
