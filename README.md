# 탈 수 있을까? 양주 광역버스 만차 예보 AI

광역버스가 **내 정류장에 도착할 때 빈자리가 없을 위험**을, 과거 비슷한 운행이 실제로 어땠는지(`n회 중 k회`)로 알려 주는 모바일 웹입니다. 도착 마감에 맞는 대안 버스도 함께 비교해 줍니다.

- 2026 양주시 미래인재 AI 공모전 출품작 (팀 탈수있조, SUB 02 똑똑하고 편리한 양주)
- 1차 기획안 제출 2026-10-04 · 2차 제출 2026-10-15 22:59 · 본선 발표 2026-10-17
- 예보 대상: **G1300 잠실행, 덕현초교(고읍) 정류장, 평일 06~08시대**. 대안 비교용으로 1306

자세한 범위와 바꾸지 않는 규칙은 [CLAUDE.md](CLAUDE.md)에 있습니다.

## 현재 상태 (2026-10-06)

| 구분 | 상태 | 내용 |
|---|---|---|
| GBIS 수집기 (F05) | 완료 | 양주 광역 12개 노선 위치 + 덕현초교 도착정보를 평일 05:30~10:15 자동 수집 |
| 운행편·정답 라벨 (F06) | 1차 완료 | G1300·1306 운행편 재구성, '도착 상태 0석' 라벨(엄격·완화 등급) 리포트 |
| API 명세서 | 예정 | 10/7 회의 후 |
| 테이블 설계 (SQL) | 예정 | 10/7 회의 후 |
| API 서버 (FastAPI) | 예정 | 명세 확정 후 |
| 화면 (React) | 예정 | 명세 확정 후 |

## 수집하는 것

| 대상 | 주기 | 하루 호출 |
|---|---|---|
| 위치 G1300 (예보 대상) | 10초 | 1,710 |
| 위치 1306 (대안 비교) | 30초 | 570 |
| 위치 1100 · 1101 · 1304 · 1407 · 8300 · 8906 · G1200 · P9601·P9602·P9603(출근) | 40초 | 각 428 |
| 도착 덕현초교 잠실행 (235000392) | 30초 | 570 |

- 하루 예상 호출: 위치 6,560회, 도착 570회. 계획 상한(위치 9,000, 도착 950)을 넘는 설정이면 수집기가 시작하지 않습니다.
- 노선 ID와 수집 여부는 [`backend/app/core/settings.py`](backend/app/core/settings.py)의 `YANGJU_ROUTES`가 기준입니다. 심야(G1300N)·퇴근 전용·타 시 관할 노선은 목록에만 있고 수집하지 않습니다.
- 대상마다 따로 돌기 때문에, 한 노선이 느리거나 실패해도 G1300 주기는 밀리지 않습니다.

## 저장소 구조

```
backend/
  app/core/settings.py   설정과 고정 상수(대상 노선, 주기, 수집 시간, 호출 한도, 예보 규칙 값)
  app/collector/         GBIS 수집기
  app/labeling/          운행편 재구성·정답 라벨·리포트
  tests/                 수집기·라벨 테스트 (GBIS 응답 픽스처 포함, 서비스 키 없음)
  .env.example           환경변수 이름 예시
deploy/
  README.md              서버 설치·확인·백업·PC 임시 실행 안내
  yangju-collector.service  systemd 서비스 파일
CLAUDE.md                범위, 바꾸지 않는 규칙, 기능·API·데이터 모델 초안
```

## 개발 환경

필요한 것: [uv](https://docs.astral.sh/uv/) (Python은 uv가 알아서 받습니다)

```bash
cd backend
uv sync                 # 의존성 설치
uv run pytest           # 테스트
uv run ruff check .     # 린트
uv run ruff format .    # 포맷
```

`backend/.env.example`을 `backend/.env`로 복사하고 값을 채웁니다. **`.env`는 커밋하지 않습니다.**

## 수집기 명령

`backend` 폴더에서 실행합니다.

| 명령 | 하는 일 |
|---|---|
| `uv run python -m app.collector status` | 오늘 수집 상태를 한 줄 JSON으로 출력 (대상별 호출·실패·건너뜀) |
| `uv run python -m app.collector once` | 지금 모든 대상을 1회 호출(13회 사용) |
| `uv run python -m app.collector run` | 평일 05:30~10:15에 계속 수집 (서버에서는 systemd가 실행) |
| `uv run python -m app.collector run --trial-until HH:MM` | 시운전. 기록은 `mode: trial`로 남아 평가에서 빠짐 |
| `uv run python -m app.labeling report --date YYYY-MM-DD` | 그날 운행편 라벨 등급별 건수 |

**같은 서비스 키로 두 곳에서 동시에 수집하지 않습니다.** 하루 호출 한도를 두 배로 쓰게 됩니다. 서버가 수집하는 동안 다른 PC에서는 `once`나 시운전도 하지 않습니다.

서버 설치와 운영은 [deploy/README.md](deploy/README.md)를 따릅니다.

## 수집 데이터

- 위치: `data/collected/<YYYY-MM-DD>/raw_poll.jsonl` (GBIS 응답 원본, 한 줄에 호출 1건). 상태 파일은 `data/collected/status.json`입니다.
- 저장소에는 올리지 않습니다(`.gitignore`). 매일 10:15 이후 하루치를 백업합니다(deploy/README.md 9장).
- 시운전(`mode: trial`)과 공휴일(`is_holiday: true`) 기록은 사례·평가에서 뺍니다.

## 협업 방식

1. `main`에 직접 올리지 않고, 작업마다 브랜치를 만듭니다. 예: `feat/api-contract`, `fix/collector-timeout`, `docs/readme`
2. Pull Request를 열고, 무엇을 왜 바꿨는지와 확인한 방법(테스트 결과, 시운전 등)을 적습니다.
3. 다른 팀원이 내용을 확인하고 승인(Approve)하면 머지합니다.
4. 커밋 메시지는 "무엇을"보다 "왜"를 적습니다. 하나의 커밋은 하나의 의도만 담습니다.
5. 서비스 키, DB 접속 정보, 수집 데이터는 어떤 경우에도 커밋하지 않습니다.

**예외:** 수집기가 멈춰 데이터를 잃고 있는 긴급 상황에는 먼저 고쳐 반영하고, 사후에 PR로 기록을 남깁니다.
