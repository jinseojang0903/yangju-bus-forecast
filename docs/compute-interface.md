# 계산 함수 입출력 (회의용 초안, 2026-10-06)

> 모델링 담당자와 백엔드가 10/7 회의에서 합의할 **사례 검색·예보·구간 소요시간 계산 함수의 입출력**이다.
> 코드는 `backend/app/forecast/interfaces.py` 에 시그니처·데이터 모양·설명만 있고 본문은 비어 있다(`NotImplementedError`).
> 이미 구현된 규칙 함수(선행시간 선택, 위험 등급, 대안 결정, 서비스 시간)는 맨 아래 5장에 정리했다.

## 0. 이름 기준

- 계산 함수의 입출력 필드 이름은 **DB 스키마 v2 의 열 이름(snake_case)** 을 따른다
  (`case_feature`, `eval_forecast`, `forecast_snapshot`, `forecast_group`, `trip_label`, `publish_decision`).
- 스키마 v2 는 다른 작업자가 같은 시기에 고치고 있다(별도 PR). **열 이름이 바뀌면 스키마 쪽 이름으로 맞춘다.**
- API 응답 필드 이름은 `docs/api-contract.md` 그대로 둔다. DB·계산 이름과 API 이름의 변환은 저장소 계층(`backend/app/repositories/`)에서 한다(6장 대응표).
- '바꾸지 않는 규칙'의 숫자(선행시간 5·10·15분, 허용폭 ±2석·±3분, 최대 30건, 표시 하한 20건, 위험 0.7·0.3, 소요 기록 10회 등)는 함수 안에 다시 쓰지 않고 `settings.FORECAST_RULES`·`settings.ARRIVAL_RULES` 로 넘긴다.

## 1. 함수 목록

| 함수 | 입력 | 출력 | 하는 일 |
|---|---|---|---|
| `compute_case_features` | `trip`(운행편), `target_station`(목표 정류장·순번), `lead_time_min`, `reference_basis`, `rule_version` | `CaseFeature`(case_feature 한 행) | 운행편이 목표 정류장 도착 L분 전일 때의 잔여석·앞차 간격 |
| `search_cases` | `feature`(CaseFeature), `history`(LabeledCase 목록), `rules`(ForecastRules) | `CaseSearchResult(n, k, case_trip_keys, status)` | 비슷한 지난 사례를 찾아 n·k 를 센다 |
| `forecast_bus` | `bus_state`(BusState), `now`, `rules`, (키워드) `history`, `publish_decisions` | `list[Forecast]`(선행시간마다 1개, 항상 3개) | 버스 한 대의 선행시간별 예보 |
| `segment_p90` | `route`(routeId), `time_bin`("HH:MM" 30분대), `history`(SegmentTimeRecord 목록), `rules`(ArrivalRules) | 초(`int`) 또는 `None` | 구간 소요시간 90백분위. 기록 10회 미만이면 `None` |

`forecast_bus` 의 `history`·`publish_decisions` 는 처음 요청한 시그니처(`bus_state, now, rules`)에 키워드 인자로 더했다. 사례 검색 입력과 공개 판정 결과가 필요하기 때문이다.

## 2. 데이터 모양

### 2.1 CaseFeature (case_feature 한 행)

| 필드 | 타입 | 뜻 |
|---|---|---|
| trip_key | str | 운행편 키 `날짜_routeId_vehId_순번`(예: `2026-10-07_235000092_235000359_1`) |
| service_date | date | 운행 날짜(KST) |
| route_id | str | 노선 ID |
| station_id | str | 목표(내) 정류장 ID |
| lead_time_min | int | 선행시간(5·10·15) |
| reference_at | datetime \| None | 도착 L분 전 시점(KST). 도착 시각을 정할 수 없으면 None |
| reference_basis | `actual_arrival` \| `predicted_arrival` | 시점 기준. 지난 운행은 실제 도착, 지금 운행은 도착 예상 |
| seats | int \| None | 그 시점 잔여석(−1·없음이면 None) |
| headway_min | float \| None | 그 시점 앞차 간격(분) |
| computable | bool | 기준 시각·잔여석·앞차 간격을 모두 정했으면 true |
| uncomputable_reason | 아래 5종 \| None | computable 이 false 일 때만. 스키마 CHECK 값과 같다 |
| rule_version | str | 특징 계산 규칙 버전. 기본값 `settings.CASE_FEATURE_RULE_VERSION`(API rulesVersion 과 별개) |

