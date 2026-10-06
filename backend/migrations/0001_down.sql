-- 경고: 운영 DB 에 실행하지 말 것. 실행 전에 대상 DB(Supabase 프로젝트·접속 주소)를 반드시 확인한다.
-- =============================================================================
-- 0001_down.sql : 0001_init.sql 되돌리기. 테이블 12개와 그 데이터를 모두 지운다.
-- 참조하는 쪽부터 지운다. 0001_init.sql 의 ALTER DEFAULT PRIVILEGES(권한 회수)는 되돌리지 않는다
-- (공개 역할에 기본 권한을 다시 주는 것은 따로 결정한다).
-- =============================================================================

BEGIN;

DROP TABLE IF EXISTS
    public.field_check,
    public.llm_log,
    public.notice,
    public.forecast_snapshot,
    public.segment_time,
    public.trip_label,
    public.trip,
    public.bus_position,
    public.raw_poll,
    public.route_station,
    public.station,
    public.route;

COMMIT;
