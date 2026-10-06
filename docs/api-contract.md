# API 계약 (api-contract.md)

> 프론트엔드와 백엔드가 함께 따르는 단일 기준이다. 양쪽에 걸치는 작업은 **이 문서를 먼저 고친 뒤** 구현한다.
> backend-dev / frontend-dev 는 작업 전에 이 문서를 읽고, 임의로 바꾸지 않는다. 바꿀 필요가 있으면 메인에 보고한다.
> FastAPI 의 `/docs`(OpenAPI)가 필드 수준 명세 역할을 하며, 이 문서와 응답 모델(Pydantic)이 어긋나면 안 된다.

- 상태: **회의용 초안** (2026-10-06). 10/7 회의에서 확정한다. 정해지지 않은 항목은 맨 아래 "미정"에 있다.
- 기준 문서: `CLAUDE.md` 의 '바꾸지 않는 규칙', '기능'(F01~F10), 'API(초안)'

## 1. 공통 규칙

| 항목 | 규칙 |
|---|---|
| Base URL | `/api/v1` |
| 인증 | 없음(공개 조회). 위치·이름·전화번호를 받지 않는다. 정류장 단위로만 조회한다 |
| 형식 | JSON, UTF-8. 필드 이름은 camelCase |
| 시각(Timestamp) | ISO 8601, KST 오프셋 포함. 예: `2026-10-07T07:31:20+09:00` |
| 하루 중 시각(TimeOfDay) | KST `"HH:MM"`(두 자리 0 채움). 예: `"08:30"` |
| GBIS 호출 | **이용자 요청 경로에서 GBIS 를 호출하지 않는다.** 응답은 수집 데이터와 1분 캐시로만 만든다 |
| LLM | LLM 응답을 기다리지 않는다. 숫자·대안은 스냅샷 API 가 먼저 주고, 설명은 별도 API 로 뒤에 붙인다 |

### 공통 에러 형식
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
| 500 | `INTERNAL_ERROR` | 서버 내부 오류 (상세 비노출) |

데이터가 오래됐거나 사례가 부족한 경우는 **에러가 아니라 200 응답의 상태값**으로 알린다(5장).

## 2. 공통 타입

```ts
type Timestamp = string;   // ISO 8601 + "+09:00"
type TimeOfDay = string;   // "HH:MM" (KST)

type StationId = string;   // GBIS 정류소 ID, ASCII 숫자 1–20자리. 예: "235000392"(덕현초교 잠실행)
type RouteId = string;     // GBIS 노선 ID, ASCII 숫자 1–20자리. 예: "235000092"(G1300)
type VehicleId = string;   // GBIS vehId, ASCII 숫자
type DestinationId = string; // 자체 코드, ^[a-z_]{1,32}$. 예: "jamsil"
type SnapshotId = string;  // 서버가 만든 불투명 문자열(예: ULID). 클라이언트는 해석하지 않는다
// ID 형식이 틀리면 400 VALIDATION_FAILED, 형식은 맞지만 없으면 404 NOT_FOUND

type LeadTimeMin = 5 | 10 | 15;                 // 선행시간(분). 고정 규칙
type RiskLevel = "high" | "medium" | "low";     // high: p ≥ 0.7, low: p < 0.3, 그 사이 medium. 고정 규칙
// 화면 문구: high "위험 높음", medium "위험 보통", low "위험 낮음". 표현은 '무좌석 위험'
```

## 3. 엔드포인트 한눈에

| 메서드·경로 | 기능 | 화면 |
|---|---|---|
| `GET /api/v1/health` | 수집 상태 | (운영 확인) |
| `GET /api/v1/stations` | 출발 정류장 목록 | 조건 입력 |
| `GET /api/v1/stations/{stationId}/routes` | 정류장의 노선·목적지 | 조건 입력 |
| `GET /api/v1/snapshot` | 현재 버스·0석 위험·대안을 같은 계산 시점으로 한 번에 | 예보, 근거 부족 |
| `GET /api/v1/snapshot/{snapshotId}/explanation` | LLM 설명 또는 고정 문구 | 예보 |
| `POST /api/v1/parse-query` | 자연어 질문 → 조회 조건 | 조건 입력 |

