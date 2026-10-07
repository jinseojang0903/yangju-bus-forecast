# API 계약 (api-contract.md)

> 프론트엔드와 백엔드가 함께 따르는 단일 기준이다. 양쪽에 걸치는 작업은 **이 문서를 먼저 고친 뒤** 구현한다.
> backend-dev / frontend-dev 는 작업 전에 이 문서를 읽고, 임의로 바꾸지 않는다. 바꿀 필요가 있으면 메인에 보고한다.
> FastAPI 의 `/docs`(OpenAPI)가 필드 수준 명세 역할을 하며, 이 문서와 응답 모델(Pydantic)이 어긋나면 안 된다.

- 상태: **회의용 초안 v2** (2026-10-06, 검토 의견 반영). 10/7 회의에서 확정한다. 정해지지 않은 항목은 "미정"(10장)에 있다.
- 기준 문서: `CLAUDE.md` 의 '바꾸지 않는 규칙', '기능'(F01~F10), 'API(초안)'
- 서버가 고르는 규칙(선행시간 선택, 캐시 주기 등)의 값은 모두 백엔드 설정 모듈(`backend/app/core/settings.py`) 한 곳에 둔다. 이 문서에 적힌 값은 그 설정의 초기값이다.

## 1. 공통 규칙

| 항목 | 규칙 |
|---|---|
| Base URL | `/api/v1` |
| 인증 | 없음(공개 조회). 위치·이름·전화번호를 받지 않는다. 정류장 단위로만 조회한다 |
| 형식 | JSON, UTF-8. 필드 이름은 camelCase |
| 시각(Timestamp) | ISO 8601, KST 오프셋 포함. 예: `2026-10-07T07:31:20+09:00` |
| 하루 중 시각(TimeOfDay) | KST `"HH:MM"`(두 자리 0 채움). 예: `"08:30"` |
| GBIS 호출 | **이용자 요청 경로에서 GBIS 를 호출하지 않는다.** 응답은 수집 데이터와 캐시(1.3절)로만 만든다 |
| LLM | 숫자·대안은 스냅샷 API 가 먼저 주고, 설명은 별도 API 로 뒤에 붙인다. LLM 은 숫자를 만들거나 고치지 않는다 |
| 규칙 버전 | 계산 결과가 담긴 응답에는 계산에 쓴 규칙 버전(`rulesVersion`)을 넣는다. 형식 `"YYYY-MM-DD.n"`, 설정 모듈에 있다 |

### 1.1 호출 횟수 제한 (Rate limit)
이용자 IP 단위로 센다. 초과하면 `429 RATE_LIMITED` 와 `Retry-After` 헤더(초)를 준다. 값은 설정 모듈에 둔다.

| 경로 | 제한(초기값) | 이유 |
|---|---|---|
| `GET /snapshot` | 분당 30회 | 화면은 `nextRefreshAt` 에 맞춰 조회하므로 정상 사용은 분당 6회 안팎 |
| `GET /snapshot/{id}/explanation` | 분당 20회 | 설명은 스냅샷당 1번만 LLM 을 부르고 이후는 캐시 |
| `POST /parse-query` | 분당 5회, 하루 50회 | LLM 비용. 서비스 전체 LLM 상한(하루 300회, 고정 규칙)과 별도 |
| 나머지 GET | 분당 60회 | |

LLM 전체 하루 상한(300회)에 닿으면 429 가 아니라 200 응답의 `fallbackReason: "daily_limit"` 으로 알린다(4.5, 4.6절).

### 1.2 CORS
- 허용 출처는 설정의 목록만 쓴다. 초기값: 배포된 프론트 주소(미정) 1개.
- 개발 중에는 Vite 프록시로 같은 출처에서 호출하므로 `localhost` 를 허용 목록에 넣지 않는다.
- `allow_credentials=false`, 메서드 `GET, POST`, 헤더 `Content-Type` 만 허용한다.

### 1.3 캐시와 다음 조회 시각
- 수집 주기: 위치 G1300 10초, 1306 30초, 도착 API 30초.
- 스냅샷은 (정류장, 목적지, 마감) 조합별로 **10초** 캐시한다. G1300 위치 갱신 주기에 맞춘 값이다. 도착 예상과 잔여석은 도착 API(30초)에서 오므로, 10초 안에 다시 계산해도 바뀌는 것은 위치 기반 값뿐이다.
- 응답의 `nextRefreshAt` = 다음 수집 갱신이 반영될 예상 시각(가장 짧은 대상 주기의 다음 경계 + 3초 여유). 화면은 이 시각에 다시 조회한다(사용자가 새로고침하지 않아도 됨).
- 서비스 시간 밖(2.2절)에는 `nextRefreshAt` 이 다음 예보 시작 시각이다.

### 1.4 공통 에러 형식
모든 4xx/5xx 응답은 아래 형식이다. `details` 는 항상 배열이다(없으면 `[]`). 메시지는 고정 문자열이며, 입력값이나 내부 예외 내용을 담지 않는다.

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "요청 형식이 올바르지 않습니다",
    "details": [{ "field": "deadline", "reason": "HH:MM 형식(00:00–23:59)이어야 합니다" }]
  }
}
```

| HTTP | code | 의미 |
|---|---|---|
| 400 | `VALIDATION_FAILED` | 요청 형식·필드 검증 실패 (FastAPI 기본 422 를 400 으로 바꿔 응답) |
| 404 | `NOT_FOUND` | 경로 또는 리소스(정류장·목적지·스냅샷) 없음 |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않은 HTTP 메서드 |
| 429 | `RATE_LIMITED` | 호출 횟수 제한 초과(`Retry-After` 헤더) |
| 500 | `INTERNAL_ERROR` | 서버 내부 오류 (상세 비노출) |

데이터가 오래됐거나, 사례가 부족하거나, 예보 시간이 아닌 경우는 **에러가 아니라 200 응답의 상태값**으로 알린다(5장).

## 2. 공통 타입

```ts
type Timestamp = string;   // ISO 8601 + "+09:00"
type TimeOfDay = string;   // "HH:MM" (KST)

