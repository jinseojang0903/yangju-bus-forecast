---
paths:
  - "frontend/**"
---
<!-- agent-setup-template: v1.0 -->
# 프론트엔드 규칙 (React + Vite + TypeScript, npm, Biome)

<!-- 2026-10-06 범위 축소(CLAUDE.md '범위')에 맞춰 갱신. 화면은 시민용 모바일 웹만 만든다. -->

## 구조
- 폴더: `src/api/`(클라이언트·타입·엔드포인트), `src/components/`(공용), `src/features/<도메인>/{components,hooks}`, `src/hooks/`(공용 훅), `src/lib/`(문구·포맷·시각 유틸), `src/test/`(테스트 도구).
- 화면은 시민용 3종(조건 입력, 예보, 근거 부족)이다. 시 담당자 대시보드, 히트맵, 요일유형 선택처럼 범위 밖 화면은 만들지 않는다.
- 도메인 전용 순수 함수는 도메인 폴더 루트에 두고 옆에 `*.test.ts` 를 둔다. 공용 포맷은 `src/lib/format.ts`(숫자), `src/lib/time.ts`(KST 시각)에 한 번만 정의한다.
- 렌더 중 예외는 루트 라우트의 `RouteErrorPage`(errorElement)가 받고, 예외 메시지를 보여주지 않는다.
- 컴포넌트는 `PascalCase.tsx`, 스타일은 같은 이름의 `.module.css`. 라우팅은 react-router v7(`src/routes.tsx`).

## API 호출
- `src/api/client.ts` 의 `apiGet` 을 쓰고, 엔드포인트 함수는 `src/api/endpoints.ts` 에 둔다. 컴포넌트에서 fetch 를 직접 호출하지 않는다. 응답 최상위 배열 필드는 `arrayFields` 로 확인한다.
- 요청/응답 타입은 `docs/api-contract.md` 와 1:1 로 맞춘다(`src/api/types.ts`). 서버 상태는 @tanstack/react-query(키는 `src/api/queryKeys.ts`).
- 에러 문구는 `src/components/ErrorMessage` 한 곳에서 정한다. 클라이언트 전용 코드 `NETWORK_ERROR`, `INVALID_RESPONSE` 는 서버로 보내지 않는다.
- 숫자(확률, n, k, 시각, 대안)는 서버 스냅샷 값을 그대로 보여준다. 화면에서 다시 계산하지 않는다. LLM 설명은 숫자를 먼저 그린 뒤 뒤에 붙인다.
- 프론트 번들에 어떤 키도 넣지 않는다. 외부 호출은 모두 백엔드를 거친다.

## UI
- 스타일: CSS Modules. `src/index.css` 에는 리셋과 색 토큰만 둔다.
- 사용자 입력이나 서버 문자열을 HTML 로 삽입하지 않는다. 외부 링크에는 `rel="noopener noreferrer"`.
- 모바일 우선(360px). 모든 입력에 label, 키보드 조작 가능, 클릭 요소는 `<button>`.
- 위험 표현은 '무좌석 위험'이며 문구는 `src/lib/labels.ts` 한 곳에 둔다: '위험 높음'(70% 이상), '위험 보통'(30% 이상 70% 미만), '위험 낮음'(30% 미만). 색만으로 구분하지 않고 문구와 'n회 중 k회'를 함께 표시한다. '탑승 여유', '만차 가능' 같은 옛 문구는 쓰지 않는다.
- 공개 기준 판정 전에 보여주는 확률에는 반드시 '예비' 표기를 붙인다. 사례 부족(20건 미만)이면 확률 없이 현재 잔여석만 보여준다.

## 테스트
- 컴포넌트 옆 `*.test.tsx`(Vitest + Testing Library), fetch 모킹은 `src/test/mockFetch.ts`. 실행: `cd frontend && npm run test`
- 신규 컴포넌트는 렌더링 1건과 주요 상호작용 1건 이상. 예보·대안 상태값마다 렌더링 테스트를 둔다.

## 명령
- 개발 서버: `cd frontend && npm run dev` (`/api` 는 127.0.0.1:8000 으로 프록시)
- 빌드: `npm run build`, 린트: `npm run lint`, 포맷: `npm run format` (Biome)