## 4. 엔드포인트

### GET /api/v1/health
수집 상태를 보여 준다. 수집기 상태 파일(`data/collected/status.json`)을 읽으며 GBIS 를 호출하지 않는다.

응답 `200`
```ts
{
  status: "ok" | "degraded" | "idle";
  // ok: 수집 창 안이고 최근 수집이 정상
  // degraded: 수집 창 안인데 마지막 성공이 오래됐거나(대상 주기의 3배 초과) 실패가 이어짐
  // idle: 수집 창 밖(평일 05:30~10:15 외)이라 수집하지 않는 중
  now: Timestamp;
  collector: {
    inWindow: boolean;
    lastSuccessAt: Timestamp | null;
    today: {
      date: string;                  // "YYYY-MM-DD"
      isHoliday: boolean;
      calls: Record<"location" | "arrival", number>;     // 오늘 호출 수
      failures: Record<"location" | "arrival", number>;  // 오늘 실패 수
      quotaExceeded: boolean;        // 포털 하루 호출량 초과 응답을 받았는지
    };
    targets: {                       // 대상별(노선·정류장)
      name: string;                  // 예: "G1300", "덕현초교"
      intervalSec: number;
      calls: number;
      failures: number;
      skippedCycles: number;
      lastSuccessAt: Timestamp | null;
    }[];
  };
}
```

### GET /api/v1/stations
출발 정류장 목록(F01). 지금은 덕현초교(잠실행) 하나다.

응답 `200`
```ts
{
  items: {
    stationId: StationId;     // "235000392"
    name: string;             // "덕현초교.덕고개"
    directionLabel: string;   // "잠실행"
    mobileNo: string | null;  // 정류장 번호(5자리). 예: "39624"
  }[];
}
```

### GET /api/v1/stations/{stationId}/routes
정류장에서 탈 수 있는 예보 대상 노선과 목적지(F01). 지금은 G1300·1306, 목적지 잠실 하나다.

응답 `200`
```ts
{
  stationId: StationId;
  destinations: {
    destinationId: DestinationId;   // "jamsil"
    name: string;                   // "잠실"
    routes: {
      routeId: RouteId;
      routeName: string;            // "G1300"
      alightStationId: StationId;   // 하차 정류장. G1300 123000611, 1306 123000002
      alightStationName: string;
    }[];
  }[];
}
```
에러: 404 `NOT_FOUND`(정류장 없음), 400(ID 형식)

### GET /api/v1/snapshot
현재 버스 정보(F02), 도착 시 0석 위험(F03), 대안 비교(F04)를 **같은 계산 시점**으로 한 번에 돌려준다. 서버는 이 결과를 예보 스냅샷으로 저장한다(F07). 같은 조건이면 1분 동안 같은 스냅샷을 돌려준다(캐시).

요청 (Query)
```ts
station: StationId;           // 필수. "235000392"
destination: DestinationId;   // 필수. "jamsil"
deadline?: TimeOfDay;         // 선택. 목적지 도착 마감. 없으면 마감 판정을 생략하고(meetsDeadline=null) 모든 후보 중에서 추천한다(확인 필요: 7장)
```