uncomputable_reason(초안 코드, 회의에서 확정):

| 코드 | 뜻 |
|---|---|
| no_target_arrival | 목표 정류장 도착 기록 없음 |
| no_arrival_estimate | 도착 예상 없음 |
| no_record_near_reference | 기준 시각 근처 위치 기록 없음 |
| seat_unknown | 잔여석 −1·없음 |
| no_preceding_vehicle | 앞차 없음 |

### 2.2 LabeledCase (사례 후보 = case_feature + trip_label)

| 필드 | 타입 | 뜻 |
|---|---|---|
| feature | CaseFeature | 지난 운행의 특징(`reference_basis = actual_arrival`) |
| label | int \| None | 1 = 도착 상태 0석, 0 = 아님, None = 미확인 |
| label_grade | `strict` \| `relaxed` \| `unconfirmed` | 라벨 등급 |
| label_rule_version | str | 라벨 규칙 버전 |
| confirmed_at | datetime \| None | 정답 확정 시각(= 목표 정류장 도착 기록 시각) |

시운전·공휴일 운행은 호출하는 쪽이 미리 뺀다.

### 2.3 CaseSearchResult

| 필드 | 타입 | 뜻 |
|---|---|---|
| n | int | 사용한 사례 수 |
| k | int | 그중 도착 상태 0석 사례 수 |
| case_trip_keys | tuple[str] | 사용한 사례의 trip_key(가까운 순) |
| status | `ok` \| `insufficient_cases` \| `missing_input` | n < 20 이면 insufficient_cases, 특징 계산 불가면 missing_input |
| no_seat_probability (계산 속성) | float \| None | k ÷ n(보정 없음). ok 일 때만 |

### 2.4 BusState (예보할 버스 한 대, forecast_snapshot 의 버스 열)

| 필드 | 타입 | 뜻 |
|---|---|---|
| route_id | str | 노선 ID |
| veh_id | str \| None | 차량 ID(시간표상 다음 차는 None) |
| station_id | str | 목표(내) 정류장 ID |
| source | `arrival_1st` \| `arrival_2nd` \| `timetable_next` | 후보 출처 |
| station_arrival_at | datetime \| None | 내 정류장 도착 예상 |
| arrival_estimate_source | `predict_time_sec` \| `predict_time_min` \| `timetable` \| None | 도착 예상 출처 |
| current_seats | int \| None | 지금 잔여석 |
| seats_updated_at | datetime \| None | 잔여석 갱신 시각(stale 판정 근거) |
| trip | Trip \| None | 운행편 위치 기록(특징 계산 입력) |

### 2.5 Forecast (forecast_snapshot 의 선행시간 행, 버스 × 선행시간 3행 중 1행)

| 필드 | 타입 | 뜻 |
|---|---|---|
| lead_time_min | int | 선행시간 |
| status | 예보 상태 7종 | ok, not_yet, insufficient_cases, not_validated, stale, missing_input, outside_hours |
| issued_at | datetime \| None | 예보를 낸 시각(도착 L분 전). not_yet 이면 None |
| no_seat_probability | float \| None | k ÷ n. ok 일 때만 |
| n, k | int \| None | 사례 수, 0석 사례 수 |
| preliminary | bool | 공개 판정 전이면 true |
| input_seats | int \| None | 예보 시점 잔여석 |
| input_headway_min | float \| None | 예보 시점 앞차 간격 |
| is_selected | bool | 그 버스에서 화면에 쓸 선행시간이면 true(버스당 최대 1행) |
| case_trip_keys | tuple[str] | 사용한 사례 |

