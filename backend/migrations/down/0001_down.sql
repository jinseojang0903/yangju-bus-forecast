-- 경고: 운영 DB 에 실행하지 말 것. 실행 전에 대상 DB(Supabase 프로젝트·접속 주소)를 반드시 확인한다.
-- 확인했으면 아래 줄의 주석을 풀고 실행한다. 없으면 가드가 오류를 내고 아무것도 지우지 않는다.
-- SET app.confirm_down = 'yes';
-- =============================================================================
-- down/0001_down.sql : 0001_init.sql 되돌리기. 테이블 18개와 그 데이터를 모두 지운다.
-- migrations/*.sql 을 이름순으로 실행하는 도구가 실수로 돌리지 않도록 별도 폴더에 둔다.
-- 참조하는 쪽(FK 를 가진 쪽)부터 지운다. 0001_init.sql 의 ALTER DEFAULT PRIVILEGES(권한 회수)는
-- 되돌리지 않는다(공개 역할에 기본 권한을 다시 주는 것은 따로 결정한다).
-- =============================================================================

BEGIN;

DO $$
BEGIN
    IF coalesce(current_setting('app.confirm_down', true), '') <> 'yes' THEN
        RAISE EXCEPTION '0001_down 중단: 대상 DB 를 확인한 뒤 SET app.confirm_down = ''yes''; 를 먼저 실행한다.';
    END IF;
END
$$;

DROP TABLE IF EXISTS
    public.llm_log,             -- → notice, forecast_group
    public.field_check,         -- → station, route
    public.notice,
    public.forecast_snapshot,   -- → forecast_group, route
    public.forecast_group,      -- → station
    public.publish_decision,    -- → route, station
    public.eval_forecast,       -- → route, station
    public.case_feature,        -- → route, station
    public.segment_time,        -- → trip, route
    public.trip_label,          -- → trip, station
    public.trip,                -- → route
    public.bus_arrival,         -- → raw_poll
    public.bus_position,        -- → raw_poll
    public.raw_poll,
    public.service_day,
    public.route_station,       -- → route, station
    public.station,
    public.route;

COMMIT;
