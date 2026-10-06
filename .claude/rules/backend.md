---
paths:
  - "backend/**"
  - "analysis/**"
  - "deploy/**"
---
<!-- agent-setup-template: v1.0 -->
# 백엔드 규칙 (Python + FastAPI, uv)

<!-- 2026-10-06 범위 축소(CLAUDE.md '범위')에 맞춰 갱신. 데이터 모델은 10/7 회의에서 확정된다. -->

## 구조
- 레이어: router(`app/api/v1/`) → service(`app/services/`) → repository(`app/repositories/`). 레이어를 건너뛰어 호출하지 않는다.
- 스키마는 `app/schemas/` 에 둔다(계약 타입명과 맞춘다). 공통 타입과 ID 정규식은 `app/schemas/common.py` 에 한 번만 정의한다.
- 저장소 주입은 `app/api/deps.py` 의 `get_repository` 하나에서 한다(가짜 데이터 → DB 교체 지점).
- 설정·고정 상수는 `app/core/settings.py` 한 곳에 둔다. CLAUDE.md '바꾸지 않는 규칙'의 값은 이 모듈의 상수만 참조하고, 다른 곳에 숫자를 다시 쓰지 않는다. 값을 바꿔야 할 것 같으면 구현하지 말고 메인에 보고한다.
- GBIS 수집기는 `app/collector/`, 배포 파일은 `deploy/` 에 둔다. 탐색용 노트북과 일회성 분석은 `analysis/` 에 두고, 재사용할 로직은 `backend/` 로 옮긴다.
- 범위 밖 기능(환승 배차, 수요–공급, 정책 시뮬레이션, 대시보드, 내일 예측, 잔여좌석 수 예측, 학습 모델)은 만들지 않는다.

## API
- 엔드포인트는 `docs/api-contract.md` 에 먼저 정의된 것만 구현한다. 접두사는 `/api/v1`. 쿼리 파라미터는 camelCase alias 로 받는다.
- 요청 검증은 Pydantic·Query(Enum, 범위, 정규식)로 경계에서 한다. 검증 실패는 400 `VALIDATION_FAILED` 다.
- 응답은 `response_model` 에 Pydantic 스키마를 지정한다. 에러 처리는 `app/core/errors.py` 에서 하고, 메시지는 고정 문자열이다.
- 이용자 요청 경로에서 GBIS 를 호출하지 않는다. 이용자 조회는 수집 데이터와 1분 캐시로만 처리한다.

## 데이터
- 수집 원본은 날짜별 JSONL(`data/collected/<날짜>/`)에 먼저 쓰고, 그다음 DB 에 넣는다. DB 실패가 수집을 멈추게 하면 안 된다.
- DB 는 PostgreSQL(Supabase), ORM 없이 psycopg 로 SQL 을 직접 쓴다. 쿼리는 파라미터 바인딩만. 스키마 기준은 `backend/migrations/*.sql`(Supabase SQL 편집기로 적용).
- 시각은 KST(`Asia/Seoul`, zoneinfo)로 계산하고 시간대 있는 타입으로 저장한다. OS 시간대·경로에 의존하지 않는다(pathlib).
- GBIS·DB·LLM 키는 `backend/.env` 에서만 읽는다. URL·로그·예외 메시지·JSONL·픽스처에 남기지 않는다.

## 테스트
- 테스트 위치: `backend/tests/` (pytest), 실행: `cd backend && uv run pytest`
- 새 엔드포인트마다 정상 1건, 실패(검증 400·없음 404) 1건 이상.
- 외부 API(GBIS·LLM)는 테스트에서 실제로 호출하지 않는다. 키를 지운 응답 픽스처(`tests/fixtures/`)를 쓴다.

## 명령
- API: `cd backend && uv run uvicorn app.main:app --reload` (포트 8000, 개발용)
- 수집기: `cd backend && uv run python -m app.collector <discover|once|run|status|save-fixture>`
- 린트/포맷: `cd backend && uv run ruff check . && uv run ruff format .`
