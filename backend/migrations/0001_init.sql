-- =============================================================================
-- 0001_init.sql : 첫 스키마 v2(회의용 초안, 2026-10-07 회의에서 확정)
-- 대상: Supabase(PostgreSQL 15 이상). anon·authenticated 역할이 있다고 가정한다
--       (일반 PostgreSQL 에서는 아래 REVOKE 가 역할이 없어 실패한다).
-- 아직 어느 DB 에도 적용하지 않았으므로 v2 변경은 0002 를 만들지 않고 이 파일에 반영했다.
--
-- 테이블 18개
--   기준정보   route, station, route_station, service_day
--   수집       raw_poll(본문 없음), bus_position, bus_arrival
--   라벨       trip, trip_label, segment_time
--   예보·평가  case_feature, eval_forecast, forecast_group, forecast_snapshot, publish_decision
--   LLM·현장   notice, llm_log, field_check
--
-- 적용 방법
--   Supabase 대시보드 > SQL Editor 에 이 파일 전체를 붙여 넣고 한 번 실행한다.
--   BEGIN/COMMIT 으로 감쌌으므로 중간에 하나라도 실패하면 아무것도 만들어지지 않는다.
--   같은 이름의 테이블이 이미 있으면 실패한다(IF NOT EXISTS 를 일부러 쓰지 않았다.
--   반쯤 다른 스키마 위에 조용히 덮어쓰지 않게 하려는 것이다).
--
-- 되돌리는 방법
--   backend/migrations/down/0001_down.sql (데이터가 모두 지워진다. 대상 DB 를 확인하고 실행한다)
--
-- 공통 규칙
--   - 시각은 timestamptz. 계산은 KST(Asia/Seoul)로 하고 저장은 시간대 있는 타입으로 한다.
--   - GBIS ID(routeId·stationId·vehId)는 text. API 가 숫자·문자열을 섞어 보내므로 문자열로 통일한다.
--   - 우리가 정한 상태·등급·사유 값은 enum 대신 text + CHECK. 회의에서 값이 바뀌어도
--     CHECK 하나만 고치면 된다.
--   - GBIS 가 보내는 값(stateCd, remainSeatCnt, crowded 등)에는 NOT NULL·CHECK 를 최소로 둔다.
--     예상 밖 값 때문에 적재가 실패하면 그 시간의 기록을 잃기 때문이다(원본은 JSONL 에 있다).
--   - '바꾸지 않는 규칙'의 숫자(선행시간 5·10·15, 사례 30건·20건, 허용폭 등)는 SQL 에 다시 쓰지
--     않는다. 기준은 backend/app/core/settings.py 다.
--   - 운행편은 trip_id 가 아니라 안정 키 trip_key(날짜_routeId_vehId_순번)로 가리킨다.
--     trip 을 다시 만들어도 같은 운행편이면 같은 값이라 근거 추적이 끊기지 않는다.
--
-- 보안(RLS·권한)
--   1) 모든 테이블에 ROW LEVEL SECURITY 를 켜고 정책(policy)은 만들지 않는다.
--      Supabase 는 public 스키마의 테이블을 공개 Data API(anon·authenticated 키)로 노출하는데,
--      RLS 가 켜져 있고 정책이 없으면 그 경로로는 어떤 행도 읽거나 쓸 수 없다.
--   2) 그것만으로는 부족하다. 정책 없는 RLS 는 TRUNCATE(RLS 대상이 아님), 뷰(기본은 뷰 소유자
--      권한으로 실행되어 RLS 를 우회), SECURITY DEFINER 함수를 막지 못한다. 그래서 파일 끝에서
--      anon·authenticated 의 테이블·시퀀스 권한을 회수하고 이후 만들 객체의 기본 권한도 회수한다.
--   3) 규칙: public 스키마에 뷰를 만들 때는 반드시 WITH (security_invoker = true) 로 만든다.
--      SECURITY DEFINER 함수는 만들지 않는다(필요하면 회의에서 따로 검토한다).
--   4) 백엔드는 DATABASE_URL 로 Postgres 에 직접 접속한다. 지금은 테이블 소유자(postgres)라
--      RLS 를 적용받지 않는다(FORCE ROW LEVEL SECURITY 를 쓰지 않는다). 소유자가 아닌 역할로
--      바꾸면 그 역할용 GRANT 와 정책이 없을 때 모든 조회가 0행이 된다(회의 안건: 접속 역할 분리).
--   5) raw_poll 에는 응답 본문·요청 파라미터·오류 문자열을 두지 않는다. 자유 문자열 열이 없고
--      ID·코드 열은 형식 CHECK 로 막으므로 서비스 키가 DB 에 들어갈 경로가 없다.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 기준정보: route, station, route_station
-- discover 결과(data/collected/reference/<날짜>/targets.json)와 노선 API 원본 기록,
-- settings.YANGJU_ROUTES 로 채운다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.route (
    route_id              text        PRIMARY KEY,
    route_name            text        NOT NULL,
    route_type_cd         text,
    route_type_name       text,
    admin_name            text,
    region_name           text,
    start_station_name    text,
    end_station_name      text,
    is_collected          boolean     NOT NULL DEFAULT false,
    is_label_target       boolean     NOT NULL DEFAULT false,
    is_reserved           boolean     NOT NULL DEFAULT false,
    is_night              boolean     NOT NULL DEFAULT false,
    collect_interval_sec  integer     CHECK (collect_interval_sec IS NULL OR collect_interval_sec > 0),
    note                  text,
    source_date           date,
    updated_at            timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE  public.route IS '노선 기준정보. 양주시 관할 광역 노선 목록(settings.YANGJU_ROUTES)과 노선 검색 결과로 채운다.';
COMMENT ON COLUMN public.route.route_id IS 'GBIS routeId(문자열).';
COMMENT ON COLUMN public.route.route_name IS '노선 번호·이름(예: G1300, P9601(출근)). settings 의 route_name 과 같게 둔다.';
COMMENT ON COLUMN public.route.admin_name IS '관할(노선 검색 결과의 adminName, 예: 경기도 양주시).';
COMMENT ON COLUMN public.route.is_collected IS '위치 API 수집 여부(settings TargetRoute.collect).';
COMMENT ON COLUMN public.route.is_label_target IS '운행편·라벨·DB 적재 대상(G1300·1306만 true).';
COMMENT ON COLUMN public.route.is_reserved IS '예약버스(P 노선). 라벨·사례에서 뺀다.';
COMMENT ON COLUMN public.route.is_night IS '심야 전용 노선.';
COMMENT ON COLUMN public.route.collect_interval_sec IS '위치 API 수집 주기(초). 수집하지 않으면 NULL.';
COMMENT ON COLUMN public.route.source_date IS '기준정보를 받은 날짜(reference/<날짜>).';

CREATE TABLE public.station (
    station_id    text             PRIMARY KEY,
    station_name  text             NOT NULL,
    mobile_no     text,
    region_name   text,
    x             double precision,
    y             double precision,
    source_date   date,
    updated_at    timestamptz      NOT NULL DEFAULT now()
);
COMMENT ON TABLE  public.station IS '정류장 기준정보. 노선별 정류장 목록(getBusRouteStationListv2)으로 채운다.';
COMMENT ON COLUMN public.station.station_id IS 'GBIS stationId(문자열). 같은 이름이라도 방향마다 ID 가 다르다(덕현초교 잠실행 235000392, 반대 235000409).';
COMMENT ON COLUMN public.station.mobile_no IS '정류장 번호(mobileNo, 예: 39624).';
COMMENT ON COLUMN public.station.x IS '경도(GBIS x).';
COMMENT ON COLUMN public.station.y IS '위도(GBIS y).';

CREATE TABLE public.route_station (
    route_id       text        NOT NULL REFERENCES public.route (route_id) ON DELETE CASCADE,
    station_seq    integer     NOT NULL CHECK (station_seq >= 1),
    station_id     text        NOT NULL REFERENCES public.station (station_id),
    is_turn_point  boolean     NOT NULL DEFAULT false,
    source_date    date,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (route_id, station_seq)
);
COMMENT ON TABLE  public.route_station IS '노선별 정류장 순서. 위치 기록의 stationSeq 를 정류장으로 바꿀 때 쓴다.';
COMMENT ON COLUMN public.route_station.station_seq IS '노선 안 정류장 순번(GBIS stationSeq, 1부터). G1300 덕현초교 13, 1306 덕현초교 11.';
COMMENT ON COLUMN public.route_station.is_turn_point IS '회차 지점(turnYn=Y). G1300 은 30(잠실광역환승센터), 1306 은 26.';
CREATE INDEX route_station_station_idx ON public.route_station (station_id);

-- -----------------------------------------------------------------------------
-- service_day: 운행일(날짜별 평일·공휴일·수집 운영 구분)
-- 사례·평가에서 '평일·정식 수집일'만 고를 때 쓴다. 다른 테이블은 FK 없이 날짜로 조인한다
-- (운행일 행을 늦게 넣어도 적재가 실패하지 않게).
-- -----------------------------------------------------------------------------
CREATE TABLE public.service_day (
    service_date    date        PRIMARY KEY,
    is_weekday      boolean     NOT NULL,
    is_holiday      boolean     NOT NULL,
    operation_kind  text        NOT NULL CHECK (operation_kind IN ('regular', 'trial', 'none')),
    note            text        CHECK (char_length(note) <= 200),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT service_day_weekday_chk CHECK (is_weekday = (extract(isodow FROM service_date) < 6))
);
COMMENT ON TABLE  public.service_day IS '운행일. 날짜별 평일·공휴일 여부와 수집 운영 구분. 사례·평가 대상 날짜를 고를 때 쓴다. 공휴일 판정의 기준은 설정 모듈의 공휴일 목록이다.';
COMMENT ON COLUMN public.service_day.service_date IS '날짜(KST).';
COMMENT ON COLUMN public.service_day.is_weekday IS '월~금이면 true. 요일과 맞지 않으면 CHECK 로 거부한다.';
COMMENT ON COLUMN public.service_day.is_holiday IS '공휴일(대체공휴일 포함). 설정 모듈의 공휴일 목록(초기값 2026-10-09)과 같은 정보다. 기준은 설정 모듈이고 이 열은 그 사본이므로 목록을 바꾸면 이 열도 함께 고친다. 공휴일에는 예보하지 않는다(API service.state = outside_collection). 수집기는 공휴일에도 수집하고 raw_poll.is_holiday 로 표시한다.';
COMMENT ON COLUMN public.service_day.operation_kind IS '수집 운영 구분: regular(정식 수집) | trial(시운전, 사례·평가에서 뺀다) | none(수집 없음).';
COMMENT ON COLUMN public.service_day.note IS '특이사항(200자 이하. 예: 수집 서버 재시작, 노선 우회 공지). 개인정보 금지: 승객·기사·팀원 개인을 알아볼 수 있는 내용(이름, 연락처, 외모)을 적지 않는다.';

-- -----------------------------------------------------------------------------
-- raw_poll: GBIS 호출 1건의 메타데이터(JSONL 한 줄 = 한 행). 응답 본문은 두지 않는다.
-- 본문의 기준은 JSONL(data/collected/<날짜>/raw_poll.jsonl)이다. DB 에는 위치 API 의
-- G1300·1306 과 도착 API(덕현초교)만 넣는다. 나머지 10개 노선은 JSONL 에만 남는다.
--
-- 재적재(같은 JSONL 을 몇 번 넣어도 결과가 같다)
--   1) raw_poll: INSERT ... ON CONFLICT (jsonl_file, line_no) DO NOTHING RETURNING raw_poll_id.
--      행이 돌아오지 않으면(이미 있음) 같은 키로 SELECT 해 raw_poll_id 를 얻는다.
--   2) bus_position: INSERT ... ON CONFLICT (raw_poll_id, item_index) DO NOTHING.
--   3) bus_arrival : INSERT ... ON CONFLICT (raw_poll_id, route_id, arrival_rank) DO NOTHING.
--
-- 보관 정리: raw_poll·bus_position·bus_arrival 은 같은 기간 보관하고 함께 지운다.
-- 자식(bus_position·bus_arrival)을 먼저 지우고 raw_poll 을 지운다(FK 는 ON DELETE RESTRICT).
-- -----------------------------------------------------------------------------
CREATE TABLE public.raw_poll (
    raw_poll_id    bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jsonl_file     text        NOT NULL
                               CHECK (jsonl_file ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}/[a-z0-9_]{1,64}\.jsonl$'),
    line_no        integer     NOT NULL CHECK (line_no >= 1),
    collected_at   timestamptz NOT NULL,
    api            text        NOT NULL CHECK (api IN ('getBusLocationListv2', 'getBusArrivalListv2')),
    route_id       text        CHECK (route_id ~ '^[0-9]{1,20}$'),
    station_id     text        CHECK (station_id ~ '^[0-9]{1,20}$'),
    ok             boolean     NOT NULL,
    http_status    integer,
    result_code    text        CHECK (result_code ~ '^[A-Za-z0-9_-]{1,64}$'),
    mode           text        NOT NULL CHECK (mode IN ('run', 'once', 'trial')),
    interval_sec   integer     CHECK (interval_sec IS NULL OR interval_sec > 0),
    is_holiday     boolean     NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT raw_poll_source_uq UNIQUE (jsonl_file, line_no),
    CONSTRAINT raw_poll_target_chk CHECK (
        (api = 'getBusLocationListv2' AND route_id IS NOT NULL AND station_id IS NULL)
        OR (api = 'getBusArrivalListv2' AND station_id IS NOT NULL AND route_id IS NULL)
    )
);
COMMENT ON TABLE  public.raw_poll IS 'GBIS 호출 메타데이터. JSONL(data/collected/<날짜>/raw_poll.jsonl) 한 줄이 한 행이다. 응답 본문·요청 파라미터·오류 문자열은 두지 않는다(본문은 JSONL 이 기준). DB 에는 위치 API 의 G1300·1306 과 도착 API(덕현초교)만 넣는다. 재적재는 (jsonl_file, line_no) 로 ON CONFLICT DO NOTHING.';
COMMENT ON COLUMN public.raw_poll.jsonl_file IS 'JSONL 파일의 상대 경로(수집 폴더 data/collected 기준, 날짜 폴더 포함). 예: 2026-10-07/raw_poll.jsonl. 경로 이동(..)·절대 경로는 CHECK 로 막는다.';
COMMENT ON COLUMN public.raw_poll.line_no IS 'JSONL 파일의 물리적 줄 번호(1부터, 빈 줄·깨진 줄 포함). 파일은 덧붙이기만 하므로 번호가 바뀌지 않는다. jsonl_file 과 함께 재적재 중복을 막는 키.';
COMMENT ON COLUMN public.raw_poll.collected_at IS '호출 시각(JSONL collected_at, KST 오프셋 포함).';
COMMENT ON COLUMN public.raw_poll.api IS '오퍼레이션 이름(getBusLocationListv2 | getBusArrivalListv2).';
COMMENT ON COLUMN public.raw_poll.route_id IS '호출 대상 노선(위치 API 의 params.routeId). 숫자만 허용한다.';
COMMENT ON COLUMN public.raw_poll.station_id IS '호출 대상 정류장(도착 API 의 params.stationId). 숫자만 허용한다.';
COMMENT ON COLUMN public.raw_poll.ok IS '수집기 판정 성공 여부. 결과 없음(차 없음, 빈 응답)도 성공이다.';
COMMENT ON COLUMN public.raw_poll.http_status IS 'HTTP 상태 코드. 연결 실패 등으로 응답이 없으면 NULL.';
COMMENT ON COLUMN public.raw_poll.result_code IS 'GBIS resultCode 또는 게이트웨이 사유 코드(영문·숫자·_- 64자 이하). 메시지 원문은 두지 않는다.';
COMMENT ON COLUMN public.raw_poll.mode IS '수집 방식: run(정식) | once(1회) | trial(시운전, 사례·평가에서 뺀다).';
COMMENT ON COLUMN public.raw_poll.interval_sec IS '그 대상의 수집 주기(초). once 등 주기 수집이 아니면 NULL.';
COMMENT ON COLUMN public.raw_poll.is_holiday IS '호출 날짜가 공휴일이면 true(JSONL is_holiday).';
COMMENT ON COLUMN public.raw_poll.loaded_at IS 'DB 에 넣은 시각.';
CREATE INDEX raw_poll_collected_at_idx ON public.raw_poll (collected_at);
CREATE INDEX raw_poll_route_collected_idx ON public.raw_poll (route_id, collected_at) WHERE route_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- bus_position: 위치 응답의 차량 항목 1개 = 한 행(G1300·1306 만)
-- 적재 실패로 행을 잃지 않게 GBIS 값 열은 NULL 을 허용한다. route FK 도 두지 않는다
-- (기준정보가 늦게 채워져도 적재가 실패하지 않게).
-- -----------------------------------------------------------------------------
CREATE TABLE public.bus_position (
    raw_poll_id      bigint      NOT NULL REFERENCES public.raw_poll (raw_poll_id) ON DELETE RESTRICT,
    item_index       smallint    NOT NULL CHECK (item_index >= 0),
    collected_at     timestamptz NOT NULL,
    route_id         text        NOT NULL,
    veh_id           text,
    plate_no         text,
    station_seq      integer,
    station_id       text,
    state_cd         smallint,
    remain_seat_cnt  integer,
    crowded          smallint,
    interval_sec     integer,
    mode             text,
    is_holiday       boolean,
    CONSTRAINT bus_position_pk PRIMARY KEY (raw_poll_id, item_index)
);
COMMENT ON TABLE  public.bus_position IS '위치 기록. 위치 응답(busLocationList)의 차량 1대 1회 관측이 한 행이다. 운행편 재구성·라벨·사례 특징·구간 소요시간의 입력. 키 (raw_poll_id, item_index) 로 재적재 중복을 막는다(ON CONFLICT DO NOTHING). raw_poll 과 같은 기간 보관하고 raw_poll 보다 먼저 지운다.';
COMMENT ON COLUMN public.bus_position.raw_poll_id IS '원본 호출(raw_poll). 필수. 이 행이 남아 있으면 raw_poll 행을 지울 수 없다(ON DELETE RESTRICT).';
COMMENT ON COLUMN public.bus_position.item_index IS '응답 목록 안 순서(0부터). raw_poll_id 와 함께 기본키.';
COMMENT ON COLUMN public.bus_position.collected_at IS 'raw_poll.collected_at 복사(조회용).';
COMMENT ON COLUMN public.bus_position.route_id IS 'GBIS routeId. route 를 가리키지만 적재 실패를 피하려고 FK 를 두지 않는다.';
COMMENT ON COLUMN public.bus_position.veh_id IS 'GBIS vehId. 없으면 운행편 재구성에서 뺀다.';
COMMENT ON COLUMN public.bus_position.station_seq IS 'GBIS stationSeq. 차량이 있는(지나는) 정류장 순번. 없으면 운행편 재구성에서 뺀다.';
COMMENT ON COLUMN public.bus_position.station_id IS 'GBIS stationId. 기준정보에 없는 정류장일 수 있어 FK 를 두지 않는다.';
COMMENT ON COLUMN public.bus_position.state_cd IS 'GBIS stateCd: 0 교차로 통과, 1 정류소 도착, 2 정류소 출발.';
COMMENT ON COLUMN public.bus_position.remain_seat_cnt IS 'GBIS remainSeatCnt. −1 은 정보 없음(라벨 미확인 사유 seat_unknown).';
COMMENT ON COLUMN public.bus_position.crowded IS 'GBIS crowded(혼잡도 코드). 원값 그대로.';
COMMENT ON COLUMN public.bus_position.interval_sec IS 'raw_poll.interval_sec 복사. 10·30·40·60초 기록이 섞일 수 있다.';
COMMENT ON COLUMN public.bus_position.mode IS 'raw_poll.mode 복사(run | once | trial). trial 은 사례·평가에서 뺀다.';
COMMENT ON COLUMN public.bus_position.is_holiday IS 'raw_poll.is_holiday 복사.';
CREATE INDEX bus_position_route_time_idx ON public.bus_position (route_id, collected_at);