type StationId = string;   // GBIS 정류소 ID, ASCII 숫자 1–20자리. 예: "235000392"(덕현초교 잠실행)
type RouteId = string;     // GBIS 노선 ID, ASCII 숫자 1–20자리. 예: "235000092"(G1300)
type VehicleId = string;   // GBIS vehId, ASCII 숫자
type DestinationId = string; // 자체 코드, ^[a-z_]{1,32}$. 예: "jamsil"
type SnapshotId = string;  // uuid. DB forecast_group.forecast_group_id 와 같다. 클라이언트는 해석하지 않는다
// ID 형식이 틀리면 400 VALIDATION_FAILED, 형식은 맞지만 없으면 404 NOT_FOUND

type LeadTimeMin = 5 | 10 | 15;                 // 선행시간(분). 고정 규칙
type RiskLevel = "high" | "medium" | "low";     // high: p ≥ 0.7, low: p < 0.3, 그 사이 medium. 고정 규칙
// 화면 문구: high "위험 높음", medium "위험 보통", low "위험 낮음". 표현은 '무좌석 위험'
```

### 2.1 선행시간과 예보 시각
- 선행시간 L분 예보는 **버스가 내 정류장 도착 L분 전이 되는 시점**에 낸다. 그때의 잔여석·앞차 간격으로 사례를 찾고, 결과를 그 버스에 대해 고정해 둔다(`issuedAt`).
- 그 시점이 아직 오지 않은 선행시간은 `status: "not_yet"` 이다.
- **화면에 쓸 선행시간(`selectedLeadTimeMin`)은 서버가 고른다.** 규칙은 설정 모듈 한 곳에 두고, 회의 전 초기 규칙은 **"이미 시점이 지난(`issuedAt` 이 있는) 예보 중 가장 최근 것"**이다(예: 도착 12분 전이면 15분 예보, 7분 전이면 10분 예보). 하나도 지나지 않았으면 `null`.
- 대안의 0석 확률(4.4 `alternatives`)과 LLM 설명은 **각 버스의 `selectedLeadTimeMin` 예보 값**을 쓴다.

### 2.2 서비스 시간
| 구분 | 시간(KST) | 응답 |
|---|---|---|
| 수집 시간 | 평일 05:30 이상 10:15 미만 | 밖이면 `service.state = "outside_collection"` |
| 예보 대상 시간대 | 내 정류장 도착 06:00~08:59 | 수집 시간 안이지만 지금 예보할 버스가 없으면 `service.state = "outside_forecast_hours"`. 버스별로는 `inForecastHours` |
| 둘 다 안 | | `service.state = "in_service"` |

- 서비스 상태는 버스 목록이 아니라 시계로 판정한다: 수집 시간 안이고 05:45 이상 09:00 미만이면 `in_service`(06:00 도착 버스의 15분 예보부터 08:59 도착 버스까지).
- 수집 시간 밖(`outside_collection`)에서는 새 데이터가 없는 것이 정상이므로 `stale` 은 항상 `false` 다.
- 화면은 `outside_*` 일 때 오류나 '정보 오래됨'이 아니라 **"지금은 예보 시간이 아니에요"** 와 `service.nextForecastStartAt`(다음 예보 시작 시각)을 보여 준다.
- `nextForecastStartAt` = 다음 평일 05:45(06:00 도착 버스의 15분 예보가 나오는 시각). 설정의 공휴일 목록(초기값 `2026-10-09`)에 있는 날은 건너뛴다. 시각과 공휴일 목록은 설정 모듈에 있다(사용자 결정 2026-10-06).
- **과거 시점 재생 기능은 만들지 않는다.** 본선 시연은 평일 아침 실제 화면을 녹화한 영상으로 한다. 예보 스냅샷은 계획대로 모두 저장한다(F07).

## 3. 엔드포인트 한눈에

| 메서드·경로 | 기능 | 화면 | 상태 |
|---|---|---|---|
| `GET /api/v1/health` | 수집 상태(공개 최소) | (운영 확인) | 이번 |
| `GET /api/v1/health/detail` | 수집 상태(상세) | (팀 운영) | 이번, 접근 제한 미정 |
| `GET /api/v1/stations` | 출발 정류장 목록 | 조건 입력 | 이번 |
| `GET /api/v1/stations/{stationId}/routes` | 정류장의 노선·목적지 | 조건 입력 | 이번 |
| `GET /api/v1/snapshot` | 현재 버스·0석 위험·대안을 같은 계산 시점으로 한 번에 | 예보, 대안, 근거 부족 | 이번 |
| `GET /api/v1/snapshot/{snapshotId}/explanation` | LLM 설명 또는 고정 문구 | 예보 | 이번(고정 문구) |
| `POST /api/v1/parse-query` | 자연어 질문 → 조회 조건 | 조건 입력 | 이번(unavailable) |
| `GET /api/v1/routes/{routeId}/positions` | 노선 정류장 좌표와 최신 수집 차량 위치·잔여석(F02 지도) | 예보(지도) | 이번 |
| `GET /api/v1/notices` | 승인된 운행 공지(F09) | 예보 | **추후 추가** |
| `GET /api/v1/reports/no-seat` | 30분대별 무좌석 도착 현황(F10) | 리포트 | **추후 추가** |
| `GET /api/v1/reports/validation` | 선행시간별 검증 지표(F10) | 리포트 | **추후 추가** |

`/health/detail` 은 OpenAPI(`/docs`)에 싣지 않고 토큰이 없으면 404 로 답하지만, 다른 메서드의 405 와 호출 제한의 429 로 경로가 있다는 것은 드러날 수 있다(은닉은 부분적이다).

## 4. 엔드포인트

### 4.1 GET /api/v1/health
공개용 최소 응답. 수집기 상태 파일을 읽으며 GBIS 를 호출하지 않는다.

```ts
{
  status: "ok" | "degraded" | "idle";
  // ok: 수집 시간 안이고 최근 수집이 정상
  // degraded: 수집 시간 안인데 마지막 성공이 오래됐거나(대상 주기의 3배 초과) 실패가 이어짐
  // idle: 수집 시간 밖
  now: Timestamp;
  lastSuccessAt: Timestamp | null;
}
```

### 4.2 GET /api/v1/health/detail
팀 운영용 상세 응답. 요청 헤더 `X-Health-Token` 이 `.env` 의 `HEALTH_DETAIL_TOKEN` 과 같아야 한다(사용자 결정 2026-10-06). 헤더가 없거나 틀리면, 또는 서버에 토큰이 설정되지 않았으면 경로의 존재를 드러내지 않도록 `404 NOT_FOUND` 로 답한다. 토큰 비교는 상수 시간 비교를 쓴다. 서버 토큰이 32자 미만이면 설정되지 않은 것으로 본다.

```ts
{
  status: "ok" | "degraded" | "idle";
  now: Timestamp;
  collector: {
    inWindow: boolean;
    lastSuccessAt: Timestamp | null;
    today: {
      date: string;                  // "YYYY-MM-DD"
      isHoliday: boolean;
      calls: Record<"location" | "arrival", number>;
      failures: Record<"location" | "arrival", number>;
      quotaExceeded: boolean;
      throttled: boolean;            // 호출량 초과로 5분 간격 시험 호출 중
    };
    targets: {
      name: string;                  // 예: "G1300", "덕현초교"
      intervalSec: number;
      calls: number;
      failures: number;
      empty: number;                 // 빈 응답(운행 없음)
      skippedCycles: number;
      lastSuccessAt: Timestamp | null;
    }[];
  };
}
```

### 4.3 GET /api/v1/stations, GET /api/v1/stations/{stationId}/routes
v1 초안과 같다(F01). 지금은 덕현초교 잠실행(235000392), G1300·1306, 목적지 잠실 하나다.

```ts
// GET /stations
{ items: { stationId: StationId; name: string; directionLabel: string; mobileNo: string | null }[] }

