-- =============================================================================
-- 0001_init.sql : 첫 스키마(회의용 초안, 2026-10-07 회의에서 확정)
-- 대상: Supabase(PostgreSQL 15 이상). anon·authenticated 역할이 있다고 가정한다
--       (일반 PostgreSQL 에서는 아래 REVOKE 가 역할이 없어 실패한다).
--
-- 적용 방법
--   Supabase 대시보드 > SQL Editor 에 이 파일 전체를 붙여 넣고 한 번 실행한다.
--   BEGIN/COMMIT 으로 감쌌으므로 중간에 하나라도 실패하면 아무것도 만들어지지 않는다.
--   같은 이름의 테이블이 이미 있으면 실패한다(IF NOT EXISTS 를 일부러 쓰지 않았다.
--   반쯤 다른 스키마 위에 조용히 덮어쓰지 않게 하려는 것이다).
--
-- 되돌리는 방법
--   backend/migrations/0001_down.sql (데이터가 모두 지워진다. 대상 DB 를 확인하고 실행한다)
--
-- 공통 규칙
--   - 시각은 timestamptz. 계산은 KST(Asia/Seoul)로 하고 저장은 시간대 있는 타입으로 한다.
--   - GBIS ID(routeId·stationId·vehId)는 text. API 가 숫자·문자열을 섞어 보내므로 문자열로 통일한다.
--   - 우리가 정한 상태·등급·사유 값은 enum 대신 text + CHECK. 회의에서 값이 바뀌어도
--     CHECK 하나만 고치면 된다.
--   - GBIS 가 보내는 값(stateCd, remainSeatCnt, crowded 등)에는 NOT NULL·CHECK 를 최소로 둔다.
--     예상 밖 값 때문에 적재가 실패하면 그 시간의 기록을 잃기 때문이다(원본은 JSONL 에 있다).
--   - '바꾸지 않는 규칙'의 숫자(선행시간 5·10·15, 사례 30건·20건 등)는 SQL 에 다시 쓰지 않는다.
--     기준은 backend/app/core/settings.py 다.
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
-- raw_poll: GBIS 응답 원본(JSONL 한 줄 = 한 행)
-- 보관 기간 정리는 (가) raw_poll 행 삭제 또는 (나) body·body_text 를 NULL 로 비우기로 한다.
-- 행을 지우면 bus_position.raw_poll_id 는 NULL 이 되고 위치 기록은 남는다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.raw_poll (
    raw_poll_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_date     date        NOT NULL,
    source_line     integer     NOT NULL CHECK (source_line >= 1),
    collected_at    timestamptz NOT NULL,
    api             text        NOT NULL CHECK (api IN ('getBusLocationListv2', 'getBusArrivalListv2')),
    params          jsonb       NOT NULL CHECK (jsonb_typeof(params) = 'object'),
    route_id        text        GENERATED ALWAYS AS (params ->> 'routeId') STORED,
    station_id      text        GENERATED ALWAYS AS (params ->> 'stationId') STORED,
    http_status     integer,
    elapsed_ms      integer,
    ok              boolean     NOT NULL,
    result_code     text,
    result_message  text,
    error           text,
    is_weekday      boolean     NOT NULL,
    is_holiday      boolean     NOT NULL,
    mode            text        NOT NULL CHECK (mode IN ('run', 'once', 'trial')),
    interval_sec    integer     CHECK (interval_sec IS NULL OR interval_sec > 0),
    body            jsonb,
    body_text       text,
    loaded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT raw_poll_source_uq UNIQUE (source_date, source_line),
    CONSTRAINT raw_poll_body_one_chk CHECK (body IS NULL OR body_text IS NULL),
    -- 서비스 키가 파라미터에 섞여 들어오면 적재를 거부한다(수집기가 이미 빼지만 한 번 더 막는다).
    CONSTRAINT raw_poll_no_service_key_chk CHECK (
        NOT (params ?| ARRAY['serviceKey', 'ServiceKey', 'servicekey', 'SERVICEKEY'])
    )
);
COMMENT ON TABLE  public.raw_poll IS 'GBIS 응답 원본. JSONL(data/collected/<날짜>/raw_poll.jsonl) 한 줄이 한 행이다. 기준은 JSONL 이고 DB 는 그다음에 넣는다. DB 에는 위치 API 의 G1300·1306 과 도착 API(덕현초교)만 적재한다. 나머지 10개 노선은 JSONL 에만 남는다. 보관 기간 정리는 행 삭제 또는 body·body_text 를 NULL 로 비우기.';
COMMENT ON COLUMN public.raw_poll.source_date IS 'JSONL 폴더 날짜(KST). source_line 과 함께 재적재 중복을 막는 키.';
COMMENT ON COLUMN public.raw_poll.source_line IS 'JSONL 파일의 물리적 줄 번호(1부터, 빈 줄·깨진 줄 포함). 파일은 덧붙이기만 하므로 번호가 바뀌지 않는다.';
COMMENT ON COLUMN public.raw_poll.collected_at IS '호출 시각(JSONL collected_at, KST 오프셋 포함).';
COMMENT ON COLUMN public.raw_poll.api IS '오퍼레이션 이름(getBusLocationListv2 | getBusArrivalListv2).';
COMMENT ON COLUMN public.raw_poll.params IS '요청 파라미터(serviceKey 제외, CHECK 로 강제). 예: {"routeId":"235000092","format":"json"}.';
COMMENT ON COLUMN public.raw_poll.route_id IS 'params.routeId 에서 뽑은 값(위치 API). 조회용 생성 열.';
COMMENT ON COLUMN public.raw_poll.station_id IS 'params.stationId 에서 뽑은 값(도착 API). 조회용 생성 열.';
COMMENT ON COLUMN public.raw_poll.ok IS '수집기 판정 성공 여부. 결과 없음(차 없음, 빈 응답)도 성공이다.';
COMMENT ON COLUMN public.raw_poll.error IS '실패 사유(수집기가 비밀값을 가린 문자열).';
COMMENT ON COLUMN public.raw_poll.mode IS '수집 방식: run(정식) | once(1회) | trial(시운전, 사례·평가에서 뺀다).';
COMMENT ON COLUMN public.raw_poll.interval_sec IS '그 대상의 수집 주기(초). once 등 주기 수집이 아니면 NULL.';
COMMENT ON COLUMN public.raw_poll.body IS '응답 본문(JSON 이면 jsonb). 라벨 규칙을 고칠 때 다시 계산하려고 그대로 둔다. 보관 기간이 지나면 NULL 로 비울 수 있다.';
COMMENT ON COLUMN public.raw_poll.body_text IS '본문이 JSON 이 아닐 때(게이트웨이 XML 오류 등)의 원문. body 와 동시에 채우지 않는다.';
CREATE INDEX raw_poll_collected_at_idx ON public.raw_poll (collected_at);
CREATE INDEX raw_poll_route_collected_idx ON public.raw_poll (route_id, collected_at) WHERE route_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- bus_position: 위치 응답의 차량 항목 1개 = 한 행(G1300·1306 만)
-- 적재 실패로 행을 잃지 않게 필수 열은 collected_at, route_id(와 적재 순서 item_index)뿐이다.
-- route FK 도 두지 않는다(기준정보가 늦게 채워져도 적재가 실패하지 않게).
-- -----------------------------------------------------------------------------
CREATE TABLE public.bus_position (
    bus_position_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    raw_poll_id      bigint      REFERENCES public.raw_poll (raw_poll_id) ON DELETE SET NULL,
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
    CONSTRAINT bus_position_item_uq UNIQUE (raw_poll_id, item_index)
);
COMMENT ON TABLE  public.bus_position IS '위치 기록. 위치 응답(busLocationList)의 차량 1대 1회 관측이 한 행이다. 운행편 재구성·라벨·구간 소요시간의 입력. raw_poll 이 보관 기간 정리로 지워져도 남는다.';
COMMENT ON COLUMN public.bus_position.raw_poll_id IS '원본 응답. 원본 행을 지우면 NULL(ON DELETE SET NULL).';
COMMENT ON COLUMN public.bus_position.item_index IS '응답 목록 안 순서(0부터). raw_poll_id 와 함께 재적재 중복을 막는다.';
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
CREATE INDEX bus_position_route_veh_time_idx ON public.bus_position (route_id, veh_id, collected_at);