-- -----------------------------------------------------------------------------
-- bus_arrival: 도착 응답의 노선 항목 × 순위(1·2번째 차) = 한 행(G1300·1306 만)
-- raw_poll 에 본문이 없으므로 도착 예상·후보 잔여석을 여기에 남긴다. API 서버가 수집 서버와
-- 다른 곳에 있어도 실시간 스냅샷과 도착 예상 평가의 근거가 된다(메인 제안, 회의에서 결정).
-- -----------------------------------------------------------------------------
CREATE TABLE public.bus_arrival (
    raw_poll_id       bigint      NOT NULL REFERENCES public.raw_poll (raw_poll_id) ON DELETE RESTRICT,
    route_id          text        NOT NULL,
    arrival_rank      smallint    NOT NULL CHECK (arrival_rank IN (1, 2)),
    item_index        smallint    NOT NULL CHECK (item_index >= 0),
    collected_at      timestamptz NOT NULL,
    station_id        text,
    veh_id            text,
    plate_no          text,
    predict_time_sec  integer,
    predict_time_min  integer,
    remain_seat_cnt   integer,
    location_no       integer,
    sta_order         integer,
    crowded           smallint,
    flag              text,
    CONSTRAINT bus_arrival_pk PRIMARY KEY (raw_poll_id, item_index, arrival_rank)
);
COMMENT ON TABLE  public.bus_arrival IS '도착 기록. 도착 응답(busArrivalList)의 노선 항목 하나를 순위(1·2)별로 나눠 한 행씩 둔다. 그 순위의 차량 정보(vehId·predictTime·plateNo 중 하나라도)가 없으면 행을 만들지 않는다. G1300·1306 항목만 넣는다. 실시간 스냅샷 입력과 도착 예상 평가에 쓴다. 메인 제안이며 회의에서 결정한다.';
COMMENT ON COLUMN public.bus_arrival.raw_poll_id IS '원본 호출(raw_poll, 도착 API). 필수. ON DELETE RESTRICT.';
COMMENT ON COLUMN public.bus_arrival.route_id IS 'GBIS routeId(항목의 routeId). 없으면 행을 만들지 않는다.';
COMMENT ON COLUMN public.bus_arrival.arrival_rank IS '순위: 1(첫 번째 차, 필드 끝 1) | 2(두 번째 차, 필드 끝 2). API Bus.source 의 arrival_1st·arrival_2nd 에 대응.';
COMMENT ON COLUMN public.bus_arrival.item_index IS '응답 목록 안 노선 항목 순서(0부터). JSONL 로 되짚어 갈 때 쓴다.';
COMMENT ON COLUMN public.bus_arrival.collected_at IS 'raw_poll.collected_at 복사(조회용).';
COMMENT ON COLUMN public.bus_arrival.station_id IS 'GBIS stationId(항목 값, 없으면 raw_poll.station_id).';
COMMENT ON COLUMN public.bus_arrival.veh_id IS 'GBIS vehId1/vehId2. 운행편(trip_key)과 맞출 때 쓴다.';
COMMENT ON COLUMN public.bus_arrival.plate_no IS 'GBIS plateNo1/plateNo2.';
COMMENT ON COLUMN public.bus_arrival.predict_time_sec IS 'GBIS predictTimeSec1/2(초). 내 정류장 도착 = collected_at + 이 값.';
COMMENT ON COLUMN public.bus_arrival.predict_time_min IS 'GBIS predictTime1/2(분). 초 단위 값이 없을 때 대신 쓴다.';
COMMENT ON COLUMN public.bus_arrival.remain_seat_cnt IS 'GBIS remainSeatCnt1/2. −1 은 정보 없음.';
COMMENT ON COLUMN public.bus_arrival.location_no IS 'GBIS locationNo1/2(몇 정류장 전).';
COMMENT ON COLUMN public.bus_arrival.sta_order IS 'GBIS staOrder(이 정류장의 노선 안 순번).';
COMMENT ON COLUMN public.bus_arrival.crowded IS 'GBIS crowded1/2(혼잡도 코드). 응답에 없으면 NULL.';
COMMENT ON COLUMN public.bus_arrival.flag IS 'GBIS flag(예: PASS·STOP·WAIT). 원값 그대로.';
CREATE INDEX bus_arrival_station_route_time_idx ON public.bus_arrival (station_id, route_id, collected_at);