// GET /stations/{stationId}/routes
{
  stationId: StationId;
  destinations: {
    destinationId: DestinationId;   // "jamsil"
    name: string;                   // "잠실"
    routes: { routeId: RouteId; routeName: string; alightStationId: StationId; alightStationName: string }[];
  }[];
}
```
에러: 404(정류장 없음), 400(ID 형식)

### 4.4 GET /api/v1/snapshot
현재 버스 정보(F02), 도착 시 0석 위험(F03), 대안 비교(F04)를 **같은 계산 시점**으로 한 번에 돌려준다. 서버는 이 결과를 예보 스냅샷으로 저장한다(F07). 캐시와 다음 조회 시각은 1.3절.

요청 (Query)
```ts
station: StationId;           // 필수. "235000392"
destination: DestinationId;   // 필수. "jamsil"
deadline?: TimeOfDay;         // 선택. 목적지 도착 마감. 없으면 마감 판정 없이 추천한다(10장 미정)
```

응답 `200`
```ts
{
  snapshotId: SnapshotId;
  computedAt: Timestamp;            // 계산 시점. 화면에 항상 보이고, 설명 API 도 이 시점을 쓴다
  nextRefreshAt: Timestamp;         // 다음 조회 권장 시각(1.3절)
  rulesVersion: string;             // 계산에 쓴 규칙 버전(선행시간 선택·사례 검색·대안 규칙)
  dataUpdatedAt: Timestamp | null;  // 계산에 쓴 수집 데이터의 마지막 갱신 시각
  stale: boolean;                   // 수집 데이터가 오래됨(기준 설정값, 초기 60초)

  service: {
    state: "in_service" | "outside_collection" | "outside_forecast_hours";
    message: string | null;         // in_service 가 아니면 "지금은 예보 시간이 아니에요"
    nextForecastStartAt: Timestamp | null;  // in_service 가 아니면 다음 예보 시작 시각
  };

  station: { stationId: StationId; name: string; directionLabel: string };
  destination: { destinationId: DestinationId; name: string };
  deadline: TimeOfDay | null;
  walkMinutesAllowed: number;       // 허용 보행시간(분). 지금 0

  buses: Bus[];                     // 도착 예정 순. service.state 가 outside_collection 이면 빈 배열
  alternatives: Alternatives;
}