-- -----------------------------------------------------------------------------
-- trip: 운행편(app/labeling/trips.py 의 Trip)
-- trip 을 다시 만들면(운행편 기준 변경 등) trip_label·segment_time 이 함께 지워진다(CASCADE).
-- 예보 스냅샷의 근거는 trip_key 문자열로 남으므로 추적이 끊기지 않는다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.trip (
    trip_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_key       text        NOT NULL
                               CHECK (trip_key ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[^_]+_[^_]+_[1-9][0-9]*$'),
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
COMMENT ON TABLE  public.trip IS '운행편. 같은 날·노선·차량의 위치 기록을 시각 순으로 묶고, 30분 넘는 공백이나 순번 10 이상 감소(회차 뒤 재출발)에서 나눈다. 다시 만들면 trip_label·segment_time 이 함께 지워지고, 스냅샷 근거는 trip_key 로 남는다.';
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
    arrival_at     timestamptz,
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
    CONSTRAINT trip_label_grade_reason_chk CHECK ((grade = 'unconfirmed') = (reason IS NOT NULL))
);
COMMENT ON TABLE  public.trip_label IS '정답 라벨. 직전 정류장 출발(stateCd 2) 이후 내 정류장 도착 전 마지막 잔여석이 0이면 1. 운행편 × 목표 정류장 1행. 규칙 해석이 바뀌면 rule_version 을 바꿔 새 행을 넣는다(이전 버전 행은 남긴다). trip 을 다시 만들면 함께 지워진다.';
COMMENT ON COLUMN public.trip_label.station_id IS '목표 정류장(내 정류장) stationId.';
COMMENT ON COLUMN public.trip_label.target_seq IS '목표 정류장의 노선 순번(직전 정류장 = target_seq − 1).';
COMMENT ON COLUMN public.trip_label.grade IS '등급: strict(직전 정류장 출발 기록 있음) | relaxed(출발 기록 없이 이동 중 기록) | unconfirmed(정답 미확인). 정답으로 쓸 등급은 회의에서 정한다.';
COMMENT ON COLUMN public.trip_label.label IS '1 = 도착 상태 0석, 0 = 아님, NULL = 미확인.';
COMMENT ON COLUMN public.trip_label.last_seat IS '라벨 근거 잔여석(미확인이면 참고값 또는 NULL).';
COMMENT ON COLUMN public.trip_label.evidence_at IS '근거 기록의 수집 시각.';
COMMENT ON COLUMN public.trip_label.arrival_at IS '목표 순번 이상인 첫 기록의 수집 시각(도착으로 보는 시각).';
COMMENT ON COLUMN public.trip_label.reason IS '미확인 사유 코드(app/labeling/labels.py UnconfirmedReason). 예약버스 사유는 판정 필드를 찾으면 추가한다.';
COMMENT ON COLUMN public.trip_label.rule_version IS '라벨 규칙 해석 버전 문자열(예: f06-v1).';
CREATE INDEX trip_label_station_grade_idx ON public.trip_label (station_id, grade);

-- -----------------------------------------------------------------------------
-- segment_time: 구간 소요시간(목적지 도착 90백분위 계산용)
-- -----------------------------------------------------------------------------
CREATE TABLE public.segment_time (
    segment_time_id  bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    trip_id          bigint      NOT NULL REFERENCES public.trip (trip_id) ON DELETE CASCADE,
    route_id         text        NOT NULL REFERENCES public.route (route_id),
    service_date     date        NOT NULL,
    time_bin         text        NOT NULL CHECK (time_bin ~ '^([01][0-9]|2[0-3]):[03]0$'),
    from_seq         integer     NOT NULL,
    from_station_id  text,
    to_seq           integer     NOT NULL,
    to_station_id    text,
    depart_at        timestamptz NOT NULL,
    arrive_at        timestamptz NOT NULL,
    duration_sec     integer     NOT NULL CHECK (duration_sec > 0),
    is_trial         boolean     NOT NULL,
    is_holiday       boolean     NOT NULL,
    rule_version     text        NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT segment_time_uq UNIQUE (trip_id, from_seq, to_seq, rule_version),
    CONSTRAINT segment_time_seq_chk CHECK (to_seq > from_seq),
    CONSTRAINT segment_time_time_chk CHECK (arrive_at > depart_at)
);
COMMENT ON TABLE  public.segment_time IS '운행편별 구간 소요시간. 같은 노선·평일·30분대·최근 10평일 기록으로 90백분위를 구해 목적지 도착 시각을 낸다(10회 미만이면 도착시각 미제공). trip 을 다시 만들면 함께 지워진다.';
COMMENT ON COLUMN public.segment_time.time_bin IS '출발(from) 시각의 KST 30분대. 예: ''07:30'' 은 07:30 이상 08:00 미만.';
COMMENT ON COLUMN public.segment_time.from_seq IS '출발 정류장 순번(예: 덕현초교).';
COMMENT ON COLUMN public.segment_time.to_seq IS '도착 정류장 순번(예: 하차 정류장).';
COMMENT ON COLUMN public.segment_time.depart_at IS '출발 정류장을 떠난 것으로 보는 시각(판정 방법은 rule_version 이 가리킨다).';
COMMENT ON COLUMN public.segment_time.arrive_at IS '도착 정류장에 닿은 것으로 보는 시각.';
COMMENT ON COLUMN public.segment_time.duration_sec IS 'arrive_at − depart_at(초).';
COMMENT ON COLUMN public.segment_time.rule_version IS '구간 시각 판정 방법 버전 문자열.';
CREATE INDEX segment_time_route_bin_date_idx ON public.segment_time (route_id, time_bin, service_date);

-- -----------------------------------------------------------------------------
-- forecast_snapshot: 예보 1회의 계산 결과(F07)
-- 한 화면 요청 = snapshot_group_id 하나(API 의 snapshotId), 그 안에 노선·차량·선행시간별 행이 여럿.
-- -----------------------------------------------------------------------------
CREATE TABLE public.forecast_snapshot (
    forecast_snapshot_id  bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_group_id     uuid         NOT NULL,
    computed_at           timestamptz  NOT NULL,
    station_id            text         NOT NULL REFERENCES public.station (station_id),
    destination_id        text         CHECK (destination_id ~ '^[a-z_]{1,32}$'),
    deadline_at           timestamptz,
    route_id              text         NOT NULL REFERENCES public.route (route_id),
    candidate_kind        text         CHECK (candidate_kind IN ('first', 'second', 'next_scheduled')),
    veh_id                text,
    plate_no              text,
    lead_time_min         smallint     CHECK (lead_time_min > 0),
    predict_time_sec      integer,
    input_collected_at    timestamptz,
    remain_seat_cnt       integer,
    headway_min           numeric(6, 2),
    case_count            integer      CHECK (case_count >= 0),
    zero_seat_count       integer      CHECK (zero_seat_count >= 0),
    probability           numeric(5, 4) CHECK (probability BETWEEN 0 AND 1),
    status                text         NOT NULL CHECK (status IN (
                                           'ok',
                                           'insufficient_cases',
                                           'not_validated',
                                           'stale',
                                           'missing_input'
                                       )),
    case_trip_keys        text[],
    label_rule_version    text,
    alternative_result    jsonb,
    alternative_status    text         CHECK (alternative_status IN (
                                           'recommended',
                                           'no_alternative',
                                           'arrival_unavailable',
                                           'undecidable'
                                       )),
    is_preliminary        boolean      NOT NULL DEFAULT true,
    rule_version          text         NOT NULL,
    CONSTRAINT forecast_snapshot_k_le_n_chk CHECK (zero_seat_count IS NULL OR zero_seat_count <= case_count),
    CONSTRAINT forecast_snapshot_ok_prob_chk CHECK (status <> 'ok' OR probability IS NOT NULL),
    CONSTRAINT forecast_snapshot_insufficient_chk CHECK (status <> 'insufficient_cases' OR probability IS NULL)
);
COMMENT ON TABLE  public.forecast_snapshot IS '예보 스냅샷. 계산 코드가 만든 확률·n·k·대안을 그대로 남긴다. LLM 설명은 이 값만 읽는다. 한 화면 요청은 snapshot_group_id(= API snapshotId)로 묶는다.';
COMMENT ON COLUMN public.forecast_snapshot.snapshot_group_id IS '한 화면 요청(같은 계산 시점)의 묶음 ID(uuid). API 응답의 snapshotId 로 그대로 쓴다. 백엔드가 요청마다 하나 만든다.';
COMMENT ON COLUMN public.forecast_snapshot.computed_at IS '계산 시점. 화면·설명에 보이는 스냅샷 시점과 같아야 한다.';
COMMENT ON COLUMN public.forecast_snapshot.station_id IS '조회 조건: 출발(내) 정류장.';
COMMENT ON COLUMN public.forecast_snapshot.destination_id IS '조회 조건: 목적지 코드(API 명세 초안 기준, 예: jamsil. 형식 ^[a-z_]{1,32}$).';
COMMENT ON COLUMN public.forecast_snapshot.deadline_at IS '조회 조건: 도착 마감 시각. 없으면 NULL.';
COMMENT ON COLUMN public.forecast_snapshot.candidate_kind IS '후보 종류: first(도착 API 첫 번째 차) | second(두 번째 차) | next_scheduled(시간표상 다음 차, 차량 없음).';
COMMENT ON COLUMN public.forecast_snapshot.veh_id IS '예보 대상 운행편의 차량(vehId). 시간표상 다음 차는 NULL.';
COMMENT ON COLUMN public.forecast_snapshot.lead_time_min IS '선행시간(분). 허용 값은 settings.FORECAST_RULES.lead_times_min. 입력 누락으로 정하지 못하면 NULL.';
COMMENT ON COLUMN public.forecast_snapshot.predict_time_sec IS 'GBIS 도착 예상(predictTimeSec). 내 정류장 도착 = computed_at + 이 값.';
COMMENT ON COLUMN public.forecast_snapshot.input_collected_at IS '입력으로 쓴 수집 기록의 시각. 정보 오래됨(stale) 판정 근거.';
COMMENT ON COLUMN public.forecast_snapshot.remain_seat_cnt IS '현재 잔여석(서버가 수집 데이터에서 계산).';
COMMENT ON COLUMN public.forecast_snapshot.headway_min IS '앞차 간격(분, 서버가 수집 데이터에서 계산).';
COMMENT ON COLUMN public.forecast_snapshot.case_count IS 'n: 사용한 유사 사례 수.';
COMMENT ON COLUMN public.forecast_snapshot.zero_seat_count IS 'k: 그중 도착 상태 0석 사례 수.';
COMMENT ON COLUMN public.forecast_snapshot.probability IS 'k ÷ n. 보정·평활 없음. 사례 부족이면 NULL.';
COMMENT ON COLUMN public.forecast_snapshot.status IS '예보 상태: ok(제공) | insufficient_cases(사례 부족) | not_validated(검증 미통과) | stale(정보 오래됨) | missing_input(입력 누락).';
COMMENT ON COLUMN public.forecast_snapshot.case_trip_keys IS '사용한 사례 운행편의 trip_key 배열(가까운 순). trip_label_id 대신 안정 키를 써서 trip 을 다시 만들어도 근거를 추적할 수 있다. 라벨은 (trip_key, station_id, label_rule_version)으로 찾는다.';
COMMENT ON COLUMN public.forecast_snapshot.label_rule_version IS '사례에 쓴 trip_label.rule_version.';
COMMENT ON COLUMN public.forecast_snapshot.alternative_result IS '대안 비교 결과(후보별 도착 예상·0석 위험·추천 이유). 같은 묶음의 행에는 같은 값을 넣는다.';
COMMENT ON COLUMN public.forecast_snapshot.alternative_status IS '대안 상태: recommended(추천) | no_alternative(마감 안 후보 없음) | arrival_unavailable(도착시각 미제공) | undecidable(정보 부족으로 판단 불가).';
COMMENT ON COLUMN public.forecast_snapshot.is_preliminary IS '예비 표기 여부. 공개 기준 판정 전에 보여 주는 확률은 true.';
COMMENT ON COLUMN public.forecast_snapshot.rule_version IS '예보 계산 코드(사례 검색 규칙) 버전 문자열.';
CREATE INDEX forecast_snapshot_computed_idx ON public.forecast_snapshot (computed_at);
CREATE INDEX forecast_snapshot_station_computed_idx ON public.forecast_snapshot (station_id, computed_at);
CREATE INDEX forecast_snapshot_group_idx ON public.forecast_snapshot (snapshot_group_id);

-- -----------------------------------------------------------------------------
-- notice: 운행 공지(F09). LLM 이 구조화한 뒤 승인 대기로만 저장한다.
-- -----------------------------------------------------------------------------
CREATE TABLE public.notice (
    notice_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_url       text        NOT NULL,
    source_name      text        NOT NULL,
    title            text,
    published_at     timestamptz,
    fetched_at       timestamptz NOT NULL DEFAULT now(),
    structured       jsonb,
    approval_status  text        NOT NULL DEFAULT 'pending'
                                 CHECK (approval_status IN ('pending', 'approved', 'rejected')),
    reviewed_by      text,
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
    snapshot_group_id  uuid,
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
COMMENT ON COLUMN public.llm_log.snapshot_group_id IS '설명 대상 스냅샷 묶음(forecast_snapshot.snapshot_group_id = API snapshotId). 묶음 테이블이 없어 FK 를 두지 않는다.';
COMMENT ON COLUMN public.llm_log.notice_id IS '공지 구조화일 때 대상 공지.';
COMMENT ON COLUMN public.llm_log.latency_ms IS '응답 지연(ms). 3초를 넘으면 고정 문구로 바꾼다.';
COMMENT ON COLUMN public.llm_log.checks IS '출력 검사 5종 결과. 예: {"json_format":true,"values_match":true,"snapshot_time_match":true,"no_assertive_words":true,"max_three_sentences":true}.';
COMMENT ON COLUMN public.llm_log.is_fallback IS '고정 문구로 대체했으면 true.';
COMMENT ON COLUMN public.llm_log.fallback_reason IS '대체 사유: check_failed(검사 실패) | timeout(3초 초과) | error(장애) | daily_limit(하루 상한 초과).';
COMMENT ON COLUMN public.llm_log.error_code IS '오류 코드(고정 코드형, 소문자·숫자·_ 64자 이하. 예: provider_timeout). 오류 메시지 원문·비밀값·질문을 넣지 않는다.';
COMMENT ON COLUMN public.llm_log.query_hmac IS 'parse_query 질문의 HMAC-SHA256(소문자 16진 64자). 같은 질문 반복을 세는 용도. 키는 backend/.env 의 별도 값(예: QUERY_HMAC_KEY)이며 DB·로그에 남기지 않는다. 키 없이 사전 대입으로 질문을 되찾지 못하게 하려는 것이다.';
CREATE INDEX llm_log_created_idx ON public.llm_log (created_at);
CREATE INDEX llm_log_snapshot_group_idx ON public.llm_log (snapshot_group_id) WHERE snapshot_group_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- field_check: 현장 대조 관측(공개 기준 '현장 대조 일치율 90% 이상' 근거)
-- -----------------------------------------------------------------------------
CREATE TABLE public.field_check (
    field_check_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    observed_at        timestamptz NOT NULL,
    station_id         text        NOT NULL REFERENCES public.station (station_id),
    route_id           text        NOT NULL REFERENCES public.route (route_id),
    plate_no           text,
    veh_id             text,
    is_full            boolean,
    observed_seat_cnt  integer     CHECK (observed_seat_cnt >= 0),
    observer_code      text        NOT NULL CHECK (observer_code ~ '^[A-Za-z0-9_-]{1,20}$'),
    memo               text        CHECK (char_length(memo) <= 500),
    trip_id            bigint      REFERENCES public.trip (trip_id) ON DELETE SET NULL,
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
COMMENT ON COLUMN public.field_check.trip_id IS '대조한 운행편(나중에 연결). 운행편을 다시 만들면 NULL 이 된다.';
CREATE INDEX field_check_station_time_idx ON public.field_check (station_id, observed_at);

-- -----------------------------------------------------------------------------
-- RLS: 켜기만 하고 정책은 만들지 않는다(이유는 파일 맨 위 '보안(RLS·권한)').
-- Supabase Data API(anon·authenticated 키)로는 모든 테이블이 보이지 않게 된다.
-- 백엔드(직접 Postgres 접속, 테이블 소유자)는 영향을 받지 않는다.
-- -----------------------------------------------------------------------------
ALTER TABLE public.route             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.station           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.route_station     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.raw_poll          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.bus_position      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trip              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trip_label        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.segment_time      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.forecast_snapshot ENABLE ROW LEVEL SECURITY;
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

COMMIT;