-- -----------------------------------------------------------------------------
-- trip: 운행편(app/labeling/trips.py 의 Trip)
-- trip 을 다시 만들면(운행편 기준 변경 등) trip_label·segment_time 이 함께 지워진다(CASCADE).
-- 사례 특징·평가·예보 스냅샷·현장 대조는 trip_key 문자열로 운행편을 가리키므로 추적이 끊기지 않는다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.trip (
    trip_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_key       text        NOT NULL
                               CHECK (trip_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{1,20}_[0-9]{1,20}_[1-9][0-9]{0,3}$'),
    service_date   date        NOT NULL,
    route_id       text        NOT NULL REFERENCES public.route (route_id),
    veh_id         text        NOT NULL,
    trip_index     integer     NOT NULL CHECK (trip_index >= 1),
    plate_no       text,
    start_at       timestamptz NOT NULL,
    end_at         timestamptz NOT NULL,
    first_seq      integer     NOT NULL,
    last_seq       integer     NOT NULL,
    record_count   integer     NOT NULL CHECK (record_count >= 1),
    interval_secs  integer[]   NOT NULL DEFAULT '{}',
    is_trial       boolean     NOT NULL,
    is_holiday     boolean     NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT trip_key_uq UNIQUE (trip_key),
    CONSTRAINT trip_natural_uq UNIQUE (service_date, route_id, veh_id, trip_index),
    CONSTRAINT trip_time_chk CHECK (end_at >= start_at)
);
COMMENT ON TABLE  public.trip IS '운행편. 같은 날·노선·차량의 위치 기록을 시각 순으로 묶고, 30분 넘는 공백이나 순번 10 이상 감소(회차 뒤 재출발)에서 나눈다. 다시 만들면 trip_label·segment_time 이 함께 지워지고, 다른 테이블의 근거는 trip_key 로 남는다.';
COMMENT ON COLUMN public.trip.trip_key IS '운행편의 안정 키: 날짜_routeId_vehId_하루안순번(예: 2026-10-07_235000092_235000359_1). app/labeling 의 Trip.trip_key 와 같은 형식. 다시 만들어도 같은 운행편이면 같은 값이다.';
COMMENT ON COLUMN public.trip.service_date IS '수집 시각(KST)의 날짜.';
COMMENT ON COLUMN public.trip.trip_index IS '같은 날·노선·차량 안의 순번(1부터).';
COMMENT ON COLUMN public.trip.start_at IS '첫 위치 기록 시각.';
COMMENT ON COLUMN public.trip.end_at IS '마지막 위치 기록 시각.';
COMMENT ON COLUMN public.trip.first_seq IS '첫 기록의 정류장 순번.';
COMMENT ON COLUMN public.trip.last_seq IS '마지막 기록의 정류장 순번.';
COMMENT ON COLUMN public.trip.interval_secs IS '기록에 남은 수집 주기(초) 목록. 오름차순, 중복 없음.';
COMMENT ON COLUMN public.trip.is_trial IS '시운전 기록이 하나라도 있으면 true. 사례·평가에서 뺀다.';
COMMENT ON COLUMN public.trip.is_holiday IS '공휴일 운행. 사례·평가에서 뺀다.';
CREATE INDEX trip_route_date_idx ON public.trip (route_id, service_date);

-- -----------------------------------------------------------------------------
-- trip_label: 운행편 × 목표 정류장의 '도착 상태 0석' 정답
-- -----------------------------------------------------------------------------
CREATE TABLE public.trip_label (
    trip_label_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id        bigint      NOT NULL REFERENCES public.trip (trip_id) ON DELETE CASCADE,
    station_id     text        NOT NULL REFERENCES public.station (station_id),
    target_seq     integer     NOT NULL CHECK (target_seq >= 2),
    grade          text        NOT NULL CHECK (grade IN ('strict', 'relaxed', 'unconfirmed')),
    label          smallint    CHECK (label IN (0, 1)),
    last_seat      integer,
    evidence_at    timestamptz,
    confirmed_at   timestamptz,
    reason         text        CHECK (reason IN (
                                   'no_departure_or_moving_record',
                                   'seat_unknown',
                                   'passed_within_poll',
                                   'never_reached_target',
                                   'no_record_before_target'
                               )),
    rule_version   text        NOT NULL,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT trip_label_uq UNIQUE (trip_id, station_id, rule_version),
    CONSTRAINT trip_label_grade_label_chk CHECK ((grade = 'unconfirmed') = (label IS NULL)),
    CONSTRAINT trip_label_grade_reason_chk CHECK ((grade = 'unconfirmed') = (reason IS NOT NULL)),
    CONSTRAINT trip_label_confirmed_at_chk CHECK (grade = 'unconfirmed' OR confirmed_at IS NOT NULL)
);
COMMENT ON TABLE  public.trip_label IS '정답 라벨. 직전 정류장 출발(stateCd 2) 이후 내 정류장 도착 전 마지막 잔여석이 0이면 1. 운행편 × 목표 정류장 1행. 규칙 해석이 바뀌면 rule_version 을 바꿔 새 행을 넣는다(이전 버전 행은 남긴다). trip 을 다시 만들면 함께 지워진다. 운행편의 trip_key 는 trip 과 조인해 얻는다.';
COMMENT ON COLUMN public.trip_label.station_id IS '목표 정류장(내 정류장) stationId.';
COMMENT ON COLUMN public.trip_label.target_seq IS '목표 정류장의 노선 순번(직전 정류장 = target_seq − 1).';
COMMENT ON COLUMN public.trip_label.grade IS '등급: strict(직전 정류장 출발 기록 있음) | relaxed(출발 기록 없이 이동 중 기록) | unconfirmed(정답 미확인). 정답으로 쓸 등급은 회의에서 정한다.';
COMMENT ON COLUMN public.trip_label.label IS '1 = 도착 상태 0석, 0 = 아님, NULL = 미확인.';
COMMENT ON COLUMN public.trip_label.last_seat IS '라벨 근거 잔여석(미확인이면 참고값 또는 NULL).';
COMMENT ON COLUMN public.trip_label.evidence_at IS '근거 기록의 수집 시각.';
COMMENT ON COLUMN public.trip_label.confirmed_at IS '정답 확정 시각 = 목표 정류장 도착 기록 시각(목표 순번 이상인 첫 위치 기록의 수집 시각, labels.py target_arrival_at). 이 기록을 보는 순간 도착 전 마지막 잔여석이 정해지므로 정답이 확정된다. 사례 검색의 "예보 시점 전에 정답이 확정된 운행만"은 confirmed_at < 예보 시각(issued_at)으로 판정한다. 목표에 닿지 않은 운행은 NULL. (v1 의 arrival_at 과 같은 값이며 이름만 바꿨다.)';
COMMENT ON COLUMN public.trip_label.reason IS '미확인 사유 코드(app/labeling/labels.py UnconfirmedReason). 예약버스 사유는 판정 필드를 찾으면 추가한다.';
COMMENT ON COLUMN public.trip_label.rule_version IS '라벨 규칙 해석 버전 문자열(예: f06-v1).';
CREATE INDEX trip_label_station_grade_idx ON public.trip_label (station_id, grade);

-- -----------------------------------------------------------------------------
-- segment_time: 구간 소요시간(목적지 도착 90백분위 계산용)
-- 범위: 덕현초교(잠실행) → 그 노선의 잠실행 하차 정류장 한 쌍만 둔다.
--   G1300(235000092): 235000392 → 123000611 잠실광역환승센터
--   1306 (235000123): 235000392 → 123000002 잠실역.잠실대교남단(중)
-- 범위를 넓히면(인접 정류장 구간 등) segment_time_scope_chk 를 고친다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.segment_time (
    segment_time_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id          bigint      NOT NULL REFERENCES public.trip (trip_id) ON DELETE CASCADE,
    route_id         text        NOT NULL REFERENCES public.route (route_id),
    service_date     date        NOT NULL,
    time_bin         text        NOT NULL CHECK (time_bin ~ '^([01][0-9]|2[0-3]):[03]0$'),
    from_seq         integer     NOT NULL,
    from_station_id  text        NOT NULL,
    to_seq           integer     NOT NULL,
    to_station_id    text        NOT NULL,
    depart_at        timestamptz NOT NULL,
    arrive_at        timestamptz NOT NULL,
    duration_sec     integer     NOT NULL CHECK (duration_sec > 0),
    is_trial         boolean     NOT NULL,
    is_holiday       boolean     NOT NULL,
    rule_version     text        NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT segment_time_uq UNIQUE (trip_id, from_seq, to_seq, rule_version),
    CONSTRAINT segment_time_seq_chk CHECK (to_seq > from_seq),
    CONSTRAINT segment_time_time_chk CHECK (arrive_at > depart_at),
    CONSTRAINT segment_time_scope_chk CHECK (
        from_station_id = '235000392'
        AND (
            (route_id = '235000092' AND to_station_id = '123000611')
            OR (route_id = '235000123' AND to_station_id = '123000002')
        )
    )
);
COMMENT ON TABLE  public.segment_time IS '운행편별 구간 소요시간. 덕현초교(잠실행) → 그 노선의 하차 정류장 한 쌍만 둔다(segment_time_scope_chk). 같은 노선·평일·30분대·최근 10평일 기록으로 90백분위를 구해 목적지 도착 시각을 낸다(10회 미만이면 도착시각 미제공). trip 을 다시 만들면 함께 지워진다.';
COMMENT ON COLUMN public.segment_time.time_bin IS '출발(from) 시각의 KST 30분대. 예: ''07:30'' 은 07:30 이상 08:00 미만.';
COMMENT ON COLUMN public.segment_time.from_seq IS '출발 정류장 순번(덕현초교: G1300 13, 1306 11).';
COMMENT ON COLUMN public.segment_time.from_station_id IS '출발 정류장. 덕현초교 잠실행 235000392 만 허용한다.';
COMMENT ON COLUMN public.segment_time.to_seq IS '도착 정류장 순번(하차 정류장: G1300 30, 1306 26).';
COMMENT ON COLUMN public.segment_time.to_station_id IS '도착 정류장. G1300 은 123000611(잠실광역환승센터), 1306 은 123000002(잠실역.잠실대교남단(중))만 허용한다.';
COMMENT ON COLUMN public.segment_time.depart_at IS '출발 정류장을 떠난 것으로 보는 시각(판정 방법은 rule_version 이 가리킨다).';
COMMENT ON COLUMN public.segment_time.arrive_at IS '도착 정류장에 닿은 것으로 보는 시각.';
COMMENT ON COLUMN public.segment_time.duration_sec IS 'arrive_at − depart_at(초).';
COMMENT ON COLUMN public.segment_time.rule_version IS '구간 시각 판정 방법 버전 문자열.';
CREATE INDEX segment_time_route_bin_date_idx ON public.segment_time (route_id, time_bin, service_date);

-- -----------------------------------------------------------------------------
-- case_feature: 사례 특징(운행편 × 목표 정류장 × 선행시간)
-- 그 운행이 목표 도착 L분 전이 된 시각(reference_at)의 잔여석·앞차 간격. 사례 검색의 입력이며
-- 실시간 예보와 평가(eval_forecast)가 같이 쓴다.
-- trip FK 를 두지 않는다: 운행 중인 차(실시간)는 아직 trip 행이 없을 수 있고, trip 을 다시 만들어도
-- trip_key 는 같다.
-- 한 rule_version 안에서 (trip_key, station_id, lead_time_min) 마다 1행이다. 기준 시각을 실제 도착과
-- 도착 예상 중 무엇으로 잡을지는 회의 안건이고, 기준을 바꿔 다시 계산하면 rule_version 을 바꾼다.
-- 주의: 같은 rule_version 에서 실시간 행(predicted_arrival)을 넣은 운행을 나중에 실제 도착 기준으로
-- 다시 계산하면 같은 키라 충돌한다. 그때는 ON CONFLICT DO UPDATE 로 덮어쓰고(실시간 당시 값은
-- forecast_snapshot.input_* 에 남는다), 두 기준을 함께 남겨야 하면 UNIQUE 에 reference_basis 를 더한다
-- (회의 안건).
-- -----------------------------------------------------------------------------
CREATE TABLE public.case_feature (
    case_feature_id      bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_key             text          NOT NULL
                                       CHECK (trip_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{1,20}_[0-9]{1,20}_[1-9][0-9]{0,3}$'),
    service_date         date          NOT NULL,
    route_id             text          NOT NULL REFERENCES public.route (route_id),
    station_id           text          NOT NULL REFERENCES public.station (station_id),
    lead_time_min        smallint      NOT NULL CHECK (lead_time_min > 0),
    reference_at         timestamptz,
    reference_basis      text          NOT NULL CHECK (reference_basis IN ('actual_arrival', 'predicted_arrival')),
    seats                integer,
    headway_min          numeric(6, 2) CHECK (headway_min IS NULL OR headway_min >= 0),
    computable           boolean       NOT NULL,
    uncomputable_reason  text          CHECK (uncomputable_reason IN (
                                           'no_target_arrival',
                                           'no_arrival_estimate',
                                           'no_record_near_reference',
                                           'seat_unknown',
                                           'no_preceding_vehicle'
                                       )),
    rule_version         text          NOT NULL,
    computed_at          timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT case_feature_uq UNIQUE (trip_key, station_id, lead_time_min, rule_version),
    CONSTRAINT case_feature_reason_chk CHECK (computable = (uncomputable_reason IS NULL)),
    CONSTRAINT case_feature_values_chk CHECK (
        NOT computable OR (reference_at IS NOT NULL AND seats IS NOT NULL AND headway_min IS NOT NULL)
    )
);
COMMENT ON TABLE  public.case_feature IS '사례 특징. 운행편 × 목표 정류장 × 선행시간마다 기준 시각(목표 도착 L분 전)의 잔여석·앞차 간격을 미리 계산해 둔다. 사례 검색(노선·방향·목표 정류장·선행시간 일치, 허용폭·거리)의 입력이며 실시간 예보와 평가가 같이 쓴다. 한 rule_version 안에서 키마다 1행.';
COMMENT ON COLUMN public.case_feature.trip_key IS '운행편 안정 키(trip.trip_key 와 같은 형식). FK 는 두지 않는다.';
COMMENT ON COLUMN public.case_feature.service_date IS '운행 날짜(trip_key 의 날짜). service_day 와 조인해 평일·정식 수집일만 고른다.';
COMMENT ON COLUMN public.case_feature.route_id IS '노선(사례 검색 필터).';
COMMENT ON COLUMN public.case_feature.station_id IS '목표 정류장(내 정류장). 방향은 정류장 ID 가 가른다.';
COMMENT ON COLUMN public.case_feature.lead_time_min IS '선행시간(분). 허용 값은 settings.FORECAST_RULES.lead_times_min.';
COMMENT ON COLUMN public.case_feature.reference_at IS '기준 시각: 그 운행이 목표 정류장 도착 L분 전이 된 시각. 도착 시각을 정할 수 없으면 NULL.';
COMMENT ON COLUMN public.case_feature.reference_basis IS '기준 시각을 잡은 근거: actual_arrival(과거 기록의 실제 도착 = trip_label.confirmed_at − L분) | predicted_arrival(실시간 도착 예상 = 도착 기록 시각 + predictTimeSec 가 L분이 된 시각). 둘이 어긋날 수 있다(회의 안건).';
COMMENT ON COLUMN public.case_feature.seats IS '기준 시각의 잔여석(기준 시각 이전 가장 가까운 위치 기록). −1 은 넣지 않고 seat_unknown 으로 둔다.';
COMMENT ON COLUMN public.case_feature.headway_min IS '기준 시각의 앞차 간격(분).';
COMMENT ON COLUMN public.case_feature.computable IS '잔여석·앞차 간격을 모두 계산했으면 true. false 면 사례 검색에서 빠지고 그 예보는 missing_input.';
COMMENT ON COLUMN public.case_feature.uncomputable_reason IS '계산 불가 사유(초안 코드, 회의에서 확정): no_target_arrival(목표 도착 기록 없음) | no_arrival_estimate(도착 예상 없음) | no_record_near_reference(기준 시각 근처 위치 기록 없음) | seat_unknown(잔여석 −1·없음) | no_preceding_vehicle(앞차 없음).';
COMMENT ON COLUMN public.case_feature.rule_version IS '사례 특징 계산 규칙(기준 시각·잔여석·간격 판정) 버전 문자열. API rulesVersion(선행시간 선택·사례 검색·대안 규칙)과는 따로 둔다. 선행시간 선택 규칙만 바뀌었을 때 특징을 다시 계산하지 않으려는 것이다.';
CREATE INDEX case_feature_search_idx
    ON public.case_feature (station_id, route_id, lead_time_min, rule_version, seats)
    WHERE computable;

-- -----------------------------------------------------------------------------
-- eval_forecast: 평가용 예보(운행편 × 목표 정류장 × 선행시간)
-- 평가는 모든 운행 × 선행시간마다 계산하므로 화면 요청 단위인 예보 스냅샷(forecast_group·
-- forecast_snapshot)과 단위·생명주기가 다르다. 섞지 않으려고 따로 둔다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.eval_forecast (
    eval_forecast_id     bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_key             text          NOT NULL
                                       CHECK (trip_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{1,20}_[0-9]{1,20}_[1-9][0-9]{0,3}$'),
    service_date         date          NOT NULL,
    route_id             text          NOT NULL REFERENCES public.route (route_id),
    station_id           text          NOT NULL REFERENCES public.station (station_id),
    lead_time_min        smallint      NOT NULL CHECK (lead_time_min > 0),
    issued_at            timestamptz,
    status               text          NOT NULL CHECK (status IN (
                                           'ok',
                                           'not_yet',
                                           'insufficient_cases',
                                           'not_validated',
                                           'stale',
                                           'missing_input',
                                           'outside_hours'
                                       )),
    n                    integer       CHECK (n >= 0),
    k                    integer       CHECK (k >= 0),
    no_seat_probability  numeric(5, 4) CHECK (no_seat_probability BETWEEN 0 AND 1),
    case_trip_keys       text[],
    label                smallint      CHECK (label IN (0, 1)),
    label_grade          text          CHECK (label_grade IN ('strict', 'relaxed', 'unconfirmed')),
    rules_version        text          NOT NULL CHECK (rules_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+$'),
    label_rule_version   text          NOT NULL,
    computed_at          timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT eval_forecast_uq UNIQUE (trip_key, station_id, lead_time_min, rules_version),
    CONSTRAINT eval_forecast_k_le_n_chk CHECK (k IS NULL OR k <= n),
    CONSTRAINT eval_forecast_prob_chk CHECK ((status = 'ok') = (no_seat_probability IS NOT NULL)),
    CONSTRAINT eval_forecast_issued_chk CHECK (
        (status <> 'ok' OR issued_at IS NOT NULL) AND (status <> 'not_yet' OR issued_at IS NULL)
    ),
    CONSTRAINT eval_forecast_cases_chk CHECK (
        case_trip_keys IS NULL OR cardinality(case_trip_keys) = n
    ),
    CONSTRAINT eval_forecast_label_chk CHECK (label IS NULL OR label_grade IN ('strict', 'relaxed'))
);
COMMENT ON TABLE  public.eval_forecast IS '평가용 예보. 모든 운행 × 목표 정류장 × 선행시간마다, 그 운행이 목표 도착 L분 전이었던 시각에 예보를 냈다면 어땠을지 계산하고 정답과 나란히 둔다. 공개 판정(publish_decision) 지표의 입력. 화면 요청 단위인 forecast_group·forecast_snapshot 과 섞지 않는다. 열 이름은 API Forecast 필드(issuedAt, noSeatProbability, n, k)와 같은 뜻의 snake_case 다.';
COMMENT ON COLUMN public.eval_forecast.trip_key IS '평가 대상 운행편(trip.trip_key 형식). FK 는 두지 않는다.';
COMMENT ON COLUMN public.eval_forecast.service_date IS '운행 날짜(trip_key 의 날짜). 평가 기간을 자를 때 쓴다.';
COMMENT ON COLUMN public.eval_forecast.issued_at IS '예보 시각(API issuedAt) = 같은 운행편·정류장·선행시간의 case_feature.reference_at. 사례는 trip_label.confirmed_at < issued_at 인 운행만 쓴다. 기준 시각이 없으면 NULL.';
COMMENT ON COLUMN public.eval_forecast.status IS '예보 상태(API ForecastStatus 7종과 같은 CHECK): ok | not_yet | insufficient_cases(사례 20건 미만) | not_validated | stale | missing_input(사례 특징 계산 불가) | outside_hours(도착이 예보 대상 시간대 밖). 평가에서는 주로 ok·insufficient_cases·missing_input·outside_hours 가 나온다.';
COMMENT ON COLUMN public.eval_forecast.n IS '사용한 유사 사례 수(API n).';
COMMENT ON COLUMN public.eval_forecast.k IS '그중 도착 상태 0석 사례 수(API k).';
COMMENT ON COLUMN public.eval_forecast.no_seat_probability IS 'k ÷ n(API noSeatProbability). 보정·평활 없음. ok 일 때만 값이 있다.';
COMMENT ON COLUMN public.eval_forecast.case_trip_keys IS '사용한 사례 운행편의 trip_key 배열(가까운 순). 개수는 n 과 같다.';
COMMENT ON COLUMN public.eval_forecast.label IS '이 운행의 정답(trip_label.label). 1 = 도착 상태 0석. 미확인이면 NULL.';
COMMENT ON COLUMN public.eval_forecast.label_grade IS '정답 등급(trip_label.grade). 지표에 쓸 등급은 회의에서 정한다.';
COMMENT ON COLUMN public.eval_forecast.rules_version IS '예보 규칙 버전(API rulesVersion, 형식 YYYY-MM-DD.n). forecast_group.rules_version 과 같은 값 체계.';
COMMENT ON COLUMN public.eval_forecast.label_rule_version IS '정답과 사례 라벨에 쓴 trip_label.rule_version. 정답 행은 (trip_key → trip, station_id, label_rule_version)으로 찾는다.';
CREATE INDEX eval_forecast_metric_idx ON public.eval_forecast (rules_version, station_id, route_id, lead_time_min, service_date);

-- -----------------------------------------------------------------------------
-- forecast_group: 예보 묶음(한 화면 요청 = 1행, F07)
-- forecast_group_id 를 API 응답의 snapshotId 로 그대로 쓴다. 조회 조건·서비스 상태·대안 결과는
-- 묶음에 두고, 버스 × 선행시간 결과는 자식 forecast_snapshot 에 둔다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.forecast_group (
    forecast_group_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    computed_at               timestamptz NOT NULL,
    next_refresh_at           timestamptz NOT NULL,
    rules_version             text        NOT NULL CHECK (rules_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+$'),
    label_rule_version        text,
    station_id                text        NOT NULL REFERENCES public.station (station_id),
    destination_id            text        NOT NULL CHECK (destination_id ~ '^[a-z_]{1,32}$'),
    deadline_at               timestamptz,
    service_state             text        NOT NULL CHECK (service_state IN (
                                              'in_service',
                                              'outside_collection',
                                              'outside_forecast_hours'
                                          )),
    next_forecast_start_at    timestamptz,
    stale                     boolean     NOT NULL,
    data_updated_at           timestamptz,
    alternative_status        text        NOT NULL CHECK (alternative_status IN (
                                              'recommended',
                                              'no_alternative',
                                              'arrival_unavailable',
                                              'undecidable'
                                          )),
    recommended_route_id      text,
    recommended_veh_id        text,
    recommended_source        text        CHECK (recommended_source IN ('arrival_1st', 'arrival_2nd', 'timetable_next')),
    recommended_reason_code   text        CHECK (recommended_reason_code IN ('lowest_risk', 'earliest_among_equal_risk')),
    switch_suggested          boolean     NOT NULL,
    candidates                jsonb       NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(candidates) = 'array'),
    CONSTRAINT forecast_group_recommended_chk CHECK (
        (alternative_status = 'recommended')
        = (recommended_route_id IS NOT NULL AND recommended_reason_code IS NOT NULL)
    ),
    CONSTRAINT forecast_group_service_chk CHECK (
        (service_state = 'in_service') = (next_forecast_start_at IS NULL)
    ),
    CONSTRAINT forecast_group_refresh_chk CHECK (next_refresh_at >= computed_at)
);
COMMENT ON TABLE  public.forecast_group IS '예보 묶음. 한 화면 요청(/api/v1/snapshot)이 한 행이다. forecast_group_id = API snapshotId. 조회 조건·서비스 상태·대안 결과를 둔다. 버스 × 선행시간 결과는 forecast_snapshot(자식). LLM 설명은 이 묶음과 자식 행의 값만 읽는다. 열 이름은 API 응답 필드와 같은 뜻의 snake_case 다(vehicleId 만 DB 공통 용어 veh_id).';
COMMENT ON COLUMN public.forecast_group.forecast_group_id IS '묶음 ID(uuid) = API snapshotId. 백엔드가 요청마다 만든다(넣지 않으면 gen_random_uuid()).';
COMMENT ON COLUMN public.forecast_group.computed_at IS '계산 시점(API computedAt). 화면·설명에 보이는 스냅샷 시점과 같아야 한다.';
COMMENT ON COLUMN public.forecast_group.next_refresh_at IS '다음 조회 권장 시각(API nextRefreshAt). 서비스 시간 밖이면 다음 예보 시작 시각.';
COMMENT ON COLUMN public.forecast_group.rules_version IS '계산에 쓴 규칙 버전(API rulesVersion, 형식 YYYY-MM-DD.n: 선행시간 선택·사례 검색·대안 규칙). eval_forecast·publish_decision 의 rules_version 과 같은 값 체계.';
COMMENT ON COLUMN public.forecast_group.next_forecast_start_at IS '다음 예보 시작 시각(API service.nextForecastStartAt). in_service 이면 NULL. 다음 평일 05:45, 설정 공휴일은 건너뛴다.';
COMMENT ON COLUMN public.forecast_group.label_rule_version IS '사례 라벨에 쓴 trip_label.rule_version.';
COMMENT ON COLUMN public.forecast_group.station_id IS '조회 조건: 출발(내) 정류장.';
COMMENT ON COLUMN public.forecast_group.destination_id IS '조회 조건: 목적지 코드(예: jamsil. 형식 ^[a-z_]{1,32}$).';
COMMENT ON COLUMN public.forecast_group.deadline_at IS '조회 조건: 도착 마감 시각(그날 KST 로 바꾼 값). 없으면 NULL.';
COMMENT ON COLUMN public.forecast_group.service_state IS '서비스 상태(API service.state): in_service | outside_collection(수집 시간 밖, 설정 공휴일 포함) | outside_forecast_hours(수집 시간 안이지만 예보할 버스가 없음).';
COMMENT ON COLUMN public.forecast_group.stale IS '수집 데이터가 오래됨(API stale).';
COMMENT ON COLUMN public.forecast_group.data_updated_at IS '계산에 쓴 수집 데이터의 마지막 갱신 시각(API dataUpdatedAt).';
COMMENT ON COLUMN public.forecast_group.alternative_status IS '대안 상태(API alternatives.status): recommended | no_alternative(마감 안 후보 없음) | arrival_unavailable(도착시각 미제공) | undecidable(판단 불가. 서비스 시간 밖도 이 값).';
COMMENT ON COLUMN public.forecast_group.recommended_route_id IS '추천 후보의 노선(API alternatives.recommended.routeId). recommended 일 때만.';
COMMENT ON COLUMN public.forecast_group.recommended_veh_id IS '추천 후보의 차량(API recommended.vehicleId). 시간표상 다음 차면 NULL.';
COMMENT ON COLUMN public.forecast_group.recommended_source IS '추천 후보의 출처(arrival_1st | arrival_2nd | timetable_next). API 에는 없지만 차량이 없는 후보를 forecast_snapshot 행과 맞추려고 둔다.';
COMMENT ON COLUMN public.forecast_group.recommended_reason_code IS '추천 이유(API recommended.reasonCode): lowest_risk | earliest_among_equal_risk.';
COMMENT ON COLUMN public.forecast_group.switch_suggested IS 'API alternatives.switchSuggested. 도착 1순위 버스가 위험 높음이고 더 낮은 위험의 추천 차가 따로 있으면 true.';
COMMENT ON COLUMN public.forecast_group.candidates IS '대안 후보 배열(API alternatives.candidates 그대로: routeId, routeName, vehicleId, source, stationArrivalAt, destinationArrivalAt, meetsDeadline, leadTimeMin, noSeatProbability, riskLevel). leadTimeMin 은 그 버스의 selectedLeadTimeMin.';
CREATE INDEX forecast_group_computed_idx ON public.forecast_group (computed_at);
CREATE INDEX forecast_group_station_computed_idx ON public.forecast_group (station_id, computed_at);

-- -----------------------------------------------------------------------------
-- forecast_snapshot: 묶음의 자식 행(버스 × 선행시간). 선행시간 3개를 모두 저장한다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.forecast_snapshot (
    forecast_snapshot_id     bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    forecast_group_id        uuid          NOT NULL REFERENCES public.forecast_group (forecast_group_id) ON DELETE CASCADE,
    route_id                 text          NOT NULL REFERENCES public.route (route_id),
    veh_id                   text,
    plate_no                 text,
    source                   text          NOT NULL CHECK (source IN ('arrival_1st', 'arrival_2nd', 'timetable_next')),
    station_arrival_at       timestamptz,
    arrival_estimate_source  text          CHECK (arrival_estimate_source IN ('predict_time_sec', 'predict_time_min', 'timetable')),
    in_forecast_hours        boolean       NOT NULL,
    current_seats            integer,
    seats_updated_at         timestamptz,
    lead_time_min            smallint      NOT NULL CHECK (lead_time_min > 0),
    status                   text          NOT NULL CHECK (status IN (
                                               'ok',
                                               'not_yet',
                                               'insufficient_cases',
                                               'not_validated',
                                               'stale',
                                               'missing_input',
                                               'outside_hours'
                                           )),
    issued_at                timestamptz,
    no_seat_probability      numeric(5, 4) CHECK (no_seat_probability BETWEEN 0 AND 1),
    n                        integer       CHECK (n >= 0),
    k                        integer       CHECK (k >= 0),
    preliminary              boolean       NOT NULL,
    input_seats              integer,
    input_headway_min        numeric(6, 2),
    is_selected              boolean       NOT NULL DEFAULT false,
    case_trip_keys           text[],
    CONSTRAINT forecast_snapshot_uq UNIQUE (forecast_group_id, route_id, source, lead_time_min),
    CONSTRAINT forecast_snapshot_k_le_n_chk CHECK (k IS NULL OR k <= n),
    CONSTRAINT forecast_snapshot_prob_chk CHECK ((status = 'ok') = (no_seat_probability IS NOT NULL)),
    CONSTRAINT forecast_snapshot_issued_chk CHECK (
        (status <> 'ok' OR issued_at IS NOT NULL) AND (status <> 'not_yet' OR issued_at IS NULL)
    ),
    CONSTRAINT forecast_snapshot_hours_chk CHECK (status <> 'outside_hours' OR NOT in_forecast_hours),
    CONSTRAINT forecast_snapshot_cases_chk CHECK (
        case_trip_keys IS NULL OR cardinality(case_trip_keys) = n
    )
);
COMMENT ON TABLE  public.forecast_snapshot IS '예보 스냅샷 행. 묶음(forecast_group) 안의 버스 × 선행시간 1행(API buses[].forecasts[]). 선행시간 3개를 모두 저장하고 화면용 1개를 is_selected 로 표시한다(API selectedLeadTimeMin = is_selected 인 행의 lead_time_min, 없으면 null). 버스 단위 값(source, station_arrival_at 등)은 같은 버스의 3행에 같은 값으로 넣는다. 열 이름은 API 필드와 같은 뜻의 snake_case 다(vehicleId 만 DB 공통 용어 veh_id).';
COMMENT ON COLUMN public.forecast_snapshot.forecast_group_id IS '묶음(= API snapshotId). 묶음을 지우면 함께 지워진다.';
COMMENT ON COLUMN public.forecast_snapshot.veh_id IS '차량(API vehicleId, GBIS vehId). 시간표상 다음 차는 NULL.';
COMMENT ON COLUMN public.forecast_snapshot.source IS '후보 출처(API Bus.source): arrival_1st(도착 API 첫 번째 차) | arrival_2nd(두 번째 차) | timetable_next(시간표상 다음 차).';
COMMENT ON COLUMN public.forecast_snapshot.station_arrival_at IS '내 정류장 도착 예상(API stationArrivalAt).';
COMMENT ON COLUMN public.forecast_snapshot.arrival_estimate_source IS '도착 예상의 근거(API arrivalEstimateSource): predict_time_sec(GBIS 초) | predict_time_min(초 값이 없어 분×60 으로 대신함, 임시 승인. 평가에서 따로 집계) | timetable(시간표상 다음 차). 도착 예상이 없으면 NULL.';
COMMENT ON COLUMN public.forecast_snapshot.in_forecast_hours IS '도착 예상이 예보 대상 시간대(06:00~08:59) 안이면 true(API inForecastHours). false 면 status 는 outside_hours 일 수 있다.';
COMMENT ON COLUMN public.forecast_snapshot.current_seats IS '지금 잔여석(API currentSeats). −1·없음이면 NULL.';
COMMENT ON COLUMN public.forecast_snapshot.seats_updated_at IS '현재 잔여석을 읽은 수집 기록 시각(API seatsUpdatedAt).';
COMMENT ON COLUMN public.forecast_snapshot.lead_time_min IS '선행시간(분). 허용 값은 settings.FORECAST_RULES.lead_times_min. 버스마다 3행.';
COMMENT ON COLUMN public.forecast_snapshot.status IS '예보 상태(API ForecastStatus v2): ok | not_yet(아직 도착 L분 전이 아님) | insufficient_cases | not_validated(공개 판정 failed) | stale | missing_input | outside_hours.';
COMMENT ON COLUMN public.forecast_snapshot.issued_at IS '예보를 낸 시각 = 버스가 도착 L분 전이 된 시점(API issuedAt). not_yet 이면 NULL.';
COMMENT ON COLUMN public.forecast_snapshot.no_seat_probability IS 'k ÷ n(API noSeatProbability). 보정·평활 없음. ok 일 때만 값이 있다.';
COMMENT ON COLUMN public.forecast_snapshot.n IS '사용한 유사 사례 수(API n).';
COMMENT ON COLUMN public.forecast_snapshot.k IS '그중 도착 상태 0석 사례 수(API k).';
COMMENT ON COLUMN public.forecast_snapshot.preliminary IS 'API preliminary. 그 노선·정류장·선행시간의 publish_decision 최신 판정이 없거나 pending 이면 true, passed 면 false. failed 면 status 가 not_validated 이다.';
COMMENT ON COLUMN public.forecast_snapshot.input_seats IS '사례 검색 입력: issued_at 시점 잔여석(API inputs.seats).';
COMMENT ON COLUMN public.forecast_snapshot.input_headway_min IS '사례 검색 입력: issued_at 시점 앞차 간격(분, API inputs.headwayMin).';
COMMENT ON COLUMN public.forecast_snapshot.is_selected IS '이 행의 lead_time_min 이 그 버스의 화면용 선행시간(API selectedLeadTimeMin)이면 true. 버스마다 최대 1행(forecast_snapshot_selected_uq). 선택된 행이 없으면 selectedLeadTimeMin 은 null.';
COMMENT ON COLUMN public.forecast_snapshot.case_trip_keys IS '사용한 사례 운행편의 trip_key 배열(가까운 순). 개수는 n 과 같다. 라벨은 (trip_key, station_id, forecast_group.label_rule_version)으로 찾는다.';
CREATE UNIQUE INDEX forecast_snapshot_selected_uq
    ON public.forecast_snapshot (forecast_group_id, route_id, source)
    WHERE is_selected;

-- -----------------------------------------------------------------------------
-- publish_decision: 공개 판정(노선 × 정류장 × 선행시간). 이력을 남긴다(갱신하지 않고 새 행).
-- API 연결 규칙(그 버스의 노선·정류장·선행시간과 현재 rules_version 이 같은 최신 판정 1건을 본다):
--   행 없음 또는 pending → status ok + preliminary=true
--   passed               → status ok + preliminary=false
--   failed               → status not_validated (확률 없음)
-- 최신 판정 = 같은 (route_id, station_id, lead_time_min, rules_version) 중 decided_at 이 가장 늦은 행
--            (같으면 publish_decision_id 가 큰 행).
-- 판정은 노선별이다. 같은 화면에서 G1300 은 passed, 1306 은 pending 처럼 갈릴 수 있다
-- (대안 비교에서 1306 예비 확률을 어떻게 보일지는 회의 안건).
-- -----------------------------------------------------------------------------
CREATE TABLE public.publish_decision (
    publish_decision_id   bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    route_id              text          NOT NULL REFERENCES public.route (route_id),
    station_id            text          NOT NULL REFERENCES public.station (station_id),
    lead_time_min         smallint      NOT NULL CHECK (lead_time_min > 0),
    decision_status       text          NOT NULL CHECK (decision_status IN ('pending', 'passed', 'failed')),
    decided_at            timestamptz   NOT NULL DEFAULT now(),
    field_match_rate      numeric(5, 4) CHECK (field_match_rate BETWEEN 0 AND 1),
    calibration_error     numeric(5, 4) CHECK (calibration_error BETWEEN 0 AND 1),
    alert_recall          numeric(5, 4) CHECK (alert_recall BETWEEN 0 AND 1),
    alert_precision       numeric(5, 4) CHECK (alert_precision BETWEEN 0 AND 1),
    brier                 numeric(6, 5) CHECK (brier BETWEEN 0 AND 1),
    brier_baseline        numeric(6, 5) CHECK (brier_baseline BETWEEN 0 AND 1),
    sample_count          integer       CHECK (sample_count >= 0),
    field_check_count     integer       CHECK (field_check_count >= 0),
    eval_period_start     date,
    eval_period_end       date,
    rules_version         text          NOT NULL CHECK (rules_version ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+$'),
    CONSTRAINT publish_decision_period_chk CHECK (eval_period_end IS NULL OR eval_period_end >= eval_period_start),
    CONSTRAINT publish_decision_decided_chk CHECK (
        decision_status = 'pending'
        OR (sample_count IS NOT NULL AND eval_period_start IS NOT NULL AND eval_period_end IS NOT NULL)
    ),
    CONSTRAINT publish_decision_passed_chk CHECK (
        decision_status <> 'passed'
        OR (
            field_match_rate IS NOT NULL AND calibration_error IS NOT NULL
            AND alert_recall IS NOT NULL AND alert_precision IS NOT NULL
            AND brier IS NOT NULL AND brier_baseline IS NOT NULL
        )
    )
);
COMMENT ON TABLE  public.publish_decision IS '공개 판정 이력. 노선 × 정류장 × 선행시간별로 공개 기준(현장 대조 90%, 보정 오차 10%p, 경보 재현율·정밀도 80%, Brier < 기준선) 판정 결과를 쌓는다(갱신하지 않고 새 행). API 는 같은 노선·정류장·선행시간·현재 rules_version 의 최신 판정 1건만 본다: 행 없음·pending → ok + preliminary=true, passed → ok + preliminary=false, failed → not_validated. 지표 열 이름은 리포트 API 의 fieldMatchRate·calibrationError·alertRecall·alertPrecision·brier·brierBaseline 과 같은 뜻이다.';
COMMENT ON COLUMN public.publish_decision.decision_status IS '판정 상태: pending(판정 전) | passed(통과) | failed(미통과).';
COMMENT ON COLUMN public.publish_decision.decided_at IS '판정 시각(pending 이면 등록 시각). 최신 판정을 고르는 기준.';
COMMENT ON COLUMN public.publish_decision.field_match_rate IS '현장 대조 일치율(0–1). field_check 와 정답 라벨 비교.';
COMMENT ON COLUMN public.publish_decision.calibration_error IS '보정 오차(0–1, 10%p = 0.10).';
COMMENT ON COLUMN public.publish_decision.alert_recall IS '경보(0석 확률 70% 이상) 재현율(0–1).';
COMMENT ON COLUMN public.publish_decision.alert_precision IS '경보 정밀도(0–1).';
COMMENT ON COLUMN public.publish_decision.brier IS 'Brier 점수(eval_forecast 의 ok 행 기준, API brier).';
COMMENT ON COLUMN public.publish_decision.brier_baseline IS '어림셈 기준선의 Brier 점수(API brierBaseline). brier 가 이보다 낮아야 한다.';
COMMENT ON COLUMN public.publish_decision.sample_count IS '평가 표본 수(eval_forecast 중 정답이 있는 ok 행 수).';
COMMENT ON COLUMN public.publish_decision.field_check_count IS '현장 대조 표본 수.';
COMMENT ON COLUMN public.publish_decision.eval_period_start IS '평가 기간 시작일(eval_forecast.service_date 기준).';
COMMENT ON COLUMN public.publish_decision.eval_period_end IS '평가 기간 종료일(포함).';
COMMENT ON COLUMN public.publish_decision.rules_version IS '판정한 예보 규칙 버전(API rulesVersion 과 같은 값 체계, 형식 YYYY-MM-DD.n). 규칙 버전이 바뀌면 이전 판정은 쓰지 않는다.';
CREATE INDEX publish_decision_latest_idx
    ON public.publish_decision (route_id, station_id, lead_time_min, rules_version, decided_at DESC, publish_decision_id DESC);

-- -----------------------------------------------------------------------------
-- notice: 운행 공지(F09). LLM 이 구조화한 뒤 승인 대기로만 저장한다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.notice (
    notice_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_url       text        NOT NULL CHECK (source_url ~ '^https://' AND char_length(source_url) <= 2048),
    source_name      text        NOT NULL,
    title            text,
    published_at     timestamptz,
    fetched_at       timestamptz NOT NULL DEFAULT now(),
    structured       jsonb,
    approval_status  text        NOT NULL DEFAULT 'pending'
                                 CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    reviewed_by      text        CHECK (reviewed_by ~ '^[A-Za-z0-9_-]{1,20}$'),
    reviewed_at      timestamptz,
    CONSTRAINT notice_review_chk CHECK (
        (approval_status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (approval_status <> 'pending' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);
COMMENT ON TABLE  public.notice IS '운행 공지. 원문은 링크만 두고, LLM 구조화 결과는 승인(approved) 뒤에만 계산에 반영한다.';
COMMENT ON COLUMN public.notice.source_url IS '공지 원문 링크.';
COMMENT ON COLUMN public.notice.source_name IS '출처(예: 양주시, 운수사 이름).';
COMMENT ON COLUMN public.notice.structured IS 'LLM 이 구조화한 결과(노선·기간·변경 내용 등).';
COMMENT ON COLUMN public.notice.approval_status IS '승인 상태: pending(승인 대기) | approved(승인) | rejected(반려).';
COMMENT ON COLUMN public.notice.reviewed_by IS '승인·반려한 팀원 코드(이름 대신 팀 내 코드).';
COMMENT ON COLUMN public.notice.reviewed_at IS '승인·반려 시각.';
CREATE INDEX notice_status_idx ON public.notice (approval_status, fetched_at);

-- -----------------------------------------------------------------------------
-- llm_log: LLM 호출 기록(F08). 개인정보·질문 원문·질문 길이는 저장하지 않는다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.llm_log (
    llm_log_id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at         timestamptz NOT NULL DEFAULT now(),
    kind               text        NOT NULL CHECK (kind IN ('parse_query', 'explanation', 'notice')),
    forecast_group_id  uuid        REFERENCES public.forecast_group (forecast_group_id) ON DELETE SET NULL,
    notice_id          bigint      REFERENCES public.notice (notice_id) ON DELETE SET NULL,
    model              text,
    latency_ms         integer     CHECK (latency_ms >= 0),
    checks             jsonb       CHECK (checks IS NULL OR jsonb_typeof(checks) = 'object'),
    is_fallback        boolean     NOT NULL,
    fallback_reason    text        CHECK (fallback_reason IN ('check_failed', 'timeout', 'error', 'daily_limit')),
    error_code         text        CHECK (error_code ~ '^[a-z0-9_]{1,64}$'),
    query_hmac         text        CHECK (query_hmac ~ '^[0-9a-f]{64}$'),
    CONSTRAINT llm_log_fallback_chk CHECK (is_fallback = (fallback_reason IS NOT NULL))
);
COMMENT ON TABLE  public.llm_log IS 'LLM 호출 기록. 검사 결과·지연·고정 문구 대체 여부를 남긴다. 위치·이름·전화번호와 질문 원문·질문 길이는 저장하지 않는다(질문은 HMAC 만).';
COMMENT ON COLUMN public.llm_log.kind IS '종류: parse_query(조건 변환) | explanation(결과 설명) | notice(공지 구조화).';
COMMENT ON COLUMN public.llm_log.forecast_group_id IS '설명 대상 예보 묶음(= API snapshotId). 묶음을 보관 정리로 지우면 NULL.';
COMMENT ON COLUMN public.llm_log.notice_id IS '공지 구조화일 때 대상 공지.';
COMMENT ON COLUMN public.llm_log.latency_ms IS '응답 지연(ms). 3초를 넘으면 고정 문구로 바꾼다.';
COMMENT ON COLUMN public.llm_log.checks IS '출력 검사 5종 결과. 예: {"json_format":true,"values_match":true,"snapshot_time_match":true,"no_assertive_words":true,"max_three_sentences":true}.';
COMMENT ON COLUMN public.llm_log.is_fallback IS '고정 문구로 대체했으면 true.';
COMMENT ON COLUMN public.llm_log.fallback_reason IS '대체 사유: check_failed(검사 실패) | timeout(3초 초과) | error(장애) | daily_limit(하루 상한 초과).';
COMMENT ON COLUMN public.llm_log.error_code IS '오류 코드(고정 코드형, 소문자·숫자·_ 64자 이하. 예: provider_timeout). 오류 메시지 원문·비밀값·질문을 넣지 않는다.';
COMMENT ON COLUMN public.llm_log.query_hmac IS 'parse_query 질문의 HMAC-SHA256(소문자 16진 64자). 같은 질문 반복을 세는 용도. 키는 backend/.env 의 별도 값(예: QUERY_HMAC_KEY)이며 DB·로그에 남기지 않는다. 키 없이 사전 대입으로 질문을 되찾지 못하게 하려는 것이다.';
CREATE INDEX llm_log_created_idx ON public.llm_log (created_at);
CREATE INDEX llm_log_forecast_group_idx ON public.llm_log (forecast_group_id) WHERE forecast_group_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- field_check: 현장 대조 관측(공개 기준 '현장 대조 일치율 90% 이상' 근거)
-- -----------------------------------------------------------------------------
CREATE TABLE public.field_check (
    field_check_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    observed_at        timestamptz NOT NULL,
    station_id         text        NOT NULL REFERENCES public.station (station_id),
    route_id           text        NOT NULL REFERENCES public.route (route_id),
    plate_no           text        CHECK (char_length(plate_no) <= 20),
    veh_id             text,
    is_full           boolean,
    observed_seat_cnt  integer     CHECK (observed_seat_cnt >= 0),
    observer_code      text        NOT NULL CHECK (observer_code ~ '^[A-Za-z0-9_-]{1,20}$'),
    memo               text        CHECK (char_length(memo) <= 500),
    trip_key           text        CHECK (trip_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{1,20}_[0-9]{1,20}_[1-9][0-9]{0,3}$'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT field_check_vehicle_chk CHECK (plate_no IS NOT NULL OR veh_id IS NOT NULL),
    CONSTRAINT field_check_result_chk CHECK (is_full IS NOT NULL OR observed_seat_cnt IS NOT NULL)
);
COMMENT ON TABLE  public.field_check IS '현장 대조. 팀원이 정류장에서 본 만석 여부·잔여석을 라벨과 비교한다.';
COMMENT ON COLUMN public.field_check.observed_at IS '관측 시각(차량이 정류장에 도착한 때).';
COMMENT ON COLUMN public.field_check.plate_no IS '차량 번호판(현장에서 확인). veh_id 와 둘 중 하나는 있어야 한다.';
COMMENT ON COLUMN public.field_check.is_full IS '관측 결과: 만석(좌석 없음)이면 true.';
COMMENT ON COLUMN public.field_check.observed_seat_cnt IS '관측 결과: 차내 표시 등으로 본 잔여석. 못 봤으면 NULL.';
COMMENT ON COLUMN public.field_check.observer_code IS '관측자 표시. 이름 대신 팀 내 코드(영문·숫자·_- 20자 이하).';
COMMENT ON COLUMN public.field_check.memo IS '메모(500자 이하). 개인정보 금지: 승객·기사 등 개인을 알아볼 수 있는 내용(이름, 외모, 연락처, 사진 링크)을 적지 않는다.';
COMMENT ON COLUMN public.field_check.trip_key IS '대조한 운행편의 안정 키(trip.trip_key 형식, 나중에 연결). FK 를 두지 않아 trip 을 다시 만들어도 연결이 남는다.';
CREATE INDEX field_check_station_time_idx ON public.field_check (station_id, observed_at);

-- -----------------------------------------------------------------------------
-- RLS: 켜기만 하고 정책은 만들지 않는다(이유는 파일 맨 위 '보안(RLS·권한)').
-- Supabase Data API(anon·authenticated 키)로는 모든 테이블이 보이지 않게 된다.
-- 백엔드(직접 Postgres 접속, 테이블 소유자)는 영향을 받지 않는다.
-- -----------------------------------------------------------------------------
ALTER TABLE public.route             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.station           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.route_station     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.service_day       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.raw_poll          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bus_position      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bus_arrival       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trip              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trip_label        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.segment_time      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.case_feature      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.eval_forecast     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.forecast_group    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.forecast_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.publish_decision  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notice            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.llm_log           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.field_check       ENABLE ROW LEVEL SECURITY;

-- -----------------------------------------------------------------------------
-- 권한 회수: 정책 없는 RLS 만으로는 TRUNCATE·뷰·SECURITY DEFINER 를 막지 못하므로
-- 공개 역할(anon·authenticated)의 권한 자체를 뺀다.
-- - ALL TABLES/SEQUENCES IN SCHEMA public: public 의 기존 객체 전부가 대상이다(새 프로젝트 기준).
-- - ALTER DEFAULT PRIVILEGES: 이 파일을 실행하는 역할(SQL Editor 에서는 postgres)이 앞으로 만드는
--   객체에만 적용된다. 다른 역할이 만든 객체는 따로 확인한다.
-- - 뷰를 만들 때는 WITH (security_invoker = true) 를 규칙으로 한다.
-- -----------------------------------------------------------------------------
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON FUNCTIONS FROM anon, authenticated;
-- 함수 EXECUTE 는 PostgreSQL 기본값으로 PUBLIC 에도 주어지므로 그것도 뺀다.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

COMMIT;