응답 `200`
```ts
{
  snapshotId: SnapshotId;
  computedAt: Timestamp;        // 계산 시점. 설명 API 와 화면이 이 시점을 같이 쓴다
  dataUpdatedAt: Timestamp | null;  // 계산에 쓴 수집 데이터의 마지막 갱신 시각
  stale: boolean;               // 수집 데이터가 오래됨(기준은 미정. 예: 2분 초과)
  station: { stationId: StationId; name: string; directionLabel: string };
  destination: { destinationId: DestinationId; name: string };
  deadline: TimeOfDay | null;
  walkMinutesAllowed: number;   // 허용 보행시간(분). 지금 0

  buses: Bus[];                 // 도착 예정 순(stationArrivalAt 오름차순)
  alternatives: Alternatives;
}

interface Bus {
  routeId: RouteId;
  routeName: string;
  vehicleId: VehicleId | null;  // 도착 API 의 vehId1/2
  plateNo: string | null;
  source: "arrival_1st" | "arrival_2nd" | "timetable_next";  // 후보 출처(고정 규칙: 도착 API 첫·두 번째 차와 시간표상 다음 차)
  stationArrivalAt: Timestamp | null;     // 내 정류장 도착 = 계산 시점 + predictTimeSec
  minutesToArrival: number | null;
  currentSeats: number | null;  // 지금 잔여석(-1·없음이면 null)
  seatsUpdatedAt: Timestamp | null;

  forecasts: Forecast[];        // 선행시간 5·10·15분 각각 1개(항상 3개)
}

interface Forecast {
  leadTimeMin: LeadTimeMin;
  status: ForecastStatus;       // 5장
  probability: number | null;   // 0석 확률 = k/n (0–1, 보정 없음). status 가 "ok" 일 때만 값
  n: number | null;             // 사용한 사례 수
  k: number | null;             // 그중 도착 시 0석이었던 사례 수 → 화면 "n회 중 k회"
  riskLevel: RiskLevel | null;  // probability 가 있을 때만
  preliminary: boolean;         // 공개 기준 판정 전이면 true → 화면에 반드시 "예비" 표기
  inputs: {                     // 서버가 수집 데이터로 계산한 사례 검색 입력값(사용자가 넣지 않음)
    seats: number | null;       // 그 시점 잔여석
    headwayMin: number | null;  // 앞차 간격(분)
  };
}

interface Alternatives {
  status: AlternativeStatus;    // 5장
  recommended: {                // status 가 "recommended" 일 때만
    routeId: RouteId;
    vehicleId: VehicleId | null;
    reasonCode: "lowest_risk" | "earliest_among_equal_risk";
  } | null;
  candidates: {
    routeId: RouteId;
    routeName: string;
    vehicleId: VehicleId | null;
    source: Bus["source"];
    stationArrivalAt: Timestamp | null;
    destinationArrivalAt: Timestamp | null;  // 내 정류장 도착 + 구간 소요 90백분위. 소요 기록 10회 미만이면 null
    meetsDeadline: boolean | null;           // deadline 이 없거나 도착시각 미제공이면 null
    probability: number | null;              // 그 차의 0석 위험(화면에 쓰는 선행시간 기준)
    riskLevel: RiskLevel | null;
  }[];
}
```
에러: 400(필수 누락, 형식), 404(정류장·목적지 없음)

### GET /api/v1/snapshot/{snapshotId}/explanation
스냅샷에 대한 짧은 설명(F08). LLM 이 만들고, 검사 5종 중 하나라도 실패하거나 3초를 넘거나 하루 300회를 넘으면 고정 문구를 준다. **뼈대 단계에서는 항상 고정 문구**다.

응답 `200`
```ts
{
  snapshotId: SnapshotId;
  computedAt: Timestamp;            // 설명이 기준으로 삼은 스냅샷 시점(화면 시점과 같아야 함)
  source: "llm" | "fallback";
  text: string;                     // 3문장 이내. 숫자는 스냅샷 값과 같다
}
```
에러: 404(스냅샷 없음 또는 만료)

### POST /api/v1/parse-query
자연어 질문을 조회 조건으로 바꾼다(F08). 결과는 조건만 돌려주고, 실제 조회는 클라이언트가 `/snapshot` 으로 한다. **뼈대 단계에서는 항상 `status: "unavailable"`** 이다.