공개 판정(`publish_decision.decision_status`)에 따른 값: 행이 없거나 `pending` → `ok` + `preliminary=true`, `passed` → `preliminary=false`, `failed` → `status=not_validated`.
`forecast_bus` 에 넘기는 `publish_decisions` 는 호출하는 쪽이 이 버스의 노선·목표 정류장과 현재 `RULES_VERSION` 으로 이미 거른 값이다(키는 선행시간). 함수는 다시 확인하지 않는다.

`noSeatProbability` 는 round(k/n, 4)(DB numeric(5,4) 와 같음)이고, 위험 등급은 반올림 전 k/n 으로 정한다(`app/forecast/risk.py` 의 `no_seat_probability`).

### 2.6 EvalForecast (eval_forecast 한 행, 백테스트)

| 필드 | 타입 |
|---|---|
| trip_key, route_id, station_id | str |
| service_date | date |
| lead_time_min | int |
| issued_at | datetime \| None(= case_feature.reference_at, 기준 시각이 없으면 None) |
| status | 예보 상태 7종 |
| n, k | int \| None |
| no_seat_probability | float \| None |
| case_trip_keys | tuple[str] |
| label | int \| None |
| label_grade | `strict` \| `relaxed` \| `unconfirmed` \| None |
| rules_version, label_rule_version | str |

### 2.7 SegmentTimeRecord (segment_time 에서 쓰는 열)

| 필드 | 타입 |
|---|---|
| route_id | str |
| service_date | date |
| time_bin | str("HH:MM", 출발 시각의 30분대) |
| from_seq, to_seq | int |
| duration_sec | int |
| is_trial, is_holiday | bool |

## 3. 규칙 요약(함수 docstring 과 같음)

| 함수 | 규칙 |
|---|---|
| search_cases | 노선·목표 정류장·선행시간 일치, `confirmed_at < feature.reference_at`(같은 날 앞선 운행 포함), 정답 미확인 제외 → 허용폭 잔여석 ±2석·간격 ±3분 → 거리 \|잔여석 차\| ÷ 2 + \|간격 차\| ÷ 3 → 가까운 순 최대 30건, 운행당 1건, 거리가 같으면 최근 운행 우선 → n < 20 이면 insufficient_cases |
| forecast_bus | 도착 L분 전 시점 전이면 not_yet, 도착이 06:00~08:59 밖이면 outside_hours, 입력이 60초 넘게 오래됐으면 stale, 그 밖은 compute_case_features(predicted_arrival) → search_cases. `is_selected` 는 `select_lead_time` 결과 행. 한 번 낸 예보는 고정(issued_at) |
| segment_p90 | 같은 노선·평일·30분대, 최근 10평일, 시운전·공휴일 제외. 10회 미만이면 None(대안의 destinationArrivalAt = null) |

## 4. 합의할 것(회의 안건)

| 항목 | 내용 |
|---|---|
| 앞차 간격 | 같은 노선 앞차가 같은 정류장을 지난 시각과의 차로 볼지, 위치 기록의 순번 차로 볼지 |
| '그 시점' 기록 | reference_at 이전 마지막 기록을 쓸지, 가장 가까운 기록을 쓸지. 허용 시차 |
| uncomputable_reason 코드 | 스키마 초안 5종(2.1절)으로 충분한지 |
| 정답 등급 | strict 만 쓸지, relaxed 도 쓸지(계약 10장 미정과 같음) |
| 90백분위 계산 | 보간(선형) 여부, 같은 운행편 중복 처리 |
| 함수 시그니처 | forecast_bus 에 더한 `history`·`publish_decisions` 인자, compute_case_features 의 `rule_version` 인자(기본 `CASE_FEATURE_RULE_VERSION`) |

## 5. 이미 구현된 규칙 함수(백엔드, 순수 함수)