interface Bus {
  routeId: RouteId;
  routeName: string;
  vehicleId: VehicleId | null;
  plateNo: string | null;
  source: "arrival_1st" | "arrival_2nd" | "timetable_next";  // 후보 출처(고정 규칙)
  stationArrivalAt: Timestamp | null;   // 내 정류장 도착 = 데이터 시각 + 도착 예상
  arrivalEstimateSource: "predict_time_sec" | "predict_time_min" | "timetable" | null;
  // predict_time_sec: GBIS predictTimeSec(초). predict_time_min: 초 값이 없어 predictTime(분)×60 으로 대신함(정밀도 낮음, 임시 승인). timetable: 시간표상 다음 차
  // 이 값은 예보 스냅샷에도 저장해, 분 값으로 대신한 예보를 평가에서 따로 집계한다
  minutesToArrival: number | null;
  inForecastHours: boolean;             // 도착이 06:00~08:59 이면 true
  currentSeats: number | null;          // 지금 잔여석(-1·없음이면 null)
  seatsUpdatedAt: Timestamp | null;

  selectedLeadTimeMin: LeadTimeMin | null;  // 지금 화면에 쓸 선행시간(서버 선택, 2.1절)
  forecasts: Forecast[];                    // 선행시간 5·10·15분 각각 1개(항상 3개)
}

interface Forecast {
  leadTimeMin: LeadTimeMin;
  status: ForecastStatus;             // 5장
  issuedAt: Timestamp | null;         // 예보를 낸 시각(버스가 도착 L분 전이 된 시점). not_yet 이면 null
  noSeatProbability: number | null;   // 도착 시 0석 확률 = k/n (0–1, 보정 없음). status 가 ok 일 때만
  // 값은 round(k/n, 4)(DB numeric(5,4) 와 같음). riskLevel 은 반올림 전 k/n 으로 정한다
  n: number | null;                   // 사용한 사례 수
  k: number | null;                   // 그중 도착 시 0석이었던 사례 수 → 화면 "n회 중 k회"
  riskLevel: RiskLevel | null;        // noSeatProbability 가 있을 때만
  preliminary: boolean;               // 공개 기준 판정 전이면 true → 화면에 반드시 "예비"
  inputs: {                           // 예보를 낸 시점에 서버가 계산한 사례 검색 입력값
    seats: number | null;
    headwayMin: number | null;
  };
}