요청 (Body)
```ts
{ text: string }   // 1–200자. 저장하지 않는다(길이·해시만 로그)
```

응답 `200`
```ts
{
  status: "parsed" | "need_more" | "unavailable";
  // parsed: 조건을 모두 찾음 / need_more: 일부만 찾음 / unavailable: LLM 미사용·장애·상한
  query: {
    station: StationId | null;
    destination: DestinationId | null;
    deadline: TimeOfDay | null;
  };
  message: string;   // 화면 안내 문구(고정 문구 또는 검사 통과한 LLM 문구)
}
```
에러: 400(빈 문자열·200자 초과)

## 5. 상태값

### 예보 상태 `ForecastStatus` (선행시간별)
| 값 | CLAUDE.md 이름 | 뜻 | 화면 |
|---|---|---|---|
| `ok` | 제공 | 사례 20건 이상, 확률 제공 | 위험 등급 + "n회 중 k회" (+ 판정 전이면 "예비") |
| `insufficient_cases` | 사례 부족 | 사례 20건 미만 | 확률 없이 현재 잔여석만 |
| `not_validated` | 검증 미통과 | 공개 기준 판정에서 통과 못 한 선행시간 | 확률 없이 현재 잔여석만 |
| `stale` | 정보 오래됨 | 수집 데이터가 오래되어 계산하지 않음 | 갱신 시각과 안내 |
| `missing_input` | 입력 누락 | 그 시점 잔여석·앞차 간격을 계산할 수 없음 | 현재 잔여석만 |

### 대안 상태 `AlternativeStatus`
| 값 | CLAUDE.md 이름 | 뜻 |
|---|---|---|
| `recommended` | 추천 | 마감 안 후보 중 0석 위험이 가장 낮은 차(같으면 먼저 오는 차) |
| `no_alternative` | 대안 없음 | 판단할 정보는 있지만 마감 안에 드는 후보가 없음 |
| `arrival_unavailable` | 도착시각 미제공 | 구간 소요 기록이 10회 미만이라 목적지 도착 시각을 낼 수 없음 |
| `undecidable` | 판단 불가 | 후보·위험 정보가 부족해 판단할 수 없음 |

`no_alternative` 와 `undecidable` 은 반드시 구분한다(고정 규칙).

## 6. 화면과의 대응
| 화면 | 쓰는 API |
|---|---|
| 조건 입력 | `/stations`, `/stations/{id}/routes`, (선택) `/parse-query` |
| 예보 | `/snapshot` → 바로 그림, 이어서 `/snapshot/{id}/explanation` 을 붙임 |
| 근거 부족 | `/snapshot` 의 `forecasts[].status` 가 `ok` 가 아닌 경우, 또는 `stale`·대안 상태가 `recommended` 가 아닌 경우 |

## 7. 미정 (10/7 회의 안건)
- **선행시간 표시**: 도착까지 남은 시간이 5·10·15분 사이일 때 어느 `forecasts` 를 보여 줄지. 응답은 세 개를 모두 주고, 화면은 한 곳(설정)에서 고르게 한다. 대안의 `probability` 도 같은 규칙을 따른다.
- **정보 오래됨 기준**: `stale` 을 몇 분부터로 볼지(제안: 수집 데이터 2분 초과).
- **스냅샷 캐시·보관**: 같은 조건 1분 캐시, 스냅샷 보관 기간.
- **정답 등급**: 사례·평가에 엄격만 쓸지, 완화도 쓸지(라벨 리포트 결과 보고 결정).
- **마감 없이 조회할 때**: `deadline` 을 필수로 할지, 없으면 마감 판정 없이 모든 후보에서 추천할지(지금 초안은 후자).
- **목적지 확장**: 지금은 잠실 하나. 다른 목적지를 열지.

## 8. 변경 이력
| 날짜 | 변경 | 영향 |
|---|---|---|
| 2026-10-06 | 새 범위로 처음 작성(회의용 초안) | FE/BE |