| 함수 | 위치 | 입력 → 출력 | 규칙 |
|---|---|---|---|
| select_lead_time | `app/forecast/lead_time.py` | 예보 목록, now → 5·10·15 또는 None | 설정 `LEAD_TIME_SELECTION_RULE="latest_issued"`: issued_at 이 있는 예보 중 가장 최근 |
| risk_level | `app/forecast/risk.py` | p → high·medium·low | p ≥ 0.7 high, p < 0.3 low |
| decide_alternatives | `app/forecast/alternatives.py` | 후보 목록, 마감 시각 → (상태, 추천(route_id, vehicle_id, source, reason_code), switchSuggested) | 계약 6장 1~5단계. 도착 1순위는 후보를 도착 예정 순으로 정렬해 고른다 |
| no_seat_probability | `app/forecast/risk.py` | k, n → (round(k/n, 4), 위험 등급) | 등급은 반올림 전 값 |
| service_state | `app/forecast/service_time.py` | now → (service.state, nextForecastStartAt) | 계약 2.2절. 평일 05:30~10:15 수집, 05:45~09:00 예보, 설정 공휴일(2026-10-09)은 outside_collection |
| next_refresh_at | `app/forecast/service_time.py` | now, data_updated_at → 시각 | 가장 짧은 수집 주기(10초)의 다음 경계 + 3초. 서비스 시간 밖이면 다음 예보 시작 |
| is_stale | `app/forecast/service_time.py` | now, data_updated_at → bool | 60초 초과 |

## 6. DB·계산 이름과 API 이름 대응표

| DB·계산(snake_case) | API(`docs/api-contract.md`) | 비고 |
|---|---|---|
| forecast_group.forecast_group_id | `snapshotId` | |
| forecast_group.station_id / destination_id | 요청 `station` / `destination`, 응답 `station.stationId` / `destination.destinationId` | |
| forecast_group.computed_at / next_refresh_at / rules_version | `computedAt` / `nextRefreshAt` / `rulesVersion` | |
| forecast_group.label_rule_version | (API 에 없음) | 사례 라벨에 쓴 trip_label.rule_version |
| forecast_group.data_updated_at / stale | `dataUpdatedAt` / `stale` | |
| forecast_group.service_state / next_forecast_start_at | `service.state` / `service.nextForecastStartAt` | `service.message` 는 DB 에 없고 서버가 붙인다 |
| forecast_group.deadline_at | `deadline`("HH:MM") | 시각 → 하루 중 시각 |
| forecast_group.alternative_status | `alternatives.status` | |
| forecast_group.recommended_route_id / recommended_veh_id | `alternatives.recommended.routeId` / `.vehicleId` | 계산 `Recommendation.route_id` / `.vehicle_id` |
| forecast_group.recommended_source | (API 에 없음) | 계산 `Recommendation.source`. 차량이 없는 후보를 forecast_snapshot 행과 맞춘다 |
| forecast_group.recommended_reason_code | `alternatives.recommended.reasonCode` | 계산 `Recommendation.reason_code` |
| forecast_group.switch_suggested / candidates | `alternatives.switchSuggested` / `alternatives.candidates` | |
| forecast_snapshot.veh_id | `buses[].vehicleId` | |
| forecast_snapshot.source / station_arrival_at / arrival_estimate_source | `buses[].source` / `.stationArrivalAt` / `.arrivalEstimateSource` | |
| forecast_snapshot.in_forecast_hours / current_seats / seats_updated_at | `buses[].inForecastHours` / `.currentSeats` / `.seatsUpdatedAt` | |
| forecast_snapshot.is_selected = true 인 행의 lead_time_min | `buses[].selectedLeadTimeMin` | true 인 행이 없으면 null |
| forecast_snapshot.lead_time_min / status / issued_at | `forecasts[].leadTimeMin` / `.status` / `.issuedAt` | |
| forecast_snapshot.no_seat_probability / n / k / preliminary | `forecasts[].noSeatProbability` / `.n` / `.k` / `.preliminary` | |
| (계산) risk_level(no_seat_probability) | `forecasts[].riskLevel` | DB 에 두지 않고 계산 |
| forecast_snapshot.input_seats / input_headway_min | `forecasts[].inputs.seats` / `.inputs.headwayMin` | |
| forecast_snapshot.case_trip_keys | (API 에 없음) | 근거 추적용 |
| (계산) 현재 시각 − station_arrival_at | `buses[].minutesToArrival` | 내림한 분 |
| route.route_name | `routeName` | 기준정보에서 붙인다 |
| station.station_name / mobile_no | `name` / `mobileNo` | |