interface Alternatives {
  status: AlternativeStatus;          // 5장, 결정 규칙은 6장
  recommended: {
    routeId: RouteId;
    vehicleId: VehicleId | null;
    reasonCode: "lowest_risk" | "earliest_among_equal_risk";
  } | null;
  switchSuggested: boolean;           // 지금 차(도착 1순위)가 '위험 높음'이고 마감 안의 더 낮은 위험 대안이 있으면 true
  candidates: {
    routeId: RouteId;
    routeName: string;
    vehicleId: VehicleId | null;
    source: Bus["source"];
    stationArrivalAt: Timestamp | null;
    destinationArrivalAt: Timestamp | null;  // 내 정류장 도착 + 구간 소요 90백분위. 소요 기록 10회 미만이면 null
    meetsDeadline: boolean | null;           // deadline 이 없거나 destinationArrivalAt 이 null 이면 null
    leadTimeMin: LeadTimeMin | null;         // 그 후보 버스의 selectedLeadTimeMin
    noSeatProbability: number | null;        // 그 선행시간 예보의 0석 확률. 없으면 null(6장 처리)
    riskLevel: RiskLevel | null;
  }[];
}
```
에러: 400(필수 누락, 형식), 404(정류장·목적지 없음)

### 4.5 GET /api/v1/snapshot/{snapshotId}/explanation
스냅샷에 대한 짧은 설명(F08). **뼈대 단계에서는 항상 고정 문구**다.

- 대기 방식: 화면은 `/snapshot` 결과를 먼저 그린 뒤 이 API 를 부른다. 서버는 LLM 을 최대 3초 기다리고(고정 규칙), 넘으면 고정 문구로 즉시 답한다. 응답 시간 상한은 약 3.5초다. 같은 스냅샷의 설명은 서버가 캐시해 LLM 을 한 번만 부른다.
- 설명의 숫자는 각 버스의 `selectedLeadTimeMin` 예보 값이고, 시점은 스냅샷의 `computedAt` 이다.

응답 `200`
```ts
{
  snapshotId: SnapshotId;
  computedAt: Timestamp;            // 설명이 기준으로 삼은 스냅샷 시점(화면 시점과 같아야 함)
  source: "llm" | "fallback";
  fallbackReason: null | "llm_disabled" | "timeout" | "check_failed" | "daily_limit" | "error";
  // check_failed: 출력 검사 5종 중 하나 실패. llm_disabled: 뼈대 단계·설정으로 꺼 둠
  text: string;                     // 3문장 이내. 숫자는 스냅샷 값과 같다
}
```
에러: 404(스냅샷 없음 또는 만료), 429

### 4.6 POST /api/v1/parse-query
자연어 질문을 조회 조건으로 바꾼다(F08). 결과는 조건만 돌려주고, 실제 조회는 클라이언트가 `/snapshot` 으로 한다. **뼈대 단계에서는 항상 `status: "unavailable"`, `fallbackReason: "llm_disabled"`** 이다.

요청 (Body): `{ text: string }`. 1–200자. 원문은 저장하지 않는다(HMAC 만 로그). 요청 본문이 4096바이트를 넘으면 본문을 읽기 전에 400 이다.

응답 `200`
```ts
{
  status: "parsed" | "need_more" | "unavailable";
  fallbackReason: null | "llm_disabled" | "timeout" | "check_failed" | "daily_limit" | "error";
  query: { station: StationId | null; destination: DestinationId | null; deadline: TimeOfDay | null };
  message: string;
}
```
에러: 400(빈 문자열·200자 초과), 429

### 4.8 GET /api/v1/routes/{routeId}/positions
F02 '현재 버스 정보'를 지도로 보여 주기 위한 응답이다. 노선의 정류장 좌표(기준정보)와 **가장 최근에 수집한** 그 노선의 차량 위치·잔여석을 준다. GBIS 를 호출하지 않고 수집 기록만 읽는다. 과거 시각을 지정하는 파라미터는 없다(2.2절).

- 지원 노선: 정류장 좌표 기준정보가 있는 노선. 지금은 G1300(`235000092`), 1306(`235000123`). 그 밖은 404.
- 캐시: 10초(1.3절과 같은 값). 호출 제한: '나머지 GET'(분당 60회).
- 프론트는 `nextRefreshAt` 에 맞춰 다시 조회한다(최소 10초).

```ts
{
  routeId: RouteId;
  routeName: string;                 // "G1300"
  targetStationId: StationId;        // 강조할 내 정류장. 지금은 덕현초교 잠실행 "235000392"
  computedAt: Timestamp;             // 응답을 만든 시각
  dataUpdatedAt: Timestamp | null;   // 사용한 위치 기록의 수집 시각(collected_at). 기록이 없으면 null
  stale: boolean;                    // 수집 시간 안인데 dataUpdatedAt 이 '정보 오래됨' 기준(초기값 60초)보다 오래됨. 수집 시간 밖이면 항상 false
  inCollectionWindow: boolean;       // 지금이 수집 시간(평일 05:30~10:15) 안인지
  nextRefreshAt: Timestamp;          // 다음 조회 권장 시각(1.3절 규칙, 수집 시간 밖이면 다음 수집 시작)
  stations: {                        // stationSeq 오름차순
    stationSeq: number;
    stationId: StationId;
    name: string;
    lat: number;                     // WGS84 위도(GBIS y)
    lng: number;                     // WGS84 경도(GBIS x)
    isOutbound: boolean;             // 잠실행 구간(stationSeq ≤ 회차 순번)이면 true, 회차 뒤 귀로면 false
    isTarget: boolean;               // stationId == targetStationId
  }[];
  vehicles: {                        // 최신 위치 기록의 차량. 수집 시간 밖이면 마지막 기록 그대로
    vehicleId: VehicleId;
    plateNo: string | null;
    stationSeq: number;              // GBIS 가 준 현재 정류장 순번
    stationId: StationId;
    state: "arrived" | "departed" | "passing" | "unknown";  // GBIS stateCd 1 도착, 2 출발, 0 교차로 통과, 그 밖 unknown
    remainSeats: number | null;      // 잔여석. GBIS 값이 -1·빈값·음수면 null('정보 없음', 0석 아님)
    stopsToTarget: number | null;    // 내 정류장까지 남은 정류장 수(잠실행 구간에서 내 정류장 앞에 있을 때만, 아니면 null).
                                     // 내 정류장에 도착·통과 중이면 0, 내 정류장에서 '출발'이면 이미 떠났으므로 null
  }[];
}
```
에러: 404(지원하지 않는 노선), 400(ID 형식)

- 위치 기록은 오늘 수집 파일에서 그 노선의 마지막 성공 기록을 쓴다. 오늘 기록이 없으면 최대 7일 전까지 거슬러 간다(설정). 수집 시간 안에 이전 날 기록을 보여 주면 `stale=true` 다.
- 시운전(`mode: trial`) 기록도 지도에는 쓴다. 지도는 통계를 내지 않으므로 '사례·평가에서 뺀다'는 규칙과 별개다(설정 하나로 끌 수 있음).
- `nextRefreshAt` 은 1.3절과 같은 방식(다음 수집 경계 + 3초)이되, 경계는 **그 노선의 수집 주기**로 잡는다(G1300 10초, 1306 30초). 1306 을 10초마다 다시 받아도 같은 데이터이기 때문이다.
- `stopsToTarget` 은 내 정류장 순번에서 상태가 `unknown` 이어도 0 이다(떠났다는 근거가 `departed` 뿐이므로).
- 정류장 좌표가 같은 노선 안에서 두 번 나오면(회차 전후 같은 정류장) 순번이 다르므로 그대로 둘 다 준다.
- `remainSeats: null` 과 `0` 은 다르다. 화면은 null 을 "정보 없음"으로, 0 을 "0석"으로 쓴다.
- `plateNo` 는 노선버스(법인 차량) 번호판으로 GBIS 공개 데이터 그대로다. 이용자가 탈 차를 알아보는 데 쓴다. 이용자 개인정보는 응답에 없다.

### 4.7 추후 추가 (모양만, 구현은 다음 단계)
```ts
// GET /api/v1/notices?station=235000392   (F09) 팀원이 승인한 공지만
{ items: { noticeId: string; title: string; summary: string; effectiveFrom: Timestamp | null; effectiveTo: Timestamp | null; sourceUrl: string; approvedAt: Timestamp }[] }

// GET /api/v1/reports/no-seat?station=235000392&from=YYYY-MM-DD&to=YYYY-MM-DD   (F10)
{ station: {...}; items: { routeId: RouteId; timeBin: TimeOfDay; trips: number; noSeatTrips: number; unconfirmedTrips: number; grade: "strict" | "relaxed" }[] }

// GET /api/v1/reports/validation   (F10) 선행시간별 공개 기준 판정
{ items: { leadTimeMin: LeadTimeMin; fieldMatchRate: number | null; calibrationError: number | null; alertRecall: number | null; alertPrecision: number | null; brier: number | null; brierBaseline: number | null; passed: boolean | null }[] }
```
공지 등록·승인(관리용 쓰기 API)은 공개 API 에 두지 않는다(방식 미정).

## 5. 상태값

### 예보 상태 `ForecastStatus` (선행시간별)
| 값 | 이름 | 뜻 | 화면 |
|---|---|---|---|
| `ok` | 제공 | 사례 20건 이상, 확률 제공 | 위험 등급 + "n회 중 k회" (+ 판정 전이면 "예비") |
| `not_yet` | 아직 시점 전 | 버스가 아직 도착 L분 전에 이르지 않음 | 이 선행시간은 표시하지 않음 |
| `insufficient_cases` | 사례 부족 | 사례 20건 미만 | 확률 없이 현재 잔여석만 |
| `not_validated` | 검증 미통과 | 공개 기준 판정에서 통과 못 한 선행시간 | 확률 없이 현재 잔여석만 |
| `stale` | 정보 오래됨 | 예보 시점의 수집 데이터가 오래되어 계산하지 않음 | 갱신 시각과 안내 |
| `missing_input` | 입력 누락 | 그 시점 잔여석·앞차 간격을 계산할 수 없음 | 현재 잔여석만 |
| `outside_hours` | 예보 시간 아님 | 도착이 06~08시대 밖이라 예보하지 않음(확인 필요) | "예보 시간이 아니에요" |

### 대안 상태 `AlternativeStatus`
| 값 | 이름 | 뜻 |
|---|---|---|
| `recommended` | 추천 | 마감 안 후보 중 0석 위험이 가장 낮은 차(같으면 먼저 오는 차) |
| `no_alternative` | 대안 없음 | 판단할 정보는 있지만 마감 안에 드는 후보가 없음 |
| `arrival_unavailable` | 도착시각 미제공 | 구간 소요 기록이 10회 미만이라 목적지 도착 시각을 낼 수 없음 |
| `undecidable` | 판단 불가 | 후보·위험 정보가 부족해 판단할 수 없음 |

## 6. 대안 결정 규칙
위에서부터 차례로 검사하고, 처음 맞는 상태를 쓴다. 값은 고정 규칙(CLAUDE.md '도착 시각과 대안 추천')을 따른다.

1. 후보가 없다 → `undecidable`
2. `deadline` 이 있는데 모든 후보의 `destinationArrivalAt` 이 null 이다 → `arrival_unavailable`
3. `deadline` 이 있고 **목적지 도착 시각이 있는 후보가 모두 마감을 넘고**, 도착 시각을 모르는 후보도 없다 → `no_alternative`
4. 마감 안 후보(`deadline` 이 없으면 모든 후보) 중 `noSeatProbability` 가 있는 후보가 1개 이상 → `recommended`. 0석 확률이 가장 낮은 차, 같으면 먼저 오는 차
5. 그 밖(마감 안 후보는 있지만 확률이 모두 없음, 또는 도착 시각을 모르는 후보가 섞여 마감 판정을 끝낼 수 없음) → `undecidable`

- **확률이 없는 후보**(`not_yet`·`insufficient_cases`·`not_validated` 등)는 추천 대상에서 빼지만 `candidates` 목록에는 남겨 화면에 보여 준다. "위험이 낮다"로 간주하지 않는다.
- **`switchSuggested`**: 도착 1순위 버스의 `riskLevel` 이 `high` 이고, `recommended` 가 그 버스가 아니며 추천 차의 위험이 더 낮을 때 `true`.

## 7. 화면 요구 (영상 녹화 기준)
본선 시연은 평일 아침 실제 화면 녹화 영상으로 한다. 프론트는 아래를 지킨다.

- 기준 폭 360px. 조건 입력 → 예보 → 대안 비교 → 근거 부족 순으로 **페이지 새로고침 없이** 넘어간다(SPA). 화면 전환 중 빈 화면이 없도록 이전 내용을 유지한 채 갱신한다.
- 모든 예보 화면 상단에 **계산 시점(`computedAt`)** 과, 확률이 보이는 곳마다 **"예비"** 표기(`preliminary: true`)를 항상 보여 준다.
- `nextRefreshAt` 에 맞춰 자동으로 다시 조회한다. 조회 중에도 이전 숫자를 지우지 않는다.
- 위험은 색만으로 구분하지 않고 "위험 높음/보통/낮음" 문구와 "n회 중 k회"를 함께 쓴다.
- `service.state` 가 `in_service` 가 아니면 오류 화면이 아니라 "지금은 예보 시간이 아니에요" + 다음 예보 시작 시각을 보여 준다.
- 설명(LLM) 영역은 숫자보다 나중에 채워지며, 고정 문구일 때도 자리 높이가 바뀌지 않게 한다.

## 8. 화면과의 대응
| 화면 | 쓰는 API |
|---|---|
| 조건 입력 | `/stations`, `/stations/{id}/routes`, (선택) `/parse-query` |
| 예보 | `/snapshot` 의 `buses[].selectedLeadTimeMin` 예보 → 이어서 `/snapshot/{id}/explanation` |
| 예보(지도) | `/routes/{routeId}/positions` (스냅샷 노선마다 1회, `/snapshot` 과 별도로 갱신). 수집 시간 밖이라 스냅샷 `buses` 가 비면 `/stations/{id}/routes` 의 같은 목적지 노선을 쓴다 |
| 대안 비교 | `/snapshot` 의 `alternatives` (`switchSuggested` 이면 강조) |
| 근거 부족 | 선택된 예보의 `status` 가 `ok` 가 아님, 또는 `stale`, 또는 대안 상태가 `recommended` 가 아님 |
| 예보 시간 아님 | `service.state` 가 `in_service` 가 아님 |

## 9. 예시 응답 (`GET /snapshot`, 예보 시간 중)
```json
{
  "snapshotId": "0d6f3c1e-8a52-4f0b-9c2a-5d1f0e7b2a11",
  "computedAt": "2026-10-07T07:31:20+09:00",
  "nextRefreshAt": "2026-10-07T07:31:33+09:00",
  "rulesVersion": "2026-10-06.1",
  "dataUpdatedAt": "2026-10-07T07:31:10+09:00",
  "stale": false,
  "service": { "state": "in_service", "message": null, "nextForecastStartAt": null },
  "station": { "stationId": "235000392", "name": "덕현초교.덕고개", "directionLabel": "잠실행" },
  "destination": { "destinationId": "jamsil", "name": "잠실" },
  "deadline": "08:30",
  "walkMinutesAllowed": 0,
  "buses": [
    {
      "routeId": "235000092", "routeName": "G1300", "vehicleId": "235000359", "plateNo": "경기76바8260",
      "source": "arrival_1st", "stationArrivalAt": "2026-10-07T07:38:40+09:00",
      "arrivalEstimateSource": "predict_time_sec", "minutesToArrival": 7, "inForecastHours": true,
      "currentSeats": 3, "seatsUpdatedAt": "2026-10-07T07:31:10+09:00",
      "selectedLeadTimeMin": 10,
      "forecasts": [
        { "leadTimeMin": 15, "status": "ok", "issuedAt": "2026-10-07T07:23:40+09:00",
          "noSeatProbability": 0.6154, "n": 26, "k": 16, "riskLevel": "medium", "preliminary": true,
          "inputs": { "seats": 7, "headwayMin": 11 } },
        { "leadTimeMin": 10, "status": "ok", "issuedAt": "2026-10-07T07:28:40+09:00",
          "noSeatProbability": 0.75, "n": 24, "k": 18, "riskLevel": "high", "preliminary": true,
          "inputs": { "seats": 4, "headwayMin": 11 } },
        { "leadTimeMin": 5, "status": "not_yet", "issuedAt": null,
          "noSeatProbability": null, "n": null, "k": null, "riskLevel": null, "preliminary": true,
          "inputs": { "seats": null, "headwayMin": null } }
      ]
    },
    {
      "routeId": "235000123", "routeName": "1306", "vehicleId": "235010110", "plateNo": "경기76바8309",
      "source": "arrival_2nd", "stationArrivalAt": "2026-10-07T07:42:00+09:00",
      "arrivalEstimateSource": "predict_time_sec", "minutesToArrival": 10, "inForecastHours": true,
      "currentSeats": 14, "seatsUpdatedAt": "2026-10-07T07:31:10+09:00",
      "selectedLeadTimeMin": 15,
      "forecasts": [
        { "leadTimeMin": 15, "status": "ok", "issuedAt": "2026-10-07T07:27:00+09:00",
          "noSeatProbability": 0.2, "n": 25, "k": 5, "riskLevel": "low", "preliminary": true,
          "inputs": { "seats": 15, "headwayMin": 14 } },
        { "leadTimeMin": 10, "status": "not_yet", "issuedAt": null,
          "noSeatProbability": null, "n": null, "k": null, "riskLevel": null, "preliminary": true,
          "inputs": { "seats": null, "headwayMin": null } },
        { "leadTimeMin": 5, "status": "not_yet", "issuedAt": null,
          "noSeatProbability": null, "n": null, "k": null, "riskLevel": null, "preliminary": true,
          "inputs": { "seats": null, "headwayMin": null } }
      ]
    }
  ],
  "alternatives": {
    "status": "recommended",
    "recommended": { "routeId": "235000123", "vehicleId": "235010110", "reasonCode": "lowest_risk" },
    "switchSuggested": true,
    "candidates": [
      { "routeId": "235000092", "routeName": "G1300", "vehicleId": "235000359", "source": "arrival_1st",
        "stationArrivalAt": "2026-10-07T07:38:40+09:00", "destinationArrivalAt": "2026-10-07T08:21:00+09:00",
        "meetsDeadline": true, "leadTimeMin": 10, "noSeatProbability": 0.75, "riskLevel": "high" },
      { "routeId": "235000123", "routeName": "1306", "vehicleId": "235010110", "source": "arrival_2nd",
        "stationArrivalAt": "2026-10-07T07:42:00+09:00", "destinationArrivalAt": "2026-10-07T08:27:00+09:00",
        "meetsDeadline": true, "leadTimeMin": 15, "noSeatProbability": 0.2, "riskLevel": "low" }
    ]
  }
}
```
수치는 형식을 보여 주는 예시이며 실제 결과가 아니다. 예보 시간 밖이면 `service` 가 `{ "state": "outside_collection", "message": "지금은 예보 시간이 아니에요", "nextForecastStartAt": "2026-10-08T05:45:00+09:00" }` 이고 `buses` 는 `[]`, 대안 상태는 `undecidable` 이다.

## 10. 미정 (10/7 회의 안건)
- **선행시간 선택 규칙**: 초기값 "이미 시점이 지난 예보 중 가장 최근". 회의에서 확정해 설정만 바꾼다.
- **분 단위 도착 예상 대체(임시 승인)**: 초 값이 없을 때 분×60 으로 대신하고 `arrivalEstimateSource: "predict_time_min"` 으로 표시한다. 이 표시는 예보 스냅샷에도 저장해 평가에서 따로 집계한다. 회의에서 확정한다.
- **서로 다른 선행시간의 확률을 대안 비교에 같이 써도 되는지**: 지금 규칙은 후보마다 자기 `selectedLeadTimeMin` 예보를 쓰므로, 예: G1300 10분 예보와 1306 15분 예보를 나란히 비교하게 된다.
- **대안 노선(1306) 확률을 검증 전에 어떻게 표시할지**: 공개 판정은 노선별로 따로 나온다. 1306 이 판정 전일 때 대안 비교에 "예비" 확률을 그대로 보일지, 숨길지.
- **`deadline` 필수 여부**: 지금 초안은 선택(없으면 마감 판정 없이 추천).
- **정보 오래됨 기준**: 초기값 60초.
- **호출 제한 값과 CORS 출처**: 배포 위치가 정해지면 확정.
- **정답 등급**: 엄격만 쓸지, 완화도 쓸지.

결정된 것(2026-10-06): `outside_hours` 상태 추가(상태값 7종), `/health` 공개·상세 분리와 상세의 토큰 헤더, 다음 예보 시작 = 다음 평일 05:45(설정의 공휴일 목록 건너뜀). 설정 공휴일에는 예보하지 않고 `service.state = "outside_collection"` 으로 답한다(수집기는 공휴일에도 수집하고 `is_holiday` 로 표시한다).

## 12. 개발·시연용 가짜 응답 (뼈대 단계)
실데이터 계산을 붙이기 전까지 백엔드는 가짜 응답을 준다. 설정 `FAKE_DATA=true` 일 때만 `GET /api/v1/snapshot` 에 개발용 쿼리 `scenario` 를 받는다. 운영(`FAKE_DATA=false`)에서는 이 쿼리를 무시한다.

| scenario | 재현하는 것 |
|---|---|
| (없음) 또는 `example` | 9장 예시 응답 그대로 |
| `status_ok`·`status_not_yet`·`status_insufficient_cases`·`status_not_validated`·`status_stale`·`status_missing_input`·`status_outside_hours` | 선택된 예보의 상태가 각 값인 경우 |
| `alt_recommended`·`alt_no_alternative`·`alt_arrival_unavailable`·`alt_undecidable` | 대안 상태가 각 값인 경우 |
| `service_outside_collection`·`service_outside_forecast_hours` | 예보 시간이 아닌 경우 |
| `published` | 공개 판정 통과 후(`preliminary=false`) |

허용 목록 밖의 값은 400 이다. 화면은 개발 중 같은 `scenario` 를 넘겨 각 상태를 그리고 캡처한다.

- 가짜 응답은 요청의 `deadline` 을 형식만 검증하고 무시한다. 마감은 scenario 마다 고정이다(대안 상태를 항상 같은 모양으로 재현하려고).
- 가짜 응답의 시각은 고정값이라 `nextRefreshAt` 이 이미 지난 시각일 수 있다. 클라이언트는 자동 재조회 간격을 최소 10초로 지킨다.

## 11. 변경 이력
| 날짜 | 변경 | 영향 |
|---|---|---|
| 2026-10-06 | 새 범위로 처음 작성(회의용 초안) | FE/BE |
| 2026-10-06 | v2: 선행시간(`not_yet`, `issuedAt`, `selectedLeadTimeMin`), 서비스 시간(`service`), 대안 결정 규칙·`switchSuggested`, 공지·리포트 추후 추가, 호출 제한·CORS, 캐시 10초·`nextRefreshAt`, 설명 대기·`fallbackReason`, `arrivalEstimateSource`, `rulesVersion`, `probability` → `noSeatProbability`, `/health` 공개·상세 분리, 화면 요구(영상 녹화), 예시 응답 | FE/BE |
| 2026-10-06 | v2 결정 반영: `outside_hours` 승인, 분 단위 대체 임시 승인·스냅샷 저장, `/health/detail` 토큰 헤더(`X-Health-Token`, 없거나 틀리면 404), 다음 예보 시작 05:45·공휴일 목록, 미정 2건 추가, 12장 개발용 `scenario` | FE/BE |
| 2026-10-06 | 구현 반영: `noSeatProbability` = round(k/n, 4)(9장 예시 0.62 → 0.6154), SnapshotId = `forecast_group.forecast_group_id`, 서비스 상태는 시계로 판정·수집 시간 밖 `stale=false`, 가짜 응답의 deadline 무시·재조회 최소 10초, `/health/detail` 문서 제외·토큰 32자 이상·은닉은 부분적, POST 본문 4096바이트 상한 | FE/BE |
| 2026-10-07 | 4.8 `GET /routes/{routeId}/positions` 추가(F02 지도: 정류장 좌표, 최신 차량 위치·잔여석, `remainSeats` null 은 '정보 없음'). 과거 시각 파라미터 없음 | FE/BE |
